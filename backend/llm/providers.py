"""The provider catalogue — one lookup layer shared by every adapter and by the settings window.

The catalogue itself is `shared/schemas/settings.json` -> `providers`: which
providers exist, where each runs, how it authenticates, which credential-store entry holds its
key, which env var stands in for that key, **which wire protocol serves it** (`wire`) and its
API base URL (`api`). Nothing here restates any of it — this module only reads it.

Two wires cover all eleven providers:

  `anthropic`  Anthropic's own Messages API, served by B1 (`claude.py`).
  `openai`     everything else, served by B2 (`compat.py`) — Groq, OpenAI, xAI, DeepSeek,
               Mistral, OpenRouter and Google's compat layer in the cloud; Ollama, LM Studio
               and llama.cpp locally, which all expose an OpenAI-compatible `/v1`.

Deliberately NOT here: choosing which provider to use. That is the router,
which is still unbuilt and out of scope — this module answers "how do I reach X", never
"should I use X".
"""
from __future__ import annotations

import logging
import os

from shared.settings import schema

log = logging.getLogger("nothal.llm")

# How long the settings window is willing to wait on a provider's model list. Short on purpose:
# it runs off a background thread, but a user staring at a spinner is the real budget.
FETCH_TIMEOUT_S = 6.0

# Every outcome `probe()` can report. Closed set: the settings window turns each into a sentence
# for the user (SettingsWindow.qml `addProbeMessage`), so a new one added here without a sentence
# there shows the user nothing. The selfcheck pins this list for exactly that reason.
PROBE_STATUSES = ("ok", "nokey", "auth", "unreachable", "empty", "error")


def catalog() -> dict[str, dict]:
    """Every provider card, keyed by id."""
    return schema()["providers"]


def card(pid: str) -> dict:
    """One provider's card, or {} for an id that is not in the catalogue."""
    return catalog().get(pid, {})


def wire(pid: str) -> str:
    """Which wire protocol serves this provider: 'anthropic' or 'openai'."""
    return card(pid).get("wire", "")


def wire_names(pid: str) -> dict[str, str | None]:
    """What THIS provider calls each of the app's request knobs.

    The app names a knob once — `max_output_tokens`, `effort`, `temperature` — and the catalogue
    says how it is spelled on the way out. The wire supplies the default (it belongs to the
    protocol, not to any one provider) and the card overrides only what it spells differently,
    so ten providers sharing a dialect share one entry and the odd one out is a two-line card
    edit rather than a branch in the adapter.

    A `None` value means the provider HAS NO such knob and it is dropped deliberately; a name
    missing altogether is a gap in the schema, which the adapter logs rather than guessing at.
    """
    names: dict[str, str | None] = dict(schema().get("wire_names", {}).get(wire(pid), {}))
    names.update(card(pid).get("wire_names", {}) or {})
    return names


def base_url(pid: str, endpoint: str | None = None) -> str:
    """The provider's OpenAI-compatible base URL.

    Cloud providers declare it outright (`api`), because the hosts genuinely differ. Local
    runners declare a user-editable `host:port` instead and the URL is built from it — `/v1`
    is the OpenAI-compat convention shared by Ollama, LM Studio and llama.cpp alike, not a
    per-provider value, so composing it here restates nothing. `endpoint` overrides the
    catalogue default, which is what the settings entry stores when the user moves a port.

    A blank `endpoint` falls back to the catalogue default deliberately: clearing the field in
    the settings window should restore the standard port, not produce a URL that cannot resolve.

    `localhost` is rewritten to `127.0.0.1` — measured 2026-07-31, and worth more than it looks.
    `localhost` resolves to IPv6 `::1` first, but every local runner we support binds IPv4 by
    default, so the IPv6 attempt is always wasted: a refused connection took 4040 ms via
    `localhost` against 2025 ms via `127.0.0.1`. The rewrite lives HERE, not only in the
    catalogue default, so it also repairs endpoints already stored in a user's settings and one
    typed by hand. Only the bare word is rewritten; an explicit `::1` is left alone for anyone
    who means it.
    """
    c = card(pid)
    if c.get("auth") == "endpoint":
        # Strip BEFORE falling back, so a field holding only spaces counts as cleared.
        host = ((endpoint or "").strip() or (c.get("endpoint") or "").strip()).rstrip("/")
        if not host:
            return ""
        if "://" not in host:
            host = f"http://{host}"
        host = host.replace("://localhost:", "://127.0.0.1:", 1)
        if host.endswith("://localhost"):
            host = host[: -len("localhost")] + "127.0.0.1"
        return host if host.endswith("/v1") else f"{host}/v1"
    return c.get("api", "")


