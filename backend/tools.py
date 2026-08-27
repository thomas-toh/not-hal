"""The tool executor: everything the model is allowed to do on this machine, and nothing else.

The registry `shared/schemas/tools.json` is the single source of truth for tool names, parameter
schemas and tiers; this module never hardcodes a tool definition. Two things it guarantees, both
binding:

- **The model only ever sees a tool this platform actually IMPLEMENTS**: a tool with no backend,
  above the enabled tier, or whose connector the user has switched off is filtered out of the
  list handed to the adapter, so the model cannot even name it. `execute()` re-checks anyway —
  the allowlist is the defence, not the model.
- **Every invocation is audited**: run, refused or errored, one JSONL line lands in
  `logs/audit.jsonl` — the same `logs/` folder a user deletes to purge everything, so there is no
  separate purge action.

A **tier** ranks a tool by what it can do to the machine: 1 = read-only, no gate · 2 = reversible,
announced by an earcon as it returns · 3 = destructive, requiring a propose-then-tap confirmation.
Tier 3's confirmation renders on the overlay and does not exist yet, so `MAX_TIER` holds the
ceiling at 2 and a Tier-3 tool is refused even when the registry defines one.

A **connector** is the second, independent gate, and a different question: a tier asks "may this
be done without asking?" — danger, the designer's judgement — while a connector asks "does this
user want their files, mail or clipboard reached at all?" — consent, theirs. Each tool names one,
the user switches it on or off in Settings, and a tool passes both gates or is not offered.
Turning a connector on can never raise a tier.

    python -m backend.tools --selfcheck     # offline: filtering, dispatch, refusal, audit

"""
from __future__ import annotations

import ctypes
import difflib
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from backend.llm.base import ToolCall
from shared.config import load_schemas
from shared import log as _log
from shared import settings

log = logging.getLogger("nothal.tools")

# One line per tool call, appended, never rewritten. Beside nothal.log so "delete logs/" purges
# both in one action (spec/50 rule 3). Resolved through shared.log so its selfcheck can redirect it.
AUDIT_FILE = _log.LOG_DIR / "audit.jsonl"

# The highest tier `execute()` will run and `tool_specs()` will offer. Raising it is how a tier
# turns on, once its backend AND its gate exist. Tier 2's gate is the announce earcon, now wired
# (orchestrator._run_tool_seen); Tier 3 still needs the propose-then-tap confirmation on the
# Teleprompter (D26), so the ceiling stops here.
# ponytail: a single ceiling, not per-tier flags — split only if a tier needs enabling alone.
MAX_TIER = 2

CLIP_LIMIT = 2000  # matches the read_clipboard registry description


# --- Tier-1 backends: name -> callable(args) -> str. The tier itself comes from the registry. ---


def _read_clipboard(args: dict) -> str:
    from backend.paste import get_clipboard_text

    text = get_clipboard_text()
    if not text:
        return "(the clipboard is empty or holds no text)"
    return text[:CLIP_LIMIT]


def _system_status(args: dict) -> str:
    # Timezone-AWARE local time: astimezone() attaches the OS's current UTC offset, so the model
    # gets an anchor and can convert to any zone from its own knowledge (Tokyo = UTC+9) — no tool
    # and no web call (rung 1). A naive "22:18" gave it nothing to convert FROM, so it refused.
    now = datetime.now().astimezone()
    z = now.strftime("%z")                        # "+0100"; astimezone() always sets an offset
    offset = f"UTC{z[:3]}:{z[3:]}" if z else "UTC"
    parts = [f"Local time: {now.strftime('%H:%M on %A %d %B %Y')} ({offset})"]
    if sys.platform == "win32":
        parts += [p for p in (_win_active_window(), _win_battery()) if p]
    # ponytail: volume level and media playback state (promised by the registry description) need
    # COM (IAudioEndpointVolume) and WinRT (GlobalSystemMediaTransportControls) respectively —
    # real work, deferred. The tool still returns useful status; add the fields when a backend lands.
    return "\n".join(parts)


def _win_active_window() -> str:
    u = ctypes.windll.user32
    hwnd = u.GetForegroundWindow()
    if not hwnd:
        return ""
    length = u.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    u.GetWindowTextW(hwnd, buf, length + 1)
    return f"Active window: {buf.value or '(untitled)'}"


class _PowerStatus(ctypes.Structure):
    # Win32 SYSTEM_POWER_STATUS: the four status fields are BYTE (unsigned) — c_ubyte, not c_byte.
    # A desktop with no battery reports BatteryLifePercent == 255 ("unknown"); read as a SIGNED
    # byte that is -1, which slipped through the 255 check and printed "Battery: -1%".
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", ctypes.c_ulong),
        ("BatteryFullLifeTime", ctypes.c_ulong),
    ]


def _win_battery() -> str:
    sps = _PowerStatus()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(sps)):
        return ""
    pct = sps.BatteryLifePercent
    if pct == 255:  # 255 = "unknown", which a desktop with no battery reports
        return "Power: AC (no battery)"
    state = "charging" if sps.ACLineStatus == 1 else "on battery"
    return f"Battery: {pct}% ({state})"


# --- find_document: the Windows Search index -------------------------------------------------
#
# The model composes the query from the utterance and this retrieves; nothing here ever opens or
# reads a file, so a wrong guess costs a wasted query, not a directory walk.
#
# ponytail: the index is reached through PowerShell's COM rather than pywin32. Its provider
# (Search.CollatorDSO) is OLE-DB — ADO is the only route, the stdlib has no COM, and pywin32 is
# not a dependency of this project (backend/paste.py made the same call for the clipboard).
# subprocess is a sanctioned Windows backend (docs/04 §Tools). This is NOT the raw-shell tool
# spec/30 rule 1 forbids: the model supplies search WORDS, never a command, and the finished SQL
# is handed over in an environment variable, so nothing the model wrote is ever parsed as
# PowerShell. Swap to pywin32 if the ~0.5 s process start ever matters.

FIND_LIMIT = 8

