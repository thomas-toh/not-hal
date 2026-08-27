"""Track G step ② (STATE): the two doors (D20) — global hotkeys for **ask** and **dictate**.

Each door is hybrid (spec/40): **tap** opens a capture and a second tap closes it;
**hold ≥ HOLD_S** is push-to-talk and the release closes it. The key is the endpoint —
the assistant's 1 s VAD silence cut does not end a keyed turn (see `capture_over` in
backend/orchestrator.py, and `auto_end` for the config-adjustable alternative).

**Why the narrow Win32 API and not a keyboard hook (spec/50).** The obvious library
(`pynput`) installs a system-wide low-level keyboard hook — not-hal's process would then
see *every* keystroke on the machine. `RegisterHotKey` instead asks the OS to deliver
only the specific combos we registered: no keystream, nothing else observed, and the
combo is consumed so other apps never see it either. `GetAsyncKeyState` is a query, not
a hook, and we only ever query the key we registered.

**macOS is NOT covered.** The narrow equivalent is Carbon `RegisterEventHotKey` (needs
pyobjc) — unbuilt. On a Mac `start()` warns and the doors never fire; the wake word is
still the hands-free entrance to the ask door, so the assistant works (macOS gap).

Run:
    python -m backend.hotkeys              # live: press the keys, watch the events
    python -m backend.hotkeys --selfcheck  # no keyboard: parsing + the tap/hold machine
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time

from shared import settings
from shared.log import setup_logging

log = logging.getLogger("nothal.hotkeys")

HOLD_S = 0.5        # spec/40: held this long -> push-to-talk, release is the endpoint
POLL_S = 0.025      # key-release poll; 25 ms is well inside the 300 ms press->indication target
SYNC_S = 0.1        # how often the pump re-reads the bindings and the pause lease. Short because
                    # it bounds the gap between clicking Record and the doors letting go: one
                    # small file read per tick, against a stray turn if a key lands inside it.

# The fallback combo per door, used only when the setting is blank or unparseable. The env vars
# stay as the headless escape hatch (`python -m backend.hotkeys`, CI), but they are BENEATH the
# setting: an env var left over in a shell used to outrank the settings window silently, which is
# the same written-but-unread failure the picker had.
# DISMISS IS NOT HERE (D24). Esc is a bare key, so a standing registration would consume it
# machine-wide; it used to be a "transient" door this module armed and disarmed in step with
# the daemon's idea of what was on screen. The Teleprompter now owns Esc outright, because it
# is the thing on screen and therefore the only party that knows — which deleted the arming
# protocol, its cross-thread race, and the modifier-less exemption below along with it.
DEFAULT_BINDINGS = {
    "ask":     os.environ.get("NOTHAL_HOTKEY_ASK", "ctrl+alt+1"),
    "dictate": os.environ.get("NOTHAL_HOTKEY_DICTATE", "ctrl+alt+2"),
}

# Which setting names each door's combo (spec/70). The schema holds the shipped default, so a
# fresh profile already reads "ctrl+alt+1" from here rather than from the dict above.
_SETTING = {"ask": "hotkey_ask", "dictate": "hotkey_dictate"}

# While the settings window is RECORDING a shortcut, the doors must be unregistered — otherwise
# RegisterHotKey consumes the combo and pressing the one you are trying to rebind opens Ask
# instead of landing in the field. Written as a DEADLINE, not a flag: the window can die between
# pressing Record and releasing the key, and a stale `true` would leave the machine with no
# doors at all until someone found the file. A lease expires on its own.
PAUSE_KEY = "hotkeys_paused_until"
PAUSE_LEASE_S = 30.0


def bindings(now: dict | None = None) -> dict[str, str]:
    """The combo for each door, read FRESH from the user's settings (spec/70).

    A blank setting falls through to `DEFAULT_BINDINGS`, and so does one that will not parse — a
    hand-edited file must not leave the daemon with no way in. Read fresh so the pump can pick up
    a rebind without a restart. `now` is a settings snapshot the pump already holds, so one tick
    costs one file read rather than three.
    """
    now = settings.load() if now is None else now
    out: dict[str, str] = {}
    for name, fallback in DEFAULT_BINDINGS.items():
        combo = str(now.get(_SETTING[name]) or "").strip() or fallback
        try:
            parse_binding(combo)
        except ValueError as exc:
            log.warning("%s binding %r is unusable (%s) — falling back to %s",
                        name, combo, exc, fallback)
            combo = fallback
        out[name] = combo
    return out


def paused(now: dict | None = None) -> bool:
    """Is a shortcut being recorded right now? An unreadable or absent lease reads as not
    paused: failing open here costs a stray hotkey, failing closed costs every door."""
    now = settings.load() if now is None else now
    try:
        return time.time() < float(now.get(PAUSE_KEY) or 0)
    except (TypeError, ValueError):
        return False


def pause(seconds: float = PAUSE_LEASE_S) -> None:
    """Unregister the doors for up to `seconds` — the settings window holds this while its key
    recorder is listening, so the combo reaches the field instead of opening a turn."""
    settings.set(PAUSE_KEY, time.time() + seconds)


def resume() -> None:
    """Hand the doors back, without waiting for the lease to run out."""
    settings.set(PAUSE_KEY, 0)

# Win32 (winuser.h). MOD_NOREPEAT means one message per press — without it, holding the
# key floods the queue and every repeat would read as another tap.
_MOD_ALT, _MOD_CONTROL, _MOD_SHIFT, _MOD_WIN, _MOD_NOREPEAT = 1, 2, 4, 8, 0x4000
_WM_HOTKEY = 0x0312
_PM_REMOVE = 0x0001

_MODS = {"alt": _MOD_ALT, "ctrl": _MOD_CONTROL, "control": _MOD_CONTROL,
         "shift": _MOD_SHIFT, "win": _MOD_WIN, "cmd": _MOD_WIN}
_KEYS = {"space": 0x20, "esc": 0x1B, "escape": 0x1B, "tab": 0x09, "enter": 0x0D,
         **{f"f{i}": 0x6F + i for i in range(1, 13)}}


def parse_binding(combo: str) -> tuple[int, int]:
    """'ctrl+alt+1' -> (modifier mask, virtual-key code). Raises ValueError on anything
    we cannot register, so a bad config line fails loudly at startup rather than
    silently leaving a door unbound. A modifier-less binding is rejected unconditionally:
    every door here is registered for the life of the daemon, and a bare combo held that
    long is swallowed everywhere you type."""
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    mods, key = 0, None
    for p in parts:
        if p in _MODS:
            mods |= _MODS[p]
        elif key is None:
            key = p
        else:
            raise ValueError(f"more than one non-modifier key in binding {combo!r}")
    if key is None:
        raise ValueError(f"no key in binding {combo!r}")
    vk = _KEYS.get(key)
    if vk is None and len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
    if vk is None:
        raise ValueError(f"unknown key {key!r} in binding {combo!r}")
    if not mods:
        # A bare key registers globally and would be swallowed everywhere you type.
        raise ValueError(f"binding {combo!r} needs a modifier (ctrl/alt/shift/win)")
    return mods, vk


class Door:
    """One hotkey and the two signals it produces. The orchestrator polls `start` and
    clears it when it takes the turn; `end` is cleared here on the next press, so a
    double-tap that lands before the orchestrator looks still reads as open-then-close."""

    def __init__(self, name: str, combo: str):
        self.name = name
        self.combo = combo
        self.mods, self.vk = parse_binding(combo)
        self.start = threading.Event()
        self.end = threading.Event()
        self.open = False          # are we between the two taps? see close()

    def close(self) -> None:
        """The capture this door opened has ended — HOWEVER it ended: second tap, hold
        release, VAD (`auto_end`), the no-speech give-up, the 30 s cap, or a dismiss.

        The orchestrator owns when a capture is really over; this door only counts
        presses, and the two must never be left disagreeing. They were: a capture that
        ended without a second press left `open` set, so the next press was read as the
        closing tap — it fired `end`, opened nothing, and the user had to press twice to
        get going again. Called from _capture()'s finally, so no exit path can skip it.

        ponytail: KNOWN RACE (G-06, accepted). This clears `start` unconditionally from the
        orchestrator thread. A press that lands in the sliver between the capture's real end
        and this `finally` running is recorded by `_fire` (`start.set()`) on the pump thread
        and then erased here — one silently lost press. The window is ~ms and self-heals (press
        again), so it is accepted. The real fix is not local to `close()`: it is the Door
        redesign parked in STATE (mechanism vs policy — see there), so noted, not patched."""
        self.open = False
        self.start.clear()
        self.end.clear()


class Hotkeys:
    def __init__(self, combos: dict[str, str] | None = None):
        self.doors = {n: Door(n, c) for n, c in (combos or bindings()).items()}
        self.hold_s = HOLD_S
        self._down = self._key_down
        # None = follow the settings; a dict passed in pins the doors (the CLI, the selfcheck).
        self._pinned = combos

    # --- the tap/hold state machine (pure enough to selfcheck; _down is injectable) ---

    def _fire(self, door: Door) -> None:
        """One WM_HOTKEY on `door`. The hold-vs-tap split is deliberate and is a FEATURE, not
        a quirk to design around: a tap toggles the capture, a hold ≥ HOLD_S is push-to-talk
        that ends on release — like hold-to-crouch vs tap-to-toggle-crouch in a game.

        ponytail: watching for the release busy-polls the message-pump thread, so while ONE
        door is held the OTHER door (dictate vs ask) is deaf until release. Accepted (G-05):
        you don't dictate and ask in the same instant, there is only ever one turn, and D24
        already moved the one key that mattered here (Esc) off this thread to the overlay. The
        real fix — watch the release off the pump thread (a GetAsyncKeyState poll, or fold it
        into the orchestrator's loop) — is worth it only if a third door lands or simultaneous
        doors ever matter."""
        if door.open:                                   # second tap: the endpoint
            door.open = False
            door.end.set()
            log.info("%s: closed (tap)", door.name)
            return
        door.open = True
        door.end.clear()
        door.start.set()
        t0 = time.perf_counter()
        while self._down(door.vk):
            time.sleep(POLL_S)
        if time.perf_counter() - t0 >= self.hold_s:     # push-to-talk: release ends it
            door.open = False
            door.end.set()
            log.info("%s: closed (hold released)", door.name)
        else:
            log.info("%s: open (tap) — tap again to close", door.name)

    @staticmethod
    def _key_down(vk: int) -> bool:
        import ctypes
        return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)

    # --- the daemon ---

    def start(self) -> None:
        """Register the combos and pump their messages on a daemon thread. Never fatal:
        a combo another app already owns logs and leaves that door unbound."""
        if sys.platform != "win32":
            log.warning("hotkeys are Windows-only for now (%s) — the doors will not fire; "
                        "use the wake word for the ask door", sys.platform)
            return
        threading.Thread(target=self._pump, name="nothal-hotkeys", daemon=True).start()

    def reset(self) -> None:
        """Forget every door's in-progress state — for a turn that was abandoned rather
        than finished (dismiss), where any door could be left mid-toggle."""
        for door in self.doors.values():
            door.close()

    def _wanted(self) -> dict[str, str]:
        """The combos that should be registered right now: none while a shortcut is being
        recorded, the pinned set if one was passed to the constructor, else the settings. One
        settings read serves both questions."""
        now = settings.load()
        if paused(now):
            return {}
        return dict(self._pinned) if self._pinned is not None else bindings(now)

    def _register(self, user32, want: dict[str, str]) -> dict[int, Door]:
        """Make the OS registration match `want`, and return the id -> Door map for the pump.

        Doors are MUTATED rather than replaced: the orchestrator holds `hk.doors[name]` and polls
        its events, and a rebind mid-capture must not strand a Door whose `open` flag someone is
        still counting on."""
        for i in range(1, len(self.doors) + 1):
            user32.UnregisterHotKey(None, i)
        by_id: dict[int, Door] = {}
        for i, (name, combo) in enumerate(want.items(), start=1):
            door = self.doors.get(name)
            if door is None:
                door = self.doors[name] = Door(name, combo)
            elif door.combo != combo:
                door.combo = combo
                door.mods, door.vk = parse_binding(combo)
            if user32.RegisterHotKey(None, i, door.mods | _MOD_NOREPEAT, door.vk):
                by_id[i] = door
                log.info("hotkey %s -> %s", combo, name)
            else:
                log.error("could not register %s for %s — another app likely owns it",
                          combo, name)
        if not want:
            log.info("hotkeys released — a shortcut is being recorded")
        return by_id

    def _pump(self) -> None:
        """Register the doors, deliver their messages, and keep both in step with the settings.

        `PeekMessageW` on a tick rather than a blocking `GetMessageW`: the bindings can change
        under us (the settings window writes them) and the doors have to be handed back while a
        shortcut is being recorded, neither of which a thread parked in GetMessage can notice.
        The tick is the same order as the release poll `_fire` already runs at."""
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        msg = wintypes.MSG()
        by_id: dict[int, Door] = {}
        live: dict[str, str] | None = None
        next_sync = 0.0
        while True:
            if time.monotonic() >= next_sync:
                next_sync = time.monotonic() + SYNC_S
                want = self._wanted()
                if want != live:
                    by_id = self._register(user32, want)
                    live = want
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, _PM_REMOVE):
                if msg.message == _WM_HOTKEY and msg.wParam in by_id:
                    self._fire(by_id[msg.wParam])
            time.sleep(POLL_S)


class _FakeUser32:
    """Stands in for user32 so `_register` can be exercised off Windows: it only has to accept
    the two calls and report every registration as taken."""

    def UnregisterHotKey(self, hwnd, i): return True
    def RegisterHotKey(self, hwnd, i, mods, vk): return True


def _selfcheck() -> None:
    """No keyboard, no Windows: binding parsing, the tap/hold machine, and the settings wiring."""
    assert parse_binding("ctrl+alt+1") == (_MOD_CONTROL | _MOD_ALT, 0x31)
    assert parse_binding("CTRL + Alt + d") == (_MOD_CONTROL | _MOD_ALT, 0x44)
    assert parse_binding("win+shift+f5") == (_MOD_WIN | _MOD_SHIFT, 0x74)
    for bad in ("1", "ctrl+", "ctrl+nope", "ctrl+a+b", ""):
        try:
            parse_binding(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{bad!r} should not parse")

    hk = Hotkeys({"ask": "ctrl+alt+1"})
    door = hk.doors["ask"]

    hk._down = lambda vk: False                    # tap: released immediately
    hk._fire(door)
    assert door.start.is_set() and not door.end.is_set() and door.open
    hk._fire(door)                                 # second tap closes it
    assert door.end.is_set() and not door.open

    down = iter([True, False])                     # hold: still down after hold_s
    hk._down = lambda vk: next(down, False)
    hk.hold_s = 0.0
    door.start.clear(); door.end.clear()
    hk._fire(door)
    assert door.start.is_set() and door.end.is_set() and not door.open

    door.start.clear(); door.end.set()             # a press clears a stale endpoint
    hk._down = lambda vk: False
    hk.hold_s = HOLD_S
    hk._fire(door)
    assert door.start.is_set() and not door.end.is_set()

    # D24: no door here may be modifier-less, with no exemption. Esc was the one exception —
    # a "transient" door armed and disarmed against the daemon's guess at what was on screen.
    # The Teleprompter owns Esc now (it is the thing on screen), so a bare binding in THIS
    # module can only mean a key consumed machine-wide for the life of the daemon.
    for bare in ("esc", "escape", "f5", "space"):
        try:
            parse_binding(bare)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{bare!r} must not parse: a bare combo is swallowed globally")
    assert "dismiss" not in DEFAULT_BINDINGS, "dismissal belongs to the overlay (D24)"

    # A capture that ends WITHOUT a second press (dismiss · no-speech give-up · 30 s cap ·
    # auto_end) must not leave the door half-open, or the next press reads as the closing
    # tap: it fires `end`, opens nothing, and the user has to press twice to start again.
    d = Hotkeys({"ask": "ctrl+alt+1"}).doors["ask"]
    hk3 = Hotkeys({"ask": "ctrl+alt+1"})
    hk3._down = lambda vk: False
    hk3.doors["ask"] = d
    hk3._fire(d)                                   # tap: capture opens
    assert d.open and d.start.is_set()
    d.close()                                      # ...and ends some other way
    assert not d.open and not d.start.is_set() and not d.end.is_set()
    d.start.clear()
    hk3._fire(d)                                   # the NEXT press must OPEN, not close
    assert d.start.is_set(), "press after a non-tap capture end must open a new capture"
    assert not d.end.is_set(), "press after a non-tap capture end must not fire the endpoint"

    hk3.reset()                                    # abandon (dismiss) clears every door
    assert not d.open and not d.start.is_set() and not d.end.is_set()

    # --- bindings come from the SETTINGS (spec/70), which is what makes the recorder bite.
    # They were read from the env and a literal, so rebinding in the window changed nothing.
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["NOTHAL_SETTINGS"] = str(Path(tmp) / "s.json")
        assert bindings() == {"ask": "ctrl+alt+1", "dictate": "ctrl+alt+2"}, bindings()
        settings.set("hotkey_ask", "ctrl+shift+a")
        assert bindings()["ask"] == "ctrl+shift+a", "the setting must win"
        assert bindings()["dictate"] == "ctrl+alt+2", "one door's rebind must not move the other"
        assert Hotkeys().doors["ask"].combo == "ctrl+shift+a", "a fresh Hotkeys reads the setting"
        # A blank or unusable value falls back rather than leaving the daemon with no way in.
        settings.set("hotkey_ask", "   ")
        assert bindings()["ask"] == DEFAULT_BINDINGS["ask"], "blank -> the fallback"
        settings.set("hotkey_ask", "ctrl+nope")
        assert bindings()["ask"] == DEFAULT_BINDINGS["ask"], "an unparseable binding must not raise"
        settings.set("hotkey_ask", "ctrl+alt+1")

        # The recording lease. While it is held, NOTHING is registered — that is the whole point:
        # RegisterHotKey consumes the combo machine-wide, so the shortcut being rebound would
        # otherwise open a turn instead of reaching the settings field.
        hk4 = Hotkeys()
        assert not paused() and hk4._wanted() == bindings(), "at rest every door is wanted"
        pause()
        assert paused() and hk4._wanted() == {}, "recording must release every door"
        resume()
        assert not paused() and hk4._wanted() == bindings(), "resume must hand them back"
        # A LEASE, not a flag: a window that dies mid-recording must not cost the doors forever.
        settings.set(PAUSE_KEY, time.time() - 1)
        assert not paused(), "an expired lease must release itself"
        settings.set(PAUSE_KEY, "nonsense")
        assert not paused(), "an unreadable lease fails OPEN — a stray hotkey beats no hotkeys"

        # A rebind MUTATES the door the orchestrator is holding rather than replacing it, so a
        # capture in flight keeps the object whose events it is polling.
        hk5 = Hotkeys()
        was = hk5.doors["ask"]
        hk5._register(_FakeUser32(), {"ask": "ctrl+shift+k"})
        assert hk5.doors["ask"] is was, "a rebind must not swap the Door out from under a capture"
        assert was.combo == "ctrl+shift+k" and (was.mods, was.vk) == parse_binding("ctrl+shift+k")
    os.environ.pop("NOTHAL_SETTINGS", None)

    print("selfcheck OK: binding parsing (no bare combos), tap-toggle, hold-PTT, "
          "stale-end clearing, bindings from settings with a safe fallback, "
          "and the recording lease releasing the doors (and expiring on its own)")


def main() -> None:
    setup_logging()
    ap = argparse.ArgumentParser(description="not-hal hotkeys — the two doors (D20)")
    ap.add_argument("--selfcheck", action="store_true",
                    help="verify parsing and the tap/hold machine without a keyboard, then exit")
    args = ap.parse_args()
    if args.selfcheck:
        _selfcheck()
        return
    hk = Hotkeys()
    hk.start()
    print("press " + " or ".join(d.combo for d in hk.doors.values()) + " (Ctrl-C to stop)")
    try:
        while True:                                 # watch the doors and report
            for door in hk.doors.values():
                if door.start.is_set():
                    door.start.clear()
                    print(f"[{door.name}] capture opened")
                if door.end.is_set():
                    door.end.clear()
                    print(f"[{door.name}] capture closed")
            time.sleep(POLL_S)
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