# Local servers THIS process started, pid -> Popen. Only these may ever be stopped: one that was
# already running belongs to someone else and may be doing their work (see stop_local_servers).
_spawned: dict[str, object] = {}

DEFAULT_KEEP_ALIVE = "30m"      # Ollama's own default is 5m — too short for intermittent dictation,
                                # which then pays an 8-12 s model load on the first turn after a gap.
_SERVER_READY_S = 10.0          # how long to wait for a freshly spawned server to answer


def _listening(pid: str, endpoint: str | None = None) -> bool:
    """Is something already serving this provider's endpoint?"""
    import socket
    from urllib.parse import urlparse
    url = base_url(pid, endpoint)
    if not url:
        return False
    u = urlparse(url)
    if not u.hostname:
        return False
    try:
        socket.create_connection((u.hostname, u.port or 80), timeout=1.0).close()
        return True
    except OSError:
        return False


def ensure_local_server(pid: str, endpoint: str | None = None,
                        keep_alive: str | None = None) -> bool:
    """Start this provider's server if it declares one and nothing is listening.

    Returns True only if WE started it — the caller needs that to know what it may stop later.

    The argv comes from the card's `serve`, so no adapter knows a binary name and a
    provider that declares none is simply never started. Windows gets CREATE_NO_WINDOW: the point
    of the exercise is a server with no console and no tray (`ollama app.exe` is the tray,
    `ollama.exe serve` is the server).

    `keep_alive` rides in the ENVIRONMENT because it cannot be sent per-request: Ollama ignores
    `keep_alive` on the OpenAI-compatible `/v1`, exactly as it ignores `think` and `num_ctx`
    (all three tested 2026-08-02, v0.32.5). So it governs a server we start and cannot reach one
    the user started themselves — a real limit, stated in the setting's help text.
    """
    import os
    import shutil
    import subprocess
    import time

    argv = card(pid).get("serve")
    if not argv or _listening(pid, endpoint):
        return False
    exe = shutil.which(argv[0])
    if not exe:
        log.info("%s declares a server but %r is not installed — leaving it to the user",
                 pid, argv[0])
        return False

    env = {**os.environ, "OLLAMA_KEEP_ALIVE": keep_alive or DEFAULT_KEEP_ALIVE}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)          # 0 off Windows
    log.info("starting %s headless (%s), keep-alive %s", pid, exe, env["OLLAMA_KEEP_ALIVE"])
    proc = subprocess.Popen([exe, *argv[1:]], env=env, creationflags=flags,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + _SERVER_READY_S
    while time.monotonic() < deadline:
        if _listening(pid, endpoint):
            _spawned[pid] = proc
            return True
        if proc.poll() is not None:                             # died on its own
            log.warning("%s server exited immediately (code %s)", pid, proc.returncode)
            return False
        time.sleep(0.2)
    log.warning("%s server did not answer within %.0f s — leaving it running", pid, _SERVER_READY_S)
    _spawned[pid] = proc                                        # ours regardless; still ours to stop
    return True


def stop_local_servers() -> None:
    """Stop the local servers THIS process started, and only those.

    A server that was already up when we arrived is never in `_spawned`, so it is never touched —
    it may be doing someone else's work, which is why that is a rule and not a setting. The
    setting (`local_server_stop_on_quit`) governs only whether we stop OUR OWN."""
    import subprocess
    while _spawned:
        pid, proc = _spawned.popitem()
        if proc.poll() is not None:                             # already gone
            continue
        log.info("stopping the %s server we started", pid)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def credential_for(pid: str) -> str | None:
    """A provider's API key: OS credential store first (service 'not-hal'),
    then the env var the card names.

    Both names come from the catalogue — the account name from `credential` and the fallback
    variable from `env` — so adding a provider is still a JSON edit and nothing else. Local
    runners authenticate by endpoint and have no key; they return None and the compat adapter
    sends a placeholder, because OpenAI's own client requires the header to exist.
    """
    c = card(pid)
    if c.get("auth") != "key":
        return None
    try:
        import keyring

        key = keyring.get_password(KEY_SERVICE, c["credential"])
        if key:
            return key
    except Exception as e:                        # a locked/broken backend must not be fatal
        log.warning("credential store unreadable for %s: %s", pid, e)
    env = c.get("env")
    return os.environ.get(env) if env else None


def _chat_only(ids: list[str]) -> list[str]:
    """Drop the ids that cannot serve a turn, and sort what remains.

    `GET /models` returns everything an account can reach, which includes speech-to-text,
    text-to-speech, embeddings, image models and safety classifiers — 15 ids for Groq, 129 for
    OpenAI (measured 2026-07-24). The substrings come from the schema's `not_chat`
    and are matched conservatively: anything ambiguous stays in the list, because hiding a model
    the user wanted is worse than showing one they don't.
    """
    bad = schema().get("not_chat", [])
    # Case-insensitive: provider ids mix case (`meta-llama/…`, `Qwen/…`) and a plain sort would
    # file every capitalised vendor above every lowercase one.
    return sorted((m for m in ids if not any(b in m.lower() for b in bad)), key=str.lower)


def probe(
    pid: str,
    endpoint: str | None = None,
    timeout: float = FETCH_TIMEOUT_S,
    key: str | None = None,
) -> tuple[list[str], str]:
    """Ask a provider what models it has. Returns `(ids, status)` and NEVER raises.

    `key` tests a CANDIDATE credential instead of the stored one, and is what the settings
    window's Test button passes: in the Add flow the typed key has not been saved yet (it goes
    to the credential store only on commit), so probing the store would test the wrong thing —
    or nothing at all. A candidate key is used for this call and never written anywhere.

    The status is the whole reason this returns a pair rather than just a list: fetching the
    model list is also the cheapest honest test of a stored key, and "no models" has several
    causes a user needs told apart —

      `ok`           the list came back
      `nokey`        nothing in the credential store for a provider that needs one
      `auth`         the provider rejected the key (401/403) — the key is wrong or revoked
      `unreachable`  no answer: offline, or a local runner that isn't running
      `empty`        the provider answered, but with nothing this account can talk to
      `error`        anything else (a 404 on a bad endpoint path, unparseable JSON)

    Swallowing all of these into `[]` leaves a wrong key and a
    down network looking identical, and leaves the settings window unable to say either.

    Anthropic goes through its SDK (`models.list()`) so the `anthropic-version` protocol
    constant stays the SDK's business. Every other provider answers `GET {base}/models`,
    including the three local runners, so there is one HTTP shape here and not nine.
    """
    import httpx

    if not wire(pid):
        return [], "error"
    needs_key = card(pid).get("auth") == "key"
    key = (key or "").strip() or credential_for(pid)
    if needs_key and not key:
        return [], "nokey"

    try:
        if wire(pid) == "anthropic":
            import anthropic

            client = anthropic.Anthropic(api_key=key, timeout=timeout)
            found = _chat_only([m.id for m in client.models.list(limit=100).data])
        else:
            url = base_url(pid, endpoint)
            if not url:
                return [], "error"
            headers = {"Authorization": f"Bearer {key}"} if key else {}
            r = httpx.get(f"{url}/models", headers=headers, timeout=timeout)
            r.raise_for_status()
            data = r.json().get("data") or []
            found = _chat_only([m["id"] for m in data if isinstance(m, dict) and m.get("id")])
        return found, ("ok" if found else "empty")
    except Exception as e:
        status = _probe_status(e)
        log.info("model probe for %s: %s (%s)", pid, status, e)
        return [], status


def _probe_status(exc: Exception) -> str:
    """Classify a failed probe by exception TYPE and status code, never message prose — the same
    rule the adapters map errors by. Both SDKs sit on httpx, so one ladder covers them."""
    import httpx

    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    if status in (401, 403):
        return "auth"
    if status is not None and status >= 500:
        return "unreachable"
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout)):
        return "unreachable"
    if isinstance(exc, httpx.HTTPError):
        return "error"
    return "error"