_FIND_PS = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
try {
  $c = New-Object -ComObject ADODB.Connection
  $c.Open("Provider=Search.CollatorDSO;Extended Properties='Application=Windows'")
  $rs = $c.Execute($env:NOTHAL_SQL)
  while (-not $rs.EOF) {
    $d = $rs.Fields.Item(2).Value
    @($rs.Fields.Item(0).Value,
      $(if ($d) { $d.ToString('yyyy-MM-dd') } else { '?' }),
      $rs.Fields.Item(1).Value) -join "`t"
    $rs.MoveNext()
  }
  $c.Close()
} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }
"""


def _search_terms(q, keep: str = "") -> str:
    """Whatever the model wrote, reduced to plain search words. This is the trust boundary shared
    by every retrieval tool here: the result is spliced into a query string literal, so anything
    that could close it or mean something to the query language is DROPPED rather than escaped —
    quotes, parens, `%` and `_` (LIKE wildcards), `*`, `-` operators. These query languages want
    bare terms anyway, so nothing useful is lost. `keep` re-admits characters a particular field
    genuinely needs (an address needs `@` and `.`); it is never given a quote.
    An absent optional parameter is "", NOT the string "None" — that would search for the word."""
    if q is None:
        return ""
    return " ".join(re.sub(rf"[^\w\s{re.escape(keep)}]", " ", str(q)).split())[:120]


def _iso_date(s) -> str:
    """`since` as a bare YYYY-MM-DD, or "" if the model sent something else (it does not go into
    the query then). Same reason as above: this ends up inside a SQL literal."""
    try:
        return datetime.fromisoformat(str(s)[:10]).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return ""


def _find_document(args: dict) -> str:
    if sys.platform != "win32":
        # ponytail: registered on every platform and degrading here, as read_clipboard does —
        # a per-platform _BACKENDS split earns its keep only when a macOS backend actually exists.
        return "Searching files needs the Windows Search index, which this machine does not have."

    query = _search_terms(args.get("query"))
    if not query:
        return "No usable search terms in that request — I need a word or two from the document."

    # Each term double-quoted and AND-ed: bare multi-word text is a syntax error to CONTAINS, and
    # quoting also demotes a stray AND/OR/NEAR from operator to literal word.
    where = [f"""CONTAINS('{' AND '.join(f'"{t}"' for t in query.split())}')"""]
    # The valid kinds live in the registry, not here (hard rule 3) — read them back off it.
    entry = _entry("find_document") or {}
    kinds = entry.get("parameters", {}).get("properties", {}).get("kind", {}).get("enum", [])
    if args.get("kind") in kinds:
        where.append(f"System.Kind = '{args['kind']}'")
    if since := _iso_date(args.get("since")):
        where.append(f"System.DateModified >= '{since}'")

    # Ranked, not date-sorted: `since` already handles "from this day", and on a real index a
    # date sort floats junk that merely CONTAINS the words (a word-list file matches everything)
    # above the document actually about them. The date rides along in each line regardless.
    sql = (f"SELECT TOP {FIND_LIMIT} System.ItemNameDisplay, System.ItemPathDisplay, "
           f"System.DateModified FROM SystemIndex WHERE {' AND '.join(where)} "
           f"ORDER BY System.Search.Rank DESC")

    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _FIND_PS],
            env={**os.environ, "NOTHAL_SQL": sql},
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW,  # no console flash over the overlay
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("find_document: %s", exc)
        return "Windows Search did not answer in time, so I could not look."

    if proc.returncode:
        log.warning("find_document: %s", proc.stderr.strip()[:200])
        return "Windows Search is not available on this machine (the index service may be off)."

    rows = [r.split("\t", 2) for r in proc.stdout.splitlines() if r.strip()]
    hits = [f"{n} · {d} · {p}" for n, d, p in (r for r in rows if len(r) == 3)]
    if not hits:
        return f"Nothing in the Windows Search index matches {query!r}."
    return f"Indexed matches for {query!r} (best first):\n" + "\n".join(hits)


# --- search_email: the desktop Outlook store --------------------------------------------------
#
# find_document's shape, second corpus: the model composes the criteria, the STORE does the
# filtering, and only headers come back — sender, date, subject. Bodies are searched (that is what
# `query` is for) but never returned, and nothing is opened, replied to or sent.
#
# Strictly the LOCAL desktop store over MAPI — no Graph, no cloud API, no credentials (spec/50).
# Same PowerShell-COM subprocess as find_document, and for the same reason: Outlook automation is
# COM-only and pywin32 is not a dependency.

MAIL_LIMIT = 8

_MAIL_PS = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
try {
  $ol = New-Object -ComObject Outlook.Application
  $inbox = $ol.GetNamespace("MAPI").GetDefaultFolder(6)   # olFolderInbox
} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 2 }
try {
  $items = $inbox.Items
  $items.Sort("[ReceivedTime]", $true)                    # newest first, BEFORE restricting
  if ($env:NOTHAL_DASL) { $items = $items.Restrict($env:NOTHAL_DASL) }
  $n = 0
  foreach ($m in $items) {
    if ($n -ge [int]$env:NOTHAL_MAX) { break }             # stop early: never walk a whole mailbox
    if ($m.Class -ne 43) { continue }                     # olMail only — a meeting request has no sender
    $d = $m.ReceivedTime
    @($m.SenderName, $(if ($d) { $d.ToString('yyyy-MM-dd HH:mm') } else { '?' }), $m.Subject) -join "`t"
    $n++
  }
} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 3 }
"""


def _mail_profile_exists() -> bool:
    """Is there a MAPI profile at all? Checked in the REGISTRY, before any COM call: asking
    Outlook for a mailbox when no profile exists can raise a "create a profile" DIALOG on the
    desktop, and a modal prompt behind a voice assistant is a hang with no way to answer it.
    ponytail: Office 16.0 covers 2016 through 365 — add a version if an older Outlook ever
    turns up. A profile existing does not prove it WORKS; the COM path still degrades on its own."""
    import winreg

    for path in (r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Windows Messaging Subsystem\Profiles",
                 r"SOFTWARE\Microsoft\Office\16.0\Outlook\Profiles"):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                if winreg.QueryInfoKey(key)[0]:  # at least one profile subkey
                    return True
        except OSError:
            continue
    return False


