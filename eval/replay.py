"""The replay harness plays recorded WAVs through the REAL pipeline (openWakeWord, Silero
VAD, faster-whisper) driving the REAL orchestrator state machine, with fakes only at
the edges: mic (the WAV), output pump (audio-time simulation), model (scripted
Contract-B events) and TTS (silence of realistic length).

Identical audio every run: if a wake/VAD/STT/tuning change alters behaviour, this
catches it in one command — on the PC and, with the copied wav/ folder, on the Mac.

    python -m eval.replay                          # run every case in cases.json
    python -m eval.replay --case key_short         # one case
    python -m eval.replay --record key_short       # record that case's WAV (mic)

The WAVs are a recorded voice and deliberately untracked (eval/replay/wav/ is
gitignored); cases.json (text only) is committed. Recording fixtures is consented,
scripted capture; the privacy rule on ambient mic audio covers live listening, and
test fixtures fall outside it.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import wave
from pathlib import Path

from backend.audio.listen import SileroVAD, _silero_model_path
from backend.audio.wake import SAMPLE_RATE, WAKE_MODEL
from backend.llm.base import Done, TextDelta
from backend.hotkeys import Hotkeys
from backend.orchestrator import Orchestrator, latency_table

HERE = Path(__file__).resolve().parent / "replay"
CASES = HERE / "cases.json"
WAV_DIR = HERE / "wav"

TTS_RATE_OUT = 24000          # fake synth only; the real rate lives in the schema
SPEECH_SPS = 0.055            # fake synth: seconds of "speech" per character (~18 chars/s)
IDLE_BUDGET_S = 60            # audio-time a case may spend beyond its clip before we stop


class ReplayExhausted(Exception):
    """FakeMic ran out of budget — the scenario is over; serve() unwinds here."""


class FakeMic:
    """Serves a recorded clip through the mic interface, then silence — until an
    audio-time budget runs out, which ends the scenario.

    `on_exhausted` fires once when the clip runs dry. For a key case that is the second
    keypress: the WAV was recorded between two real presses, so the clip ending IS the
    endpoint — no invented per-case timestamp."""

    def __init__(self, clip16, on_exhausted=None):
        import numpy as np
        self._clip = clip16.astype(np.int16)
        self._pos = 0
        self._budget = len(clip16) + IDLE_BUDGET_S * SAMPLE_RATE
        self._on_exhausted = on_exhausted

    read_available = 0          # nothing ever buffers: reads are on-demand

    def read(self, n: int):
        import numpy as np
        if self._pos > self._budget:
            raise ReplayExhausted
        chunk = self._clip[self._pos:self._pos + n]
        if len(chunk) < n:
            chunk = np.concatenate([chunk, np.zeros(n - len(chunk), dtype=np.int16)])
            if self._on_exhausted is not None:
                self._on_exhausted()
                self._on_exhausted = None       # once
        self._pos += n
        return chunk.reshape(-1, 1), False


class FakePump:
    """Output side in audio-time, no device, no wall clock: play() queues seconds;
    each playing() poll consumes 32 ms (one VAD-chunk of mic reading in _speak).
    Earcon seconds decay the same way — close enough for event/latency assertions."""

    def __init__(self):
        self.remaining = 0.0
        self.played: list[float] = []

    def play(self, samples) -> None:
        dur = len(samples) / TTS_RATE_OUT
        self.remaining += dur
        self.played.append(dur)

    def playing(self) -> bool:
        if self.remaining <= 0:
            return False
        self.remaining -= 0.032
        return True

    def cut(self) -> None:
        self.remaining = 0.0


class FakeModel:
    """Contract B with a script: one TextDelta then Done. Zero network, zero keys."""

    def __init__(self, reply: str):
        self.reply = reply

    async def converse(self, session, utterance, tools):
        yield TextDelta(self.reply)
        yield Done()


def fake_synth(text: str, voice=None, speed: float = 1.0):
    """Silence with a realistic spoken duration — timing semantics without Kokoro."""
    import numpy as np
    return np.zeros(max(1, int(TTS_RATE_OUT * SPEECH_SPS * len(text))), dtype=np.float32)


def load_wav(path: Path):
    import numpy as np
    with wave.open(str(path), "rb") as w:
        ok = (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, SAMPLE_RATE)
        if not ok:
            raise ValueError(f"{path.name}: need {SAMPLE_RATE} Hz mono 16-bit "
                             f"(got {w.getframerate()} Hz, {w.getnchannels()} ch, "
                             f"{w.getsampwidth() * 8}-bit) — re-record with --record")
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", s.lower())).strip()


def similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def run_case(case: dict, wake_model) -> list[str]:
    """Replay one case; return a list of failure strings (empty = pass)."""
    wav = WAV_DIR / case["file"]
    if not wav.exists():
        return [f"{wav} missing — record it: python -m eval.replay --record {case['name']}"]
    clip = load_wav(wav)

    trigger = case.get("trigger", "wake")
    Orchestrator._flush_wake(wake_model)        # a prior case's phrase must not leak in
    orch = Orchestrator(model=FakeModel(case.get("model_reply", "Acknowledged.")))
    orch.synth = fake_synth
    orch.vad = SileroVAD(_silero_model_path())

    # A keyed case drives the REAL Door objects — never registering a combo, so no Win32
    # and no keyboard here; a Door is two Events and that is the whole interface.
    on_exhausted = None
    if trigger == "key":
        orch.hk = Hotkeys({"ask": "ctrl+alt+1"})
        ask = orch.hk.doors["ask"]
        ask.start.set()                         # the press that opened the recording
        on_exhausted = ask.end.set              # ...and the one that closed it
    try:
        orch.serve(FakeMic(clip, on_exhausted), FakePump(), wake_model)
    except ReplayExhausted:
        pass

    events = [e for _, e, _ in orch.trace]
    transcripts = [d for _, e, d in orch.trace if e == "transcript"]
    fails = []
    # Both doors trace a "wake" event; the detail says which entrance opened it.
    entrances = [d for _, e, d in orch.trace if e == "wake"]
    want = {"key": ["key"], "wake": ["phrase"], "none": []}[trigger]
    if entrances[:1] != want:
        fails.append(f"trigger: expected {trigger} {want}, got {entrances}")
    expected = case.get("transcripts", [])
    if len(transcripts) != len(expected):
        fails.append(f"transcripts: expected {len(expected)}, got {transcripts}")
    for want, got in zip(expected, transcripts):
        r = similar(want, got)
        if r < 0.85:
            fails.append(f"transcript {r:.2f} similar: want {want!r}, got {got!r}")
    for ev in case.get("expect_events", []):
        if ev not in events:
            fails.append(f"missing event {ev!r}; events={events}")
    print(latency_table(orch.trace))
    return fails


def run_all(only: str | None) -> int:
    import openwakeword.utils
    from openwakeword.model import Model

    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    if only:
        cases = [c for c in cases if c["name"] == only]
        if not cases:
            print(f"no case named {only!r}", file=sys.stderr)
            return 2
    openwakeword.utils.download_models([WAKE_MODEL])
    wake_model = Model(wakeword_models=[WAKE_MODEL], inference_framework="onnx")

    failed = 0
    for case in cases:
        print(f"\n=== {case['name']} ===")
        fails = run_case(case, wake_model)
        for f in fails:
            print(f"FAIL  {f}")
        failed += bool(fails)
        print(f"{'FAIL' if fails else 'PASS'}  {case['name']}")
    print(f"\nreplay: {len(cases) - failed}/{len(cases)} passed")
    return 1 if failed else 0


def _record_keyed(max_seconds: int):
    """Record between two real presses of the ask hotkey. The clip is then exactly the
    capture window, so replay needs no invented endpoint timestamp — the WAV running out
    IS the second tap. Uses the real hotkey module, which is also a live test of it."""
    import time

    import numpy as np
    import sounddevice as sd

    hk = Hotkeys()
    hk.start()
    ask = hk.doors["ask"]
    print(f"{ask.combo}: tap to start / tap to stop, OR hold down and release "
          f"— whichever the case's script says ({max_seconds} s cap)")
    while not ask.start.is_set():               # set on PRESS, so a hold records too
        time.sleep(0.03)
    print("recording...")
    frames = []
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16") as mic:
        while not ask.end.is_set() and len(frames) * 1024 < max_seconds * SAMPLE_RATE:
            chunk, _ = mic.read(1024)
            frames.append(chunk[:, 0].copy())
    data = np.concatenate(frames) if frames else np.zeros(1, dtype=np.int16)
    if len(data) < SAMPLE_RATE // 2:
        print("WARNING: under 0.5 s captured — the window shut almost immediately. "
              "Re-record; start speaking after the 'recording...' line, not before.")
    return data


def record(name: str, seconds: int) -> None:
    """Record one case's WAV from the default mic (16 kHz mono 16-bit)."""
    import sounddevice as sd

    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    case = next((c for c in cases if c["name"] == name), None)
    if case is None:
        sys.exit(f"no case named {name!r}; valid: {[c['name'] for c in cases]}")
    seconds = seconds or case.get("seconds", 12)
    WAV_DIR.mkdir(parents=True, exist_ok=True)
    print(f"script: {case['script']}")
    if case.get("trigger") == "key":
        data = _record_keyed(seconds)
    else:
        input(f"recording {seconds} s after you press Enter...")
        data = sd.rec(int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE,
                      channels=1, dtype="int16")
        sd.wait()
    path = WAV_DIR / case["file"]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(data.tobytes())
    print(f"wrote {path} ({len(data) / SAMPLE_RATE:.1f} s) — "
          f"replay it: python -m eval.replay --case {name}")


def main() -> None:
    from shared.log import setup_logging
    setup_logging()
    ap = argparse.ArgumentParser(description="Replay recorded audio through the pipeline")
    ap.add_argument("--case", help="run a single case by name")
    ap.add_argument("--record", metavar="NAME", help="record NAME's WAV from the mic")
    ap.add_argument("--seconds", type=int, default=0, help="recording length override")
    args = ap.parse_args()
    if args.record:
        record(args.record, args.seconds)
        return
    sys.exit(run_all(args.case))


if __name__ == "__main__":
    main()