def list_models(pid: str, endpoint: str | None = None, timeout: float = FETCH_TIMEOUT_S) -> list[str]:
    """The provider's live model ids, or [] if they cannot be fetched. See `probe` for why."""
    return probe(pid, endpoint, timeout)[0]


def build_model(provider: str, model: str | None = None, endpoint: str | None = None,
                effort: str | None = None, temperature: float | None = None):
    """Construct the adapter that serves `provider`, chosen by its `wire`: B1 (ClaudeModel) for
    the anthropic wire, B2 (CompatModel) for the OpenAI wire that the other ten share. `effort` is
    the card's reasoning-effort dial and `temperature` its sampling dial, both passed to the OpenAI
    wire (which sends each only where the provider declares the capability — only local providers
    declare temperature today).

    This is adapter CONSTRUCTION, not routing: it builds the model you name. Deciding *which*
    provider to use — the primary, per-role selection — is the router (`router.py`).
    Imports are local to dodge the providers <-> adapters import cycle (both adapters import from
    this module at load time).
    """
    if wire(provider) == "anthropic":
        from .claude import ClaudeModel

        # ponytail: Claude's effort/extended-thinking are not wired into B1 yet (persona/output
        # work), and Anthropic declares no temperature capability; both are ignored here rather than
        # faked. B2 below honours them.
        return ClaudeModel(model=model)
    from .compat import CompatModel

    return CompatModel(provider, model=model, endpoint=endpoint, effort=effort,
                       temperature=temperature)