def _mail_filter(args: dict) -> tuple[str, list[str]]:
    """Build the DASL restriction, plus a plain-English echo of what it asks for. A DASL filter is
    an injection surface exactly like the SQL one, so every value goes through `_search_terms`
    first and dates through `_iso_date` — a value that sanitises to nothing is simply left out."""
    clauses, said = [], []
    p = "urn:schemas:httpmail:"

    if sender := _search_terms(args.get("sender"), keep="@.-"):
        clauses.append(f"""("{p}fromname" LIKE '%{sender}%' OR "{p}fromemail" LIKE '%{sender}%')""")
        said.append(f"from {sender!r}")
    if subject := _search_terms(args.get("subject")):
        clauses.append(f""""{p}subject" LIKE '%{subject}%'""")
        said.append(f"subject containing {subject!r}")
    if query := _search_terms(args.get("query")):
        # Word by word, not as one phrase: "lease renewal" should still find a mail whose subject
        # says "renewal of the lease". Each word must appear in the subject OR the body.
        for word in query.split():
            clauses.append(f"""("{p}subject" LIKE '%{word}%' OR "{p}textdescription" LIKE '%{word}%')""")
        said.append(f"mentioning {query!r}")
    if since := _iso_date(args.get("since")):
        clauses.append(f""""{p}datereceived" >= '{since}'""")
        said.append(f"on or after {since}")
    if before := _iso_date(args.get("before")):
        clauses.append(f""""{p}datereceived" < '{before}'""")
        said.append(f"before {before}")

    return ("@SQL=" + " AND ".join(clauses) if clauses else ""), said


def _search_email(args: dict) -> str:
    if sys.platform != "win32":
        return "Searching mail needs Outlook on Windows, which this machine does not have."
    if not _mail_profile_exists():
        return ("Outlook has no mail profile set up on this machine, so there is no mailbox to "
                "search.")

    dasl, said = _mail_filter(args)
    asked = ", ".join(said) if said else "the most recent mail"

    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _MAIL_PS],
            env={**os.environ, "NOTHAL_DASL": dasl, "NOTHAL_MAX": str(MAIL_LIMIT)},
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,  # no console flash over the overlay
        )
    except (OSError, subprocess.SubprocessError) as exc:
        # ponytail: 30 s covers a cold Outlook start; a warm one answers in well under a second.
        log.warning("search_email: %s", exc)
        return "Outlook did not answer in time, so I could not search your mail."

    if proc.returncode == 2:
        log.warning("search_email: no Outlook (%s)", proc.stderr.strip()[:200])
        return "Outlook is not available on this machine, so I could not search your mail."
    if proc.returncode:
        log.warning("search_email: %s", proc.stderr.strip()[:200])
        return "Outlook could not run that search."

    rows = [r.split("\t", 2) for r in proc.stdout.splitlines() if r.strip()]
    hits = [f"{s} · {d} · {subj}" for s, d, subj in (r for r in rows if len(r) == 3)]
    if not hits:
        return f"No mail in the Outlook inbox matches: {asked}."
    return f"Inbox matches ({asked}), newest first:\n" + "\n".join(hits)


# --- Tier 2: the reversible actions -----------------------------------------------------------
#
# Tier 2 is "not-hal may do this without asking, because you can undo it" — an app opened, a window
# raised, a media key pressed. What keeps that safe is not the tier alone but the SHAPE of the
# parameters: the model supplies a WORD, never a path and never a command. Each word is matched
# against something that already exists on this machine — a Start Menu shortcut, the title of an
# open window, a fixed key list read off the registry — so the worst a wrong guess can do is open
# an app the user already has. There is no parameter here that can name something new.
#
# Each of these also ANNOUNCES itself: the orchestrator pings `success`/`failure` as any call at
# Tier 2 or above returns (spec/30's tier table), so an action taken on your machine is never
# entirely silent.

_KEYEVENTF_KEYUP = 0x0002
_SW_RESTORE = 9

# The action names belong to the registry (hard rule 3); this only maps each to its Windows
# virtual key code. The selfcheck asserts the two sets match exactly, so adding an action to the
# schema without a key here fails offline instead of in front of the user.
_MEDIA_KEYS = {
    "play_pause": 0xB3,     # VK_MEDIA_PLAY_PAUSE
    "next": 0xB0,           # VK_MEDIA_NEXT_TRACK
    "previous": 0xB1,       # VK_MEDIA_PREV_TRACK
    "volume_up": 0xAF,      # VK_VOLUME_UP
    "volume_down": 0xAE,    # VK_VOLUME_DOWN
    "mute_toggle": 0xAD,    # VK_VOLUME_MUTE
}


def _norm(s) -> str:
    """Fold a name to bare lowercase alphanumerics, so 'Google Chrome', 'google-chrome' and
    'googlechrome' are one key. Shared by the app matcher and the window matcher — spoken names
    arrive without the punctuation the real thing was given."""
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def _closest(asked, names) -> list[str]:
    """The names closest to what was asked for, so a miss can offer real options instead of a bare
    no. Compared case-folded and returned in their real spelling — a spoken name arrives all
    lowercase and would otherwise score badly against 'Google Chrome'.

    The 0.6 cutoff is deliberately strict: at 0.5, asking for an app this PC does NOT have
    answered "the closest are: ReadMe, Camera" (measured for 'chrome'), which is worse than
    saying nothing — it reads as a considered suggestion. Better an honest bare no than three
    confident wrong ones. 'spotifi' -> 'Spotify' still clears it."""
    real = {n.lower(): n for n in names}
    return [real[n] for n in
            difflib.get_close_matches(str(asked or "").lower(), list(real), n=3, cutoff=0.6)]


