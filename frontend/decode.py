"""Contract P (shared/schemas/status.json) — the wire half of the Teleprompter, Qt-free.

NDJSON framing plus the reducer that turns a message stream into what the island shows.
This module deliberately imports nothing from `backend/` and nothing from Qt: the front-end
depends on the *wire*, never on the daemon, so a future non-Python back-end drops in
unchanged (spec/00 D21) — and the fiddly logic stays testable in CI, where PySide6 isn't
installed.

Run:
    python -m frontend.decode --selfcheck   # no Qt, no sockets: framing + reducer
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

log = logging.getLogger("nothal.teleprompter")


@lru_cache(maxsize=1)
def status_schema() -> dict:
    """shared/schemas/status.json — the executable contract (hard rule 3), read straight from
    the repo rather than via shared.config, so the front-end stays decoupled from the daemon."""
    root = Path(__file__).resolve().parent.parent
    return json.loads((root / "shared" / "schemas" / "status.json").read_text(encoding="utf-8"))


# Contract P transport (spec/00 D19). LOADED from status.json, not restated — the daemon
# (backend/broadcaster.py) reads the same key and honours the same env override, so the two
# sides cannot disagree about where the feed lives. The port used to sit in three places while
# only the daemon read the env var, so setting it moved the daemon and left the overlay
# reconnecting to the old port forever, silently.
def status_host() -> str:
    return status_schema()["transport"]["host"]


def status_port() -> int:
    t = status_schema()["transport"]
    return int(os.environ.get(t["portEnv"], t["port"]))


HOST = status_host()
PORT = status_port()


@lru_cache(maxsize=1)
def targets() -> dict:
    """shared/schemas/targets.json — the latency targets (hard rule 3), read straight from the
    repo like status_schema() so the front-end stays decoupled from the daemon. The overlay's
    readout quotes these instead of hardcoding 1500/4000, which used to live in four places."""
    root = Path(__file__).resolve().parent.parent
    raw = (root / "shared" / "schemas" / "targets.json").read_text(encoding="utf-8")
    return json.loads(raw)["targets"]


@lru_cache(maxsize=1)
def known_types() -> frozenset[str]:
    """The 'type' consts the schema defines. Anything else is ignored (protocol: log once)."""
    return frozenset(d["properties"]["type"]["const"]
                     for d in status_schema()["$defs"].values())


@lru_cache(maxsize=1)
def upstream_types() -> frozenset[str]:
    """The message types that travel overlay->orchestrator (D24). Contract P is otherwise
    one-way; today this is exactly {'dismiss'}."""
    return frozenset(status_schema()["upstream"])


@lru_cache(maxsize=1)
def downstream_types() -> frozenset[str]:
    """What a SUBSCRIBER may legitimately receive. An upstream type arriving on the feed is
    the daemon echoing our own verb back at us — a bug, not a message."""
    return known_types() - upstream_types()


def m_dismiss() -> dict:
    """The one upstream message (D24). Lives here rather than in backend/broadcaster.py's m_*
    helpers because the overlay is its only sender, and the front-end imports nothing from
    backend/ (D21)."""
    return {"type": "dismiss"}


class Decoder:
    """Bytes -> Contract-P messages. Holds the partial trailing line between reads, and
    drops malformed lines / unknown types, logging each kind once (status.json protocol)."""

    def __init__(self) -> None:
        self._buf = b""
        self._warned: set[str] = set()

    def reset(self) -> None:
        """Drop the partial trailing line — call at the START of a new connection, so a remnant
        left by a connection that died mid-line is not glued onto the next stream's first
        message (P-03). Keeps `_warned`: 'log each malformed kind once' stays per-process, not
        per-connection."""
        self._buf = b""

    def feed(self, data: bytes) -> list[dict]:
        self._buf += data
        out: list[dict] = []
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                self._warn("malformed", "ignoring malformed feed line")
                continue
            if not isinstance(msg, dict):
                self._warn("nonobject", "ignoring non-object feed message")
                continue
            if msg.get("type") not in downstream_types():
                self._warn(f"type:{msg.get('type')}",
                           f"ignoring unknown message type {msg.get('type')!r}")
                continue
            out.append(msg)
        return out

    def _warn(self, key: str, message: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            log.warning(message)


# Which 'state' values clear the previous turn's reply and fault. NOT a literal here: the rule
# is data in status.json (hard rule 3), because it is a fact about the contract that the schema
# and this reducer had already drifted apart on once — the schema taught the exact opposite of
# the code for a day.
#
# 'speaking'/'error' must never clear: the reply streams in during THINKING and the island flips
# to SPEAKING while it is read, so clearing there would blank the text as the user starts reading.
#
# 'listening' DOES clear (D24), and that is only sound because of a precondition worth stating:
# **every capture window is now user-initiated.** spec/50 rule 4 binds `listening` to "audio is
# being captured", so it fires whenever the mic opens — which stopped being the same thing as
# "a new turn began" during the 8 s follow-up window, and a long answer was wiped milliseconds
# after arriving by the mic opening behind it. The follow-up window is gone; wake-watch and
# barge-in monitoring publish no 'listening' at all. IF A NON-TURN CAPTURE WINDOW IS EVER
# REINTRODUCED, THIS LIST MUST CHANGE BACK before it lands — it is one array in status.json.
#
# 'idle' is deliberately absent (D24): it means the daemon has finished, not that the island
# must blank. The overlay decides when to stop showing the answer, since only it knows how much
# text is left to reveal.
@lru_cache(maxsize=1)
def clears_turn() -> frozenset[str]:
    return frozenset(status_schema()["clearsTurn"])


# Prior prompts. RAM only — spec/50 forbids writing any of this to disk.
# NOTHING RENDERS THIS TODAY, deliberately: the ⌄ handle that used to show it was cut (D22),
# and its replacement — the expanded view — is not built yet. Kept because that view is the
# agreed home for prior prompts, and because collecting them is ~4 lines with a test, whereas
# reconstructing a session's prompts after the fact is impossible (nothing is on disk).
# ponytail: a flat cap, not a ring buffer; the process is short-lived.
HISTORY_MAX = 50


@dataclass
class OverlayState:
    """What the island is currently showing. Fed by apply(); rendered by the QML layer."""

    state: str = "idle"
    transcript: str = ""
    reply: str = ""
    done: bool = False
    mic: float = 0.0
    error: str = ""
    kind: str = ""
    # The Contract-T tool running right now, named for a person, or "" between calls (D38).
    tool: str = ""
    # The model that produced the reply + the turn's total tokens (D34) — stamped on the 'done'
    # response message, shown in the peek footer.
    model: str = ""
    tokens: int = 0
    # Which KIND of turn this was, so the island knows how long to leave it up (D43): "quick" for
    # one that acted, "slow" for one that answered. Never a duration — the seconds are the user's
    # setting. "slow" at rest, so anything unstamped keeps the readable dwell.
    dwell: str = "slow"
    # Per-turn instrument readings (spec/40 targets: feedback < 1500 ms, first word < 4000 ms).
    # status.json calls these "not user-facing chrome by default", so the overlay only shows
    # them behind a toggle — but they're shown on screen for the M0 acceptance run.
    feedback_ms: float = 0.0
    first_word_ms: float = 0.0
    # Survives the turn clear deliberately: the turn ends, the session's prompts do not.
    history: list = field(default_factory=list)

    def clear_turn(self) -> None:
        """Forget the current turn — prompt, reply, fault, instrument readings. The session's
        prompt history deliberately survives (it belongs to the session, not the turn)."""
        self.transcript = self.reply = self.error = self.kind = self.model = ""
        self.tool = ""
        self.done = False
        self.tokens = 0
        self.dwell = "slow"
        self.feedback_ms = self.first_word_ms = 0.0

    def apply(self, msg: dict) -> None:
        # A type-valid message with a required field MISSING must be ignored, never raise. The
        # Decoder validates the message TYPE only (feed()), so a well-typed message with fields
        # absent CAN reach here — from a future producer, or a non-not-hal process that squats the
        # port (broadcaster does not set SO_REUSEADDR on Windows). A raise here aborts the whole
        # read in feed._on_ready, dropping every later message in that batch and leaving the mic
        # timer unarmed. So each branch reads with .get and skips a malformed field rather than
        # indexing it. Booleans are read STRICTLY (`is True`): a stray "done":"false" is a truthy
        # string, and truthiness there would clear a live indicator the wire says is still running.
        t = msg.get("type")
        if t == "state":
            st = msg.get("state")
            if not isinstance(st, str) or not st:
                return
            self.state = st
            if self.state in clears_turn():
                self.clear_turn()
            if self.state != "listening":
                self.mic = 0.0          # bars fall the moment the capture window closes
        elif t == "transcript":
            text = msg.get("text")
            if not isinstance(text, str):
                return
            self.transcript = text
            # Only settled prompts join the history; partials (streaming STT, deferred) would
            # otherwise pile up one entry per keystroke-equivalent.
            if msg.get("final", True) and text:
                self.history.append(text)
                del self.history[:-HISTORY_MAX]
        elif t == "response":
            self.reply += str(msg.get("delta", "") or "")
            self.done = msg.get("done") is True
            if msg.get("model"):
                self.model = str(msg["model"])   # stamped on the 'done' message (D34)
            if msg.get("tokens") is not None:
                try:
                    self.tokens = int(msg["tokens"])
                except (TypeError, ValueError):
                    pass
            # Only the two words the schema allows are honoured; anything else leaves the
            # readable dwell in place. A malformed hint must not be able to blink an answer away.
            if msg.get("dwell") in ("quick", "slow"):
                self.dwell = str(msg["dwell"])
        elif t == "mic":
            try:
                self.mic = float(msg.get("level"))
            except (TypeError, ValueError):
                self.mic = 0.0
        elif t == "tool":
            # The tool currently running, or "" between calls (D38). Latched on the start
            # message and cleared by its own `done`, rather than by the next state change: a
            # label that outlived the work would claim not-hal was reading your mail when it was
            # not, which is the one thing this indicator exists to get right.
            self.tool = "" if msg.get("done") is True else (msg.get("label") or msg.get("name") or "")
        elif t == "error":
            self.error = str(msg.get("message", "") or "")
            self.kind = msg.get("kind", "unknown")
        elif t == "latency":
            try:
                ms = float(msg.get("ms"))
            except (TypeError, ValueError):
                return
            if msg.get("metric") == "feedback":
                self.feedback_ms = ms
            elif msg.get("metric") == "first_word":
                self.first_word_ms = ms


def _selfcheck() -> None:
    """No Qt, no sockets: prove the framing survives arbitrary chunking and that the reducer
    keeps the reply on screen while it is spoken."""
    assert known_types() == {"state", "transcript", "response", "mic", "tool", "latency", "error",
                             "dismiss"}, sorted(known_types())
    # Contract P is one-way but for a single verb (D24). A subscriber must not accept it back.
    assert upstream_types() == {"dismiss"}, sorted(upstream_types())
    assert "dismiss" not in downstream_types()
    assert m_dismiss()["type"] == "dismiss"
    assert Decoder().feed(b'{"type":"dismiss"}\n') == [], "the feed must not deliver upstream verbs"
    # The clearing rule is loaded, not restated — the drift this exists to prevent (S-01) was
    # the schema and this reducer teaching opposite rules for a day.
    assert clears_turn() == {"listening", "thinking"}, sorted(clears_turn())
    assert "idle" not in clears_turn(), "D24: idle means the daemon is free, not blank the island"
    # `booting` (status.json v0.7.0) is a real state that does NOT clear a turn — it only ever
    # precedes the first turn, so there is nothing on screen to clear.
    _states = status_schema()["$defs"]["state"]["properties"]["state"]["enum"]
    assert "booting" in _states, "status.json must declare the booting state (v0.7.0)"
    assert "booting" not in clears_turn(), "booting must not clear a turn"
    _boot = OverlayState()
    _boot.apply({"type": "state", "state": "booting"})
    assert _boot.state == "booting", _boot.state

    # Latency targets are loaded, not hardcoded (D25 — they had drifted across four files).
    # The reclassification is DATA the overlay obeys: first_word is 'measured', so the readout
    # must never flag it over-budget; feedback stays a pass/fail 'gate'.
    tg = targets()
    assert tg["first_word"]["kind"] == "measured", "D25: first_word is a diagnostic, not a gate"
    assert tg["feedback"]["kind"] == "gate" and tg["feedback"]["ms"] == 1500
    assert {t["kind"] for t in tg.values()} <= {"floor", "gate", "measured"}, "unknown target kind"

    # Transport is loaded from status.json (P-01), and the daemon's port override reaches the
    # overlay — the split that used to leave a moved daemon talking to a deaf overlay. The
    # daemon (backend/broadcaster.py) reads the SAME key with the same env, so they can't drift.
    tp = status_schema()["transport"]
    assert (status_host(), status_port()) == (tp["host"], int(os.environ.get(tp["portEnv"], tp["port"])))
    os.environ[tp["portEnv"]] = "9911"
    try:
        assert status_port() == 9911, "the overlay must honour the daemon's NOTHAL_STATUS_PORT"
    finally:
        os.environ.pop(tp["portEnv"], None)

    # --- framing ---
    d = Decoder()
    assert d.feed(b'{"type":"state","state":"idle"}\n') == [{"type": "state", "state": "idle"}]
    assert d.feed(b'{"type":"mic","level":0.5}') == []            # partial line: held back
    assert d.feed(b'\n') == [{"type": "mic", "level": 0.5}]       # ...completed by the next read
    two = d.feed(b'{"type":"mic","level":0.1}\n{"type":"mic","level":0.2}\n')
    assert [m["level"] for m in two] == [0.1, 0.2]                # several per chunk
    # a message split mid-token across three reads still arrives intact
    assert d.feed(b'{"type":"transc') == [] and d.feed(b'ript","text":"hi"') == []
    assert d.feed(b'}\n') == [{"type": "transcript", "text": "hi"}]
    # junk is dropped, and never wedges the stream
    assert d.feed(b'not json\n') == []
    assert d.feed(b'[1,2]\n') == []                               # valid JSON, not an object
    assert d.feed(b'{"type":"future_thing","x":1}\n') == []       # unknown type: ignored
    assert d.feed(b'\n\n') == []                                  # blank lines
    assert d.feed(b'{"type":"state","state":"idle"}\n') == [{"type": "state", "state": "idle"}]

    # a connection that dies mid-line must not glue its remnant onto the next one (P-03)
    d.feed(b'{"type":"mic","level":0.5')                 # daemon dies mid-message
    d.reset()                                            # ...the overlay reconnects
    assert d.feed(b'{"type":"state","state":"idle"}\n') == [{"type": "state", "state": "idle"}], \
        "a partial line from the dead connection was glued onto the next one"

    # --- reducer ---
    s = OverlayState()
    s.apply({"type": "state", "state": "listening"})
    s.apply({"type": "mic", "level": 0.7})
    assert s.state == "listening" and s.mic == 0.7
    s.apply({"type": "state", "state": "thinking"})
    assert s.mic == 0.0, "bars must fall when the capture window closes"
    s.apply({"type": "transcript", "text": "what's the weather"})
    for word in ("It's ", "clear ", "in ", "Tokyo."):
        s.apply({"type": "response", "delta": word})
    assert s.reply == "It's clear in Tokyo." and not s.done
    # the reply must SURVIVE the flip to speaking — this is the whole point of `clearsTurn`
    s.apply({"type": "state", "state": "speaking"})
    assert s.reply == "It's clear in Tokyo.", "speaking must not clear the reply"
    assert s.transcript == "what's the weather", "speaking must not clear the prompt"
    s.apply({"type": "response", "done": True, "model": "claude-opus-4-8", "tokens": 421})
    assert s.done and s.model == "claude-opus-4-8" and s.tokens == 421, (s.model, s.tokens)
    # D43: an unstamped reply is an ANSWER. That default matters more than the stamped case — it
    # is what an older daemon, or one that simply forgot, falls back to, and falling back to the
    # SHORT dwell would blink an answer off the screen while it was being read. On its own state
    # object, so this cannot disturb the sequence above.
    d = OverlayState()
    d.apply({"type": "response", "delta": "It is noon.", "done": True})
    assert d.dwell == "slow", f"an unstamped reply must read as an answer, got {d.dwell!r}"
    d.apply({"type": "response", "done": True, "dwell": "quick"})
    assert d.dwell == "quick", d.dwell
    for junk in ("fast", "", 3, None, "QUICK"):
        d.apply({"type": "response", "done": True, "dwell": junk})
        assert d.dwell == "quick", f"junk must leave the last good value alone, got {d.dwell!r}"
    d.clear_turn()
    assert d.dwell == "slow", "a new turn starts as an answer until told otherwise"

    # Tool activity (D38): named while it runs, gone the moment it stops. The label is latched
    # on the start message and cleared by its OWN done — never by a later state change, because
    # an indicator that outlived the work would claim not-hal reached your mail when it did not.
    s.apply({"type": "tool", "name": "search_email", "label": "Search your inbox for a message"})
    assert s.tool == "Search your inbox for a message", s.tool
    s.apply({"type": "tool", "name": "search_email", "done": True})
    assert s.tool == "", "the indicator must not survive the call that raised it"
    # A tool with no label in the registry still names something rather than nothing.
    s.apply({"type": "tool", "name": "some_new_tool"})
    assert s.tool == "some_new_tool", s.tool
    s.apply({"type": "tool", "name": "some_new_tool", "done": True})

    # A type-valid but field-INVALID message must be IGNORED, never raise (2026-08-02). The decoder
    # validates the type only, so a well-typed message with fields absent reaches the reducer — and
    # a raise would abort feed._on_ready mid-batch, dropping every later message and leaving the mic
    # timer unarmed. Reachable from a non-not-hal producer on the port (no SO_REUSEADDR on Windows).
    hardy = OverlayState()
    hardy.apply({"type": "state", "state": "thinking"})
    for junk in ({"type": "tool"}, {"type": "state"}, {"type": "state", "state": ""},
                 {"type": "transcript"}, {"type": "mic"}, {"type": "mic", "level": "loud"},
                 {"type": "error"}, {"type": "latency"}, {"type": "response", "tokens": "lots"}):
        hardy.apply(junk)                        # none of these may raise
    assert hardy.state == "thinking", "an absent/empty state must be ignored, not applied"
    assert hardy.tool == "", "{type: tool} with no name must not raise, and names nothing"
    # done and the tool-clear are read STRICTLY: a string "false" is truthy, and must not pass.
    hardy.apply({"type": "tool", "name": "search_email"})
    hardy.apply({"type": "tool", "name": "search_email", "done": "false"})
    assert hardy.tool == "search_email", "a string 'false' done must not clear a live indicator"
    hardy.apply({"type": "response", "delta": "hi", "done": "false"})
    assert hardy.done is False, "done parses strictly — the string 'false' is not True"
    # ...and it must survive `idle` too (D24). `idle` now means the DAEMON has finished, not
    # "blank the island": the overlay owns that, because only it knows how much text is left to
    # reveal. While the daemon owned it, it was timing a reveal it could not see, and long
    # answers went dark mid-sentence.
    s.apply({"type": "state", "state": "idle"})
    assert s.reply == "It's clear in Tokyo.", "idle must not wipe the answer (D24)"
    assert s.transcript == "what's the weather", "idle must not wipe the prompt (D24)"
    # The NEXT capture window is what ends a turn. `listening` clearing is what makes it
    # impossible to draw the mic bars over a stale answer — the barge-in bug of 2026-07-22,
    # which existed because that clear lived in one caller instead of in the state itself.
    s.apply({"type": "state", "state": "listening"})
    assert s.reply == "" and s.transcript == "" and not s.done, "listening must clear the turn"
    assert s.model == "" and s.tokens == 0, "the turn clear must reset the model + token footer"
    assert s.history == ["what's the weather"], s.history
    s.apply({"type": "state", "state": "thinking"})
    s.apply({"type": "transcript", "text": "set a timer"})
    s.apply({"type": "transcript", "text": "partial", "final": False})   # partials excluded
    s.apply({"type": "state", "state": "idle"})
    assert s.history == ["what's the weather", "set a timer"], s.history
    for i in range(HISTORY_MAX + 10):                                    # cap holds
        s.apply({"type": "transcript", "text": f"prompt {i}"})
    assert len(s.history) == HISTORY_MAX and s.history[-1] == f"prompt {HISTORY_MAX + 9}"

    # faults: the message carries the reason, and state:error must not wipe it
    s.apply({"type": "error", "message": "I can't reach my model right now.", "kind": "unavailable"})
    s.apply({"type": "state", "state": "error"})
    assert s.error == "I can't reach my model right now." and s.kind == "unavailable"
    s.apply({"type": "error", "message": "no kind given"})
    assert s.kind == "unknown"                                    # schema default
    # latency: captured for the acceptance-run readout, and reset with the turn
    s.apply({"type": "latency", "metric": "feedback", "ms": 900})
    s.apply({"type": "latency", "metric": "first_word", "ms": 3400})
    assert (s.feedback_ms, s.first_word_ms) == (900.0, 3400.0)
    s.apply({"type": "state", "state": "idle"})
    assert s.state == "idle"
    assert s.error == "no kind given", "idle must not wipe a fault (D24)"
    assert (s.feedback_ms, s.first_word_ms) == (900.0, 3400.0), \
        "idle must not reset the instrument — the readout is still on screen (D24)"
    s.apply({"type": "state", "state": "listening"})              # the next turn opens
    assert s.error == "" and s.kind == ""
    assert (s.feedback_ms, s.first_word_ms) == (0.0, 0.0), "latency must reset with the turn"

    print("selfcheck OK: framing survives arbitrary chunking, junk/unknown types ignored, "
          "upstream verbs never delivered downstream, reducer keeps the reply through SPEAKING "
          "and idle, and clears it when the next capture opens")


def main() -> None:
    ap = argparse.ArgumentParser(description="Teleprompter wire decoder — Contract P (Track P)")
    ap.add_argument("--selfcheck", action="store_true",
                    help="verify framing + reducer without Qt or sockets, then exit")
    args = ap.parse_args()
    if args.selfcheck:
        logging.basicConfig(level=logging.WARNING)
        _selfcheck()
        return
    ap.error("nothing to do: pass --selfcheck (the renderer lives in `python -m teleprompter`)")


if __name__ == "__main__":
    main()