if __name__ == "__main__":
    # ponytail: runnable check of the lookups — the logic worth guarding is URL composition and
    # the credential/env fallback. No network: list_models is exercised only for its [] contract.
    cat = catalog()
    assert cat, "the provider catalogue must load from shared/schemas/settings.json"

    # Every card must declare a wire this code can actually serve, and cloud cards must carry
    # both an API base and an env fallback — otherwise the adapter has no way to reach them.
    for pid, c in cat.items():
        assert c.get("wire") in ("anthropic", "openai"), f"{pid}: unknown wire {c.get('wire')!r}"
        if c.get("auth") == "key":
            assert c.get("api", "").startswith("https://"), f"{pid}: cloud card needs an https api"
            assert c.get("env"), f"{pid}: cloud card needs an env fallback name"
        else:
            assert c.get("endpoint"), f"{pid}: a local runner must declare host:port"

    assert wire("anthropic") == "anthropic", "B1 keeps its native wire"
    assert wire("groq") == "openai"
    assert base_url("groq") == cat["groq"]["api"], "a cloud base URL is taken verbatim"

    # Local URL composition: bare host:port, an explicit scheme, and an already-suffixed URL
    # must all land on exactly one /v1.
    assert base_url("ollama") == "http://127.0.0.1:11434/v1", base_url("ollama")
    assert base_url("ollama", "127.0.0.1:9999") == "http://127.0.0.1:9999/v1"
    assert base_url("ollama", "https://box.lan:443") == "https://box.lan:443/v1"
    assert base_url("ollama", "http://x:1/v1") == "http://x:1/v1", "must not double the suffix"
    # `localhost` is rewritten wherever it arrives — catalogue default, stored setting, or typed
    # by hand — because the wasted IPv6 attempt costs ~2 s per connection (see base_url).
    assert base_url("ollama", "  localhost:1234/  ") == "http://127.0.0.1:1234/v1"
    assert base_url("ollama", "http://localhost:11434/v1") == "http://127.0.0.1:11434/v1"
    assert base_url("ollama", "::1:11434") == "http://::1:11434/v1", "an explicit ::1 is honoured"
    assert base_url("ollama", "localhost.lan:80") == "http://localhost.lan:80/v1", \
        "only the bare host is rewritten, never a name that merely starts with it"
    # A cleared field restores the catalogue default rather than yielding an unresolvable URL.
    assert base_url("ollama", "") == base_url("ollama") == "http://127.0.0.1:11434/v1"
    assert base_url("ollama", "   ") == "http://127.0.0.1:11434/v1", "whitespace is still blank"
    assert base_url("nosuch") == "", "an unknown provider resolves to nothing, never raises"

    # --- local server lifetime. THE rule: only a server WE started may ever be stopped, because
    # one that was already running belongs to someone else. Tested with no processes at all.
    assert not _spawned, "the registry must start empty"
    assert card("ollama").get("serve"), "ollama must declare how to start itself"
    for pid in ("groq", "openai", "anthropic"):
        assert not card(pid).get("serve"), f"{pid} is a cloud provider and must declare no server"
    # A provider that declares nothing is never started, whatever the port says.
    assert ensure_local_server("groq") is False, "a card with no `serve` must never be started"
    assert ensure_local_server("nosuch") is False, "an unknown provider must not raise"

    class _Fake:                                   # stands in for a Popen we own
        def __init__(self): self.stopped = False
        def poll(self): return None
        def terminate(self): self.stopped = True
        def wait(self, timeout=None): return 0

    ours = _Fake()
    _spawned["ollama"] = ours
    stop_local_servers()
    assert ours.stopped, "a server we started must be stopped"
    assert not _spawned, "stopping must clear the registry"
    # ...and the registry is the ONLY thing consulted: nothing else can be stopped, because a
    # server we did not start never enters it. Calling again on an empty registry is a no-op.
    stop_local_servers()
    assert not _spawned

    # A local runner has no key by design; asking for one must not invent a placeholder here.
    assert credential_for("ollama") is None, "endpoint auth carries no credential"
    # The env fallback is read through the card's `env` name, never a literal in this file.
    env_pid = "openrouter"
    os.environ[cat[env_pid]["env"]] = "sentinel"
    try:
        import keyring                            # noqa: F401  - present in this project

        stored = None
        try:
            stored = keyring.get_password(KEY_SERVICE, cat[env_pid]["credential"])
        except Exception:
            pass
        if not stored:
            assert credential_for(env_pid) == "sentinel", "env must stand in when nothing is stored"
    except ImportError:
        assert credential_for(env_pid) == "sentinel"
    os.environ.pop(cat[env_pid]["env"], None)

    assert list_models("nosuch") == [], "an unknown provider fetches nothing and never raises"
    assert list_models("ollama", "127.0.0.1:1") == [], "a dead local runner returns [], not a crash"

    # The probe's STATUS is what lets the settings window tell a wrong key from a dead network —
    # the distinction the first cut lost by returning [] for both.
    import httpx

    assert probe("nosuch")[1] == "error", "an unknown provider is a programming fault, not a 401"
    ids, why = probe("ollama", "127.0.0.1:1", timeout=2.0)
    assert (ids, why) == ([], "unreachable"), (ids, why)

    def _http(status):
        return httpx.HTTPStatusError(
            "x", request=httpx.Request("GET", "http://x"),
            response=httpx.Response(status, request=httpx.Request("GET", "http://x")))

    assert _probe_status(_http(401)) == "auth", "a rejected key must be nameable as such"
    assert _probe_status(_http(403)) == "auth"
    assert _probe_status(_http(404)) == "error", "a bad path is not a bad key"
    assert _probe_status(_http(503)) == "unreachable"
    assert _probe_status(httpx.ConnectError("refused")) == "unreachable"
    assert _probe_status(httpx.ReadTimeout("slow")) == "unreachable"
    assert _probe_status(RuntimeError("boom")) == "error"

    # The status vocabulary is CLOSED. Two readers phrase these for the user —
    # SettingsWindow.qml's `addProbeMessage` switch and settings_model.modelState's docstring —
    # and neither can be checked from here, so adding a status must break this line first.
    assert PROBE_STATUSES == ("ok", "nokey", "auth", "unreachable", "empty", "error")
    for exc in (_http(401), _http(404), _http(503), httpx.ConnectError("x"), RuntimeError("x")):
        assert _probe_status(exc) in PROBE_STATUSES, exc
    # A cloud provider with no stored key must say so rather than looking like an outage.
    absent = next((p for p, c in cat.items()
                   if c.get("auth") == "key" and not credential_for(p)), None)
    if absent:
        assert probe(absent)[1] == "nokey", absent

    # The non-chat filter, against ids really returned by Groq and OpenAI on 2026-07-24. A model
    # picker offering an STT or embedding model hands the user a turn that can only fail.
    assert schema().get("not_chat"), "the non-chat substrings must come from the schema"
    kept = _chat_only([
        "llama-3.3-70b-versatile", "whisper-large-v3-turbo", "openai/gpt-oss-safeguard-20b",
        "canopylabs/orpheus-v1-english", "meta-llama/llama-prompt-guard-2-22m",
        "text-embedding-ada-002", "tts-1", "dall-e-3", "gpt-4o", "openai/gpt-oss-120b",
    ])
    assert kept == ["gpt-4o", "llama-3.3-70b-versatile", "openai/gpt-oss-120b"], kept
    assert _chat_only(["B-model", "a-model"]) == ["a-model", "B-model"], "sorted for a stable picker"
    assert _chat_only([]) == []

    served = [p for p, c in cat.items() if c.get("adapter")]
    print(f"providers selfcheck OK: {len(cat)} cards, {len(served)} with an adapter "
          f"({', '.join(sorted(served))})")