_APPS_PS = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Get-StartApps | ForEach-Object { "$($_.Name)`t$($_.AppID)" }
"""


_APPS_CACHE: list[tuple[str, str]] = []


def _start_apps(refresh: bool = False) -> list[tuple[str, str]]:
    """`(name, AppID)` for every app this PC's Start Menu shows, asked of Windows itself.

    This IS open_app's vocabulary, and it is why nothing has to be configured: whatever is
    installed is already listed, so "open spotify" works on a fresh machine. The user's alias
    table (schemas/app_aliases.json) is for the exceptions, not the rule.

    `Get-StartApps` rather than walking the Start Menu FOLDERS, which was the first cut and was
    wrong: those folders hold only classic installers' `.lnk` files, so Notepad, Calculator,
    Terminal and every Store app are missing from them — which is precisely the set a person is
    most likely to ask for. Measured on this box: 110 shortcuts on disk against 139 apps Windows
    actually lists.

    Same PowerShell subprocess as the retrieval tools, for the same reason (spec/30 rule 3): a
    sanctioned Windows backend, and pywin32 is not a dependency of this project.

    **Cached for the life of the process, refreshed on a miss.** The ~0.7 s subprocess was
    acceptable while it hid behind a model round; it is not once the deterministic path stops
    calling one, and in use the whole turn felt slower than typing
    (2026-08-03). Staleness has exactly one symptom — an app installed since we last looked is
    not found — so `_open_app` refreshes and retries once when a name matches nothing, and a
    newly installed app is found on the second look. Every hit stays free, and the only call that
    pays twice is one that was already failing."""
    global _APPS_CACHE
    if _APPS_CACHE and not refresh:
        return _APPS_CACHE
    if sys.platform != "win32":
        return []
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _APPS_PS],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW,   # no console flash over the overlay
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("open_app: could not list this PC's apps (%s)", exc)
        return []
    if proc.returncode:
        log.warning("open_app: Get-StartApps failed (%s)", proc.stderr.strip()[:200])
        return []
    rows = [r.split("\t", 1) for r in proc.stdout.splitlines() if r.strip()]
    _APPS_CACHE = [(n, a) for n, a in (r for r in rows if len(r) == 2) if n and a]
    return _APPS_CACHE


def _resolve_app(asked, apps=None, aliases=None):
    """What a person called an app -> something launchable. Returns `(name, target, near)`: the
    app's real name and either its Windows AppID (a string) or, for an alias pointing at a file,
    a Path. Nothing matched gives `("", None, near)`, where `near` holds the closest real names
    so the answer can offer them instead of a bare no.

    Order: the user's alias table wins outright, then an exact name, then a prefix, then a
    substring — shortest name first, so 'chrome' reaches 'Google Chrome' and not 'Google Chrome
    Canary'. A malformed alias entry is skipped rather than thrown, as the word-replacement table
    is: a hand-edited file must never break a tool."""
    apps = _start_apps() if apps is None else apps
    aliases = load_schemas()["app_aliases"]["aliases"] if aliases is None else aliases
    want = _norm(asked)
    if not want:
        return "", None, []

    by_norm: dict[str, tuple[str, str]] = {}
    for name, app_id in apps:
        by_norm.setdefault(_norm(name), (name, app_id))

    for entry in aliases:
        opens = str(entry.get("open") or "")
        if opens and _norm(entry.get("say")) == want:
            # An alias either names another Start Menu app ('spot' -> 'Spotify') or gives a full
            # path, for something Windows does not list at all. The USER wrote this file — the
            # model only ever supplies `say` — which is why a path is allowed here and nowhere
            # else in Contract T.
            if hit := by_norm.get(_norm(opens)):
                return hit[0], hit[1], []
            return Path(opens).stem, Path(opens), []

    if want in by_norm:
        return (*by_norm[want], [])
    for pool in ([n for n in by_norm if n.startswith(want)],
                 [n for n in by_norm if want in n]):
        if pool:
            return (*by_norm[min(pool, key=len)], [])
    return "", None, _closest(asked, sorted({n for n, _ in apps}))


def _open_app(args: dict) -> str:
    if sys.platform != "win32":
        # ponytail: macOS is `open -a "<Name>"`, with no Start Menu to ask (a macOS seam).
        return "Opening apps needs Windows, which this machine is not running."
    asked = str(args.get("app") or "").strip()
    if not asked:
        return "That request did not name an app to open."

    name, target, near = _resolve_app(asked)
    if target is None and _APPS_CACHE:
        # The one thing a stale list can cause is a miss, so a miss is where we pay to re-look.
        # An app installed since this process started is found on the second try.
        name, target, near = _resolve_app(asked, apps=_start_apps(refresh=True))
    if target is None:
        if near:
            return (f"There is no app called {asked!r} on this PC. The closest installed names "
                    f"are: {', '.join(near)}.")
        return f"There is no app called {asked!r} installed on this PC."

    try:
        if isinstance(target, Path):
            os.startfile(target)     # only ever a path the USER put in the alias table
        else:
            # The AppsFolder shell namespace launches classic and Store apps alike from the one
            # AppID. explorer.exe is handed an argument LIST, never a command line, and the AppID
            # came out of Windows' own list — the model supplied a word and nothing more.
            # Its exit code is not read: explorer returns 1 on a perfectly good launch.
            subprocess.run(["explorer.exe", rf"shell:AppsFolder\{target}"], timeout=20,
                           creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("open_app %s: %s", name, exc)
        return f"Windows would not open {name!r}: {exc}"
    return f"Opened {name}."


def _visible_windows() -> list[tuple[int, str]]:
    """`(handle, title)` for every visible top-level window that has a title — focus_window's
    vocabulary, and the only thing it can act on."""
    from ctypes import wintypes

    u = ctypes.windll.user32
    u.IsWindowVisible.argtypes = [wintypes.HWND]
    u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    u.GetWindowTextW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]

    found: list[tuple[int, str]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _each(hwnd, _lparam):
        if u.IsWindowVisible(hwnd):
            length = u.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                u.GetWindowTextW(hwnd, buf, length + 1)
                if buf.value.strip():
                    found.append((hwnd, buf.value))
        return True                              # keep enumerating

    u.EnumWindows(_each, 0)
    return found


def _match_window(query, windows: list[tuple[int, str]]):
    """The best title match: an exact title first, then the SHORTEST title containing the query —
    'mail' should reach a window titled 'Mail' before 'Mailchimp — Google Chrome'."""
    want = _norm(query)
    if not want:
        return None
    for hwnd, title in windows:
        if _norm(title) == want:
            return hwnd, title
    hits = [w for w in windows if want in _norm(w[1])]
    return min(hits, key=lambda w: len(w[1])) if hits else None


def _to_foreground(hwnd) -> bool:
    """Raise a window and give it the keyboard, around the foreground lock. Returns whether it
    actually ended up in front — CHECKED, not assumed.

    Windows only lets the process that already owns the foreground hand it on, and the daemon
    never owns it, so a bare `SetForegroundWindow` quietly does nothing. Attaching our input
    thread to the current foreground window's thread makes us that process for the length of the
    call, and we detach immediately. The other published workaround — injecting a synthetic ALT
    keypress — also works, but it lands in whatever app is in front and can pop its menu bar,
    which is a side effect on a window nobody asked us to touch.

    The verification is the point: a call that silently failed and reported success would be the
    D36 failure again — a capability failure narrated as though it had happened."""
    from ctypes import wintypes

    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    for fn in (u.SetForegroundWindow, u.BringWindowToTop, u.IsIconic):
        fn.argtypes = [wintypes.HWND]
    u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    u.GetForegroundWindow.restype = wintypes.HWND
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]

    if u.IsIconic(hwnd):
        u.ShowWindow(hwnd, _SW_RESTORE)          # a minimised window cannot take the foreground
    u.SetForegroundWindow(hwnd)
    if u.GetForegroundWindow() == hwnd:
        return True

    theirs = u.GetWindowThreadProcessId(u.GetForegroundWindow(), None)
    ours = k.GetCurrentThreadId()
    if theirs and theirs != ours and u.AttachThreadInput(ours, theirs, True):
        try:
            u.BringWindowToTop(hwnd)
            u.SetForegroundWindow(hwnd)
        finally:
            u.AttachThreadInput(ours, theirs, False)
    return u.GetForegroundWindow() == hwnd


def _focus_window(args: dict) -> str:
    if sys.platform != "win32":
        return "Bringing a window forward needs Windows, which this machine is not running."
    asked = str(args.get("title_query") or "").strip()
    if not asked:
        return "That request did not say which window to bring forward."

    windows = _visible_windows()
    hit = _match_window(asked, windows)
    if hit is None:
        near = _closest(asked, [t for _, t in windows])
        if near:
            return f"No open window matches {asked!r}. Open right now: {', '.join(near)}."
        return f"No open window matches {asked!r}."

    hwnd, title = hit
    if _to_foreground(hwnd):
        return f"Brought {title!r} to the front."
    return (f"I found the window {title!r}, but Windows would not bring it forward — it may be "
            f"showing a dialog, or running as administrator.")


def _media_control(args: dict) -> str:
    """Press a media or volume key exactly as the keyboard's own would; Windows routes it to
    whatever app currently owns media. That is also why the answer says the key was SENT rather
    than that music is now playing — nothing reports back, and the stronger claim would be a
    guess wearing a fact's clothes."""
    if sys.platform != "win32":
        return "The media keys need Windows, which this machine is not running."
    action = str(args.get("action") or "")
    vk = _MEDIA_KEYS.get(action)
    if vk is None:
        return f"{action!r} is not a media action I can send. I can do: {', '.join(_MEDIA_KEYS)}."
    # ponytail: keybd_event, as backend/paste.py uses for Ctrl+V — a couple of lines instead of a
    # SendInput struct array, and adequate for one key. Move up only if an app ignores it.
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, 0)
    return f"Sent the {action.replace('_', ' ')} key."


_BACKENDS: dict[str, Callable[[dict], str]] = {
    "system_status": _system_status,
    "read_clipboard": _read_clipboard,
    "find_document": _find_document,
    "search_email": _search_email,
    "open_app": _open_app,
    "focus_window": _focus_window,
    "media_control": _media_control,
    # set_timer is in the registry and within the tier, but has no backend: a timer FIRES outside
    # any turn and Contract P has no message that can announce it (STATE, Track T). No backend =
    # never offered and refused if called, which is the honest state of it.
}


# --- the registry, the filtered tool list, and dispatch --------------------------------------


def _registry() -> list[dict]:
    """The raw Contract T registry (shared/schemas/tools.json), loaded fresh (hard rule 3)."""
    return load_schemas()["tools"]["tools"]


def _entry(name: str) -> dict | None:
    return next((t for t in _registry() if t.get("name") == name), None)


def _connectors() -> dict[str, bool]:
    """`{connector id: is it switched on}`, read FRESH from the user's settings (D38). Derived
    from the schema's `connector_*` entries rather than a list here, so adding a connector is a
    JSON edit (hard rule 3). Settings are re-read every turn, which is why a toggle applies to
    the next utterance with no restart and no watcher."""
    now = settings.load()
    # `is True`, never `bool(...)`: a consent gate must be EXPLICIT. settings.load() has already
    # merged the schema defaults (real booleans), so an unset key reads its default; the only thing
    # this rejects is a hand-edited file holding a non-boolean — `"connector_files": "false"` is a
    # truthy STRING and `bool()` would read it as ON, which is the one direction a consent gate must
    # never fail. Everything that is not literally `true` fails closed.
    return {s["connector"]: (now.get(key) is True)
            for key, s in settings.schema()["settings"].items() if "connector" in s}


def _connected(entry: dict, on: dict[str, bool]) -> bool:
    """Has the user consented to this tool's connector? A tool naming a connector that has no
    setting is treated as OFF, not on: an unrecognised id must fail closed, or a typo in the
    registry would quietly hand the model a tool nobody agreed to."""
    return on.get(entry.get("connector"), False)


def label_of(name: str) -> str:
    """A tool said in a sentence a person would use, from the registry's `label` — or the bare
    tool name if it has none. What the island shows while the tool runs and what the connector
    card lists (D38); one wording, read from the schema by both (hard rule 3)."""
    return (_entry(name) or {}).get("label", "") or name


def tier_of(name: str) -> int:
    """A tool's tier from the registry, or 0 if it has none — so a caller asking "was that a
    Tier-2 action?" about a refused unknown tool gets a plain no rather than an exception. What
    the orchestrator reads to decide whether the call needs announcing (spec/30's tier table)."""
    return (_entry(name) or {}).get("tier", 0)


def implemented(entry: dict) -> bool:
    """Could this tool run AT ALL on this machine — is there a backend, and is it within the tier
    ceiling? Deliberately the designer's half of the question only; whether the user WANTS it is
    the connector's, asked separately. The settings window calls this so a connector card can show
    which of its tools are real today rather than promising what the tier still forbids."""
    return entry.get("name") in _BACKENDS and entry.get("tier", 99) <= MAX_TIER


def tool_specs() -> list[dict]:
    """The tools handed to the model this turn: only those with a backend on this platform,
    within the enabled tier, AND whose connector the user has switched on (spec/30 rule 3 — the
    model never receives a tool it cannot call). Tier and connector are independent: either one
    alone is enough to withhold a tool."""
    on = _connectors()
    return [t for t in _registry() if implemented(t) and _connected(t, on)]


def disabled_note() -> str:
    """One sentence for the system prompt naming what the user has switched OFF, or "" if
    nothing is (D38). Without it a hidden tool is simply absent, and a model asked to find a file
    improvises instead of saying it cannot — the can't-rendered-as-didn't failure D36 found in
    `search_email`, which is a lie about what happened, not merely an unhelpful answer.

    Only connectors that would OTHERWISE be usable are named — a connector with no implemented,
    in-tier tool behind it is left unmentioned, because telling the model "Web is off" implies
    switching it on would work. Labels come from the schema, so this text follows the pane."""
    on = _connectors()
    live = {t["connector"] for t in _registry() if implemented(t)}
    off = [s["label"] for s in settings.schema()["settings"].values()
           if s.get("connector") in live and not on.get(s["connector"])]
    if not off:
        return ""
    return (f" Switched off in this user's settings, so you have no way to reach them: "
            f"{', '.join(off)}. If one of those is what a request needs, say plainly that it is "
            f"switched off in settings — never imply you looked and found nothing.")


def execute(call: ToolCall, *, session: str = "", transcript: str = "") -> tuple[str, str]:
    """Run one tool call through Contract T. Returns `(content, outcome)` and NEVER raises: a
    tool fault becomes a string the model reads and narrates, not an exception that kills the
    turn. Every path — run, refused, errored — is audited before returning (spec/30 rule 2)."""
    name = call.name
    args = dict(call.input or {})
    t0 = time.perf_counter()

    entry = _entry(name)
    backend = _BACKENDS.get(name)
    if entry is None or backend is None:
        # Not in the registry, or no backend on this platform. The allowlist is the defence.
        content, outcome = f"Tool {name!r} is not available.", "refused:unknown_tool"
    elif entry.get("tier", 99) > MAX_TIER:
        content = f"Tool {name!r} needs a confirmation step that is not built yet."
        outcome = f"refused:tier_{entry.get('tier')}"
    elif not _connected(entry, _connectors()):
        # The consent gate, checked again here and not only in the filter (D38): a tool the user
        # switched off must be dead even if something else calls it — history from before the
        # toggle, a resampled round, a future caller that skips tool_specs().
        content = f"Tool {name!r} is switched off in this user's settings."
        outcome = f"refused:connector_{entry.get('connector')}"
    else:
        try:
            content, outcome = backend(args), "ok"
        except Exception as exc:  # noqa: BLE001 — a tool fault is data for the model, not a crash
            content, outcome = f"Tool {name!r} failed: {exc}", "error"
            log.warning("tool %s failed: %s", name, exc)

    _audit(session, transcript, name, args, outcome, round((time.perf_counter() - t0) * 1000, 1))
    return content, outcome


def _audit(session, transcript, tool, args, outcome, duration_ms) -> None:
    """Append one audit record (spec/30 rule 2 shape). Best-effort on the WRITE: a full disk must
    not take the daemon down mid-turn, so a failed write is logged loudly and the call proceeds —
    the same degrade-don't-crash posture shared.log takes.
    ponytail: warn-and-proceed rather than refuse-if-unloggable; harden to refuse only if the
    audit trail ever has to be provably complete on this prototype."""
    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "session": session,
        "transcript_snippet": (transcript or "")[:200],
        "tool": tool,
        "args": args,
        "outcome": outcome,
        "duration_ms": duration_ms,
    }
    try:
        AUDIT_FILE.parent.mkdir(exist_ok=True)
        with AUDIT_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.error("AUDIT WRITE FAILED for %s (%s) — call ran unlogged", tool, exc)


def _selfcheck() -> None:
    # No network, no real audio: the logic worth guarding is the two GATES (a tool the model must
    # not see), dispatch, the refusal backstop, and that every path audits.
    import tempfile

    global AUDIT_FILE, _BACKENDS, _APPS_CACHE

    reg = _registry()
    assert reg, "shared/schemas/tools.json must carry the starter tools"
    assert all(t.get("connector") for t in reg), "every tool declares a connector (D38)"

    # Both gates read the USER's settings, so pointing them at an empty file is what makes this
    # check deterministic — otherwise it would pass or fail with whatever is toggled on this box.
    # No try/finally: this runs as a one-shot script, so a leaked env var in a process that is
    # either about to print OK or about to die on a traceback buys nothing.
    settings_dir = tempfile.TemporaryDirectory()
    os.environ["NOTHAL_SETTINGS"] = str(Path(settings_dir.name) / "settings.json")
    keys = [k for k, s in settings.schema()["settings"].items() if "connector" in s]
    assert keys, "the connectors pane must declare its settings (schemas/settings.json)"

    # A fresh install: System only. Files, Email and Clipboard are consent, not danger, and stay
    # off until they are asked for (D38) — so not-hal answers and dictates and reaches nothing.
    assert {t["name"] for t in tool_specs()} == {"system_status"}, tool_specs()

    # Every connector on: every tool that has a backend AND sits within the ceiling appears.
    for k in keys:
        settings.set(k, True)
    offered = {t["name"] for t in tool_specs()}
    assert offered == {"system_status", "read_clipboard", "find_document", "search_email",
                       "open_app", "focus_window", "media_control"}, offered
    for t in reg:
        if t["tier"] > MAX_TIER:
            assert t["name"] not in offered, f"{t['name']}: tier {t['tier']} must not be offered"
    # A tool the registry defines but nothing implements is NOT offered, even in tier and with
    # its connector on — set_timer needs a surface that can announce it firing outside a turn.
    # Being in the registry is a promise about the contract, not about this machine.
    assert tier_of("set_timer") <= MAX_TIER and "set_timer" not in offered, offered
    assert all("tier" in t for t in tool_specs()), "specs carry the tier for the loop to read"
    # The tier the orchestrator reads to decide whether a call needs announcing.
    assert tier_of("open_app") == 2 and tier_of("system_status") == 1, "tiers come off the registry"
    assert tier_of("no_such_tool") == 0, "an unknown tool must not look like a Tier-2 action"
    # ...and with everything on there is nothing to warn the model about.
    assert disabled_note() == "", disabled_note()

    # The connector alone is also sufficient to exclude: switching Files off removes exactly
    # find_document and leaves every other tool where it was.
    settings.set("connector_files", False)
    assert {t["name"] for t in tool_specs()} == offered - {"find_document"}, tool_specs()

    # A consent gate must be EXPLICIT: a hand-edited file holding a non-boolean fails CLOSED. Only
    # the literal boolean `true` is ON — "false"/"0"/"off" are truthy STRINGS that a bool() read
    # would have flipped on, the one direction consent must never fail. (The UI only ever writes
    # real booleans; this is the file-override path spec/70 §2 sanctions.)
    for junk in ("false", "0", "off", "true", 1):
        settings.set("connector_files", junk)
        assert "find_document" not in {t["name"] for t in tool_specs()}, \
            f"a non-boolean connector value must fail closed, got ON for {junk!r}"
    settings.set("connector_files", True)
    assert "find_document" in {t["name"] for t in tool_specs()}, "the real boolean True is ON"
    settings.set("connector_files", False)
    assert {t["name"] for t in tool_specs()} == offered - {"find_document"}, tool_specs()

    # ...and the model is TOLD, in prose. A hidden tool is merely absent, which reads as "no such
    # capability exists" and gets improvised around — the can't-rendered-as-didn't failure of D36.
    # Only connectors with a usable tool behind them are named: saying "Web is off" would imply
    # switching it on would work, and there is no web tool at all.
    note = disabled_note()
    assert "Files" in note and "switched off" in note, note
    for absent in ("Web", "MCP"):
        assert absent not in note, f"a connector with no live tool must not be named: {note}"
    settings.set("connector_files", True)

    # Apps & media has live tools now, so switching it off is a thing the model must be TOLD —
    # the same rule that used to keep it unmentioned now requires naming it. Off is its default.
    settings.set("connector_apps_media", False)
    assert "Apps & media" in disabled_note(), disabled_note()
    assert "open_app" not in {t["name"] for t in tool_specs()}, tool_specs()
    settings.set("connector_apps_media", True)

    # find_document's trust boundary: the model's words end up inside a SQL string literal, so
    # everything that could close it or steer the query is dropped, and a bad date never lands.
    assert _search_terms("bob's ' OR 1=1 --") == "bob s OR 1 1", _search_terms("bob's ' OR 1=1 --")
    assert "'" not in _search_terms("a'b\"c;d`e$f(g)") and "$" not in _search_terms("a$b")
    assert _search_terms("café über") == "café über", "real words survive, only punctuation goes"
    # An OMITTED optional parameter must vanish, not become a search for the word "None".
    assert _search_terms("!!!") == "" and _search_terms(None) == ""
    # `keep` re-admits what an address needs, and not one character more.
    assert _search_terms("sarah.jones@example.com", keep="@.-") == "sarah.jones@example.com"
    assert "'" not in _search_terms("o'brien@x.com", keep="@.-")
    assert _iso_date("2026-01-31") == "2026-01-31"
    assert _iso_date("last tuesday") == "" and _iso_date(None) == "" and _iso_date(7) == ""

    # search_email's DASL is built from the same sanitised parts. Nothing the model wrote can
    # close the string literal or smuggle in a LIKE wildcard, and an omitted param adds no clause.
    dasl, said = _mail_filter({"sender": "sarah", "since": "2026-05-01"})
    assert dasl.startswith("@SQL=") and "fromname" in dasl and "datereceived" in dasl, dasl
    assert "subject\" LIKE" not in dasl, "an omitted parameter must not add a clause"
    assert said == ["from 'sarah'", "on or after 2026-05-01"], said
    hostile = _mail_filter({"subject": "x%' OR '1'='1"})[0]
    assert hostile.count("'") == 2, f"the value must stay inside ONE literal: {hostile}"
    assert "x OR 1 1" in hostile, f"...as its declawed self: {hostile}"
    assert _mail_filter({})[0] == "", "no criteria means no restriction, not a malformed one"
    # Each free-text word gets its own subject-OR-body clause, so word order never matters.
    assert _mail_filter({"query": "lease renewal"})[0].count("textdescription") == 2

    # --- Tier 2's matchers, off the wire. Nothing below opens an app, moves a window or presses
    # a key: the RESOLUTION is the logic, and it is pure once it is handed a list.
    assert _norm("Google Chrome") == _norm("google-chrome") == "googlechrome"
    assert _norm(None) == "" and _norm("  ") == ""

    apps = [("Google Chrome", "Chrome.AppID"),
            ("Google Chrome Canary", "Canary.AppID"),
            ("Spotify", "SpotifyAB.SpotifyMusic_x!Spotify"),
            ("Word", "Microsoft.Office.WINWORD.EXE.15")]
    assert _resolve_app("word", apps, [])[:2] == ("Word", "Microsoft.Office.WINWORD.EXE.15")
    assert _resolve_app("SPOTIFY", apps, [])[0] == "Spotify"
    assert _resolve_app("google chrome", apps, [])[0] == "Google Chrome"
    # Shortest match wins, so a spoken 'chrome' does not land on the Canary build.
    assert _resolve_app("chrome", apps, [])[0] == "Google Chrome"
    # A miss offers the closest REAL names rather than a bare no — and finds them despite the
    # case difference, which is the whole reason _closest folds case.
    name, target, near = _resolve_app("spotifi", apps, [])
    assert target is None and near == ["Spotify"], (name, target, near)
    assert _resolve_app("zzqx nosuch", apps, []) == ("", None, [])
    assert _resolve_app("", apps, [])[1] is None, "an empty name matches nothing, not everything"
    # The user's alias table wins outright, and may name another Start Menu app OR give a path.
    assert _resolve_app("browser", apps, [{"say": "browser", "open": "Spotify"}])[0] == "Spotify"
    assert _resolve_app("thing", apps, [{"say": "thing", "open": r"D:\t\thing.exe"}])[1] \
        == Path(r"D:\t\thing.exe"), "a path in the alias table is the user's own, and is honoured"
    # A malformed alias entry is SKIPPED and the lookup still answers — a hand-edited table must
    # never break a tool (backend/replace.py takes the same posture with its own).
    assert _resolve_app("word", apps, [{"say": "word"}, {}, {"open": "x"}])[0] == "Word"
    assert isinstance(load_schemas()["app_aliases"]["aliases"], list), "the shipped table loads"

    # The app list is cached for the process — asking Windows costs ~0.7 s (measured), which is
    # most of a deterministic launch once no model round is hiding it. Seeded by hand so this is
    # deterministic off Windows too: a warm cache must be answered WITHOUT a subprocess, and
    # removing the short-circuit fails here rather than quietly costing 0.7 s a turn again.
    _APPS_CACHE = [("Sentinel App", "Sentinel.AppID")]
    assert _start_apps() == [("Sentinel App", "Sentinel.AppID")], "a warm cache must not re-fetch"
    assert _resolve_app("sentinel app")[1] == "Sentinel.AppID", "...and resolution reads it"
    _APPS_CACHE = []

    # focus_window: an exact title first, then the SHORTEST title containing the query.
    wins = [(1, "Mailchimp — Google Chrome"), (2, "Mail"), (3, "Inbox — Outlook")]
    assert _match_window("mail", wins) == (2, "Mail"), _match_window("mail", wins)
    assert _match_window("outlook", wins) == (3, "Inbox — Outlook")
    assert _match_window("mailchimp", wins) == (1, "Mailchimp — Google Chrome")
    assert _match_window("photoshop", wins) is None and _match_window("", wins) is None

    # media_control's key map must cover the registry's enum EXACTLY. The registry is the truth
    # (hard rule 3); an action added there with no key here would be offered to the model and
    # then fail in front of the user, which is exactly the shape this catches offline.
    actions = _entry("media_control")["parameters"]["properties"]["action"]["enum"]
    assert set(actions) == set(_MEDIA_KEYS), (sorted(actions), sorted(_MEDIA_KEYS))

    with tempfile.TemporaryDirectory() as tmp:
        AUDIT_FILE = Path(tmp) / "audit.jsonl"

        # These execute() calls test DISPATCH + AUDIT + the gate — NOT the real backends, which on a
        # box with a clipboard and an Outlook profile would read the user's actual clipboard and
        # inbox — search_email with no criteria enumerates real mail. Stub the two that read
        # personal CONTENT
        # so the check touches none; system_status and find_document stay real (a local-time read
        # and an index query for a nonsense term — neither returns personal data). Each stubbed
        # backend's own degradation and trust boundary are proven directly above, off the wire.
        # ...and the same for the three Tier-2 backends, which ACT: running them for real here
        # would open an app, steal the foreground and change this machine's volume. Their
        # resolution logic — the only part with a decision in it — is proven directly above.
        _real_backends = _BACKENDS
        _BACKENDS = {**_real_backends,
                     "read_clipboard": lambda a: "(clipboard not read during the selfcheck)",
                     "search_email": lambda a: "(inbox not read during the selfcheck)",
                     "open_app": lambda a: f"(would open {a.get('app')!r})",
                     "focus_window": lambda a: f"(would focus {a.get('title_query')!r})",
                     "media_control": lambda a: f"(would send {a.get('action')!r})"}

        # An unknown tool is refused, not executed — the allowlist backstop behind the filter.
        content, outcome = execute(ToolCall("1", "no_such_tool", {}), session="s", transcript="hi")
        assert outcome == "refused:unknown_tool" and "not available" in content, (content, outcome)

        # A tool that IS in the registry and in tier but has no backend is refused too (defence
        # in depth: even if the filter were bypassed, execute() still says no).
        content, outcome = execute(ToolCall("2", "set_timer", {"seconds": 60}))
        assert outcome == "refused:unknown_tool", (content, outcome)

        # A real Tier-1 tool runs and returns something the model can read — including a UTC
        # offset, so a "what time in <city>" question is arithmetic the model does itself (rung 1).
        content, outcome = execute(ToolCall("3", "system_status", {}), session="s")
        assert outcome == "ok" and "time" in content.lower(), (content, outcome)
        assert "utc" in content.lower(), f"time needs a UTC anchor for zone conversion: {content!r}"

        # read_clipboard runs; on a headless runner with no clipboard it degrades to a string
        # rather than raising (like paste.py's own selfcheck).
        content, outcome = execute(ToolCall("4", "read_clipboard", {}))
        assert outcome in ("ok", "error"), (content, outcome)

        # find_document dispatches and ALWAYS answers in prose, whatever the machine offers: a
        # real hit list on an indexed box, "not available" off Windows or with the index service
        # off (a CI runner). A nonsense term keeps it deterministic — the point is the round trip,
        # not the corpus, so no live index is required here.
        content, outcome = execute(ToolCall("5", "find_document", {"query": "zzqx nosuch term"}))
        assert outcome == "ok" and content.strip(), (content, outcome)

        # ...including when the model sends junk in the optional params: an unknown `kind` and an
        # unparseable `since` are dropped, not passed through to the query.
        content, outcome = execute(
            ToolCall("6", "find_document", {"query": "zzqx", "kind": "spaceship", "since": "soon"})
        )
        assert outcome == "ok" and content.strip(), (content, outcome)

        # search_email dispatches and answers in prose on every machine: real headers where
        # Outlook has a profile, "not available" where it does not (a CI runner, or a machine
        # with Outlook installed but no profile). No live mailbox is required.
        content, outcome = execute(ToolCall("7", "search_email", {"sender": "zzqx", "since": "2026-01-01"}))
        assert outcome == "ok" and content.strip(), (content, outcome)

        # ...and with NO criteria at all, which is legal here (every parameter is optional) and
        # must mean "the most recent mail", not a malformed restriction.
        content, outcome = execute(ToolCall("8", "search_email", {}))
        assert outcome == "ok" and content.strip(), (content, outcome)

        # A tool whose connector the user switched off is REFUSED even when called directly, not
        # merely hidden (D38): the filter is convenience, the allowlist is the defence. This is
        # the path a stale round or a caller that skips tool_specs() would take.
        settings.set("connector_clipboard", False)
        content, outcome = execute(ToolCall("9", "read_clipboard", {}))
        assert outcome == "refused:connector_clipboard", (content, outcome)
        assert "switched off" in content, content
        settings.set("connector_clipboard", True)

        # The three Tier-2 tools dispatch and come back `ok`, which is what the orchestrator
        # turns into the announce earcon. A Tier-2 tool is refused when ITS connector is off,
        # exactly as a Tier-1 one is — the two gates do not change shape with the tier.
        for i, (name, a) in enumerate([("open_app", {"app": "spotify"}),
                                       ("focus_window", {"title_query": "mail"}),
                                       ("media_control", {"action": "play_pause"})], start=10):
            content, outcome = execute(ToolCall(str(i), name, a))
            assert outcome == "ok" and content.strip(), (name, content, outcome)
        settings.set("connector_apps_media", False)
        content, outcome = execute(ToolCall("13", "open_app", {"app": "spotify"}))
        assert outcome == "refused:connector_apps_media", (content, outcome)
        settings.set("connector_apps_media", True)

        # Every one of those calls left exactly one audit line, with the required fields — a
        # refused call is audited exactly as a run one is (spec/30 rule 2).
        lines = AUDIT_FILE.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 13, f"every call must audit once, got {len(lines)}"
        rec = json.loads(lines[0])
        assert set(rec) == {"ts", "session", "transcript_snippet", "tool", "args",
                            "outcome", "duration_ms"}, sorted(rec)
        assert rec["tool"] == "no_such_tool" and rec["session"] == "s"
        assert rec["transcript_snippet"] == "hi", "the triggering transcript is recorded"
        _BACKENDS = _real_backends

    settings_dir.cleanup()          # the settings temp dir was held open for the whole check
    os.environ.pop("NOTHAL_SETTINGS", None)
    print(f"tools selfcheck OK: {len(offered)} tools offered up to tier {MAX_TIER} with every "
          f"connector on ({', '.join(sorted(offered))}); tier, connector, a missing backend and "
          f"the allowlist each refuse on their own, the Tier-2 matchers resolve off the wire, "
          f"and every call is audited")


if __name__ == "__main__":
    _selfcheck()
