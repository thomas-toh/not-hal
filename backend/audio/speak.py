"""Voice out: earcons and text-to-speech.

Two ways for the bridge to make sound:
- earcon(id): play a designed earcon WAV for one of the ids in
  shared/schemas/earcons.json (ids come from the schema — never hard-coded here).
  The WAVs live in backend/assets/earcons/<id>.wav, pre-rendered to the schema
  outbound rate, so they load through the stdlib wave module — no codec dependency.
- speak(text): synthesise speech with Kokoro (via kokoro-onnx, ONNX runtime, no torch)
  and play it. Output is 24 kHz (schema outbound rate), which is Kokoro's native rate.

Playback uses sounddevice (same lib as the mic, output side). Generate-then-play for now;
streaming TTS is a later latency optimisation.

Run:
    python -m backend.audio.speak "hello, I am your assistant"   # speak text
    python -m backend.audio.speak --earcon awake        # play one earcon
    python -m backend.audio.speak --earcon all          # audition every earcon
    python -m backend.audio.speak --selfcheck           # no audio/model: check tone gen
"""
from __future__ import annotations

import argparse
import logging
import re
import threading
import time
import wave
from collections import deque
from functools import lru_cache
from pathlib import Path

from shared.config import load_schemas
from shared.log import setup_logging

log = logging.getLogger("nothal.speak")

# The espeak phonemizer logs a harmless "words count mismatch" warning whenever it
# merges/splits words (contractions, numbers) — once per sentence since synthesis is
# per-sentence. Pure noise; real phonemizer failures still surface as errors.
logging.getLogger("phonemizer").setLevel(logging.ERROR)

# Output rate is an audio schema constant -- load it, never hardcode (the schema is the truth).
SAMPLE_RATE_OUT = load_schemas()["audio"]["outbound"]["sampleRateHz"]

VOICE = "bf_emma:45,af_heart:40,bm_george:15"   # The app's voice (chosen by ear, 2026-07-13):
                                                # British-led (emma dominant -> en-gb phonemes),
                                                # heart's clarity, george for depth; --voice to retune

SENTENCE_GAP_MS = 300       # ponytail: silence joined between sentences; tune by ear

# Earcons are designed WAVs in backend/assets/earcons/<id>.wav, pre-rendered to SAMPLE_RATE_OUT
# (the mp3->wav conversion is a one-time build step, not run here). Loaded via the stdlib wave
# module and cached; the OutputPump copies on play(), so the cached array is never mutated.
EARCON_DIR = Path(__file__).resolve().parent.parent / "assets" / "earcons"


def _earcon_ids() -> set[str]:
    return {e["id"] for e in load_schemas()["earcons"]["earcons"]}


@lru_cache(maxsize=None)
def _load_earcon(name: str):
    """One designed earcon as float32 mono at SAMPLE_RATE_OUT. The WAVs are pre-rendered to
    that rate (build-time conversion), so this only reads + scales — no resampling. Cached:
    the pump copies on play(), so a shared array is safe to hand out repeatedly. ponytail:
    assumes 16-bit PCM (what the converter emits) — a swapped-in WAV of another width warns
    and is caught by _selfcheck at dev time rather than mis-decoding silently."""
    import numpy as np
    with wave.open(str(EARCON_DIR / f"{name}.wav"), "rb") as w:
        nch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if rate != SAMPLE_RATE_OUT:
        log.warning("earcon %s is %d Hz, expected %d — reconvert it", name, rate, SAMPLE_RATE_OUT)
    if width != 2:
        log.warning("earcon %s is %d-bit, expected 16-bit PCM — reconvert it", name, width * 8)
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    return samples.reshape(-1, nch).mean(axis=1) if nch > 1 else samples


def _play(samples, rate: int = SAMPLE_RATE_OUT) -> None:
    import numpy as np
    import sounddevice as sd
    # latency="high" = bigger buffer -> no underrun buzz; contiguous float32 for a clean feed.
    sd.play(np.ascontiguousarray(samples, dtype=np.float32), rate, latency="high")
    sd.wait()


def earcon_samples(name: str):
    """Float32 samples for one earcon by its schema id (no playback — the orchestrator feeds
    these into the OutputPump; the CLI plays them via earcon())."""
    if name not in _earcon_ids():
        raise ValueError(f"unknown earcon {name!r}; valid: {sorted(_earcon_ids())}")
    return _load_earcon(name)


def earcon(name: str) -> None:
    """Play one earcon by its schema id."""
    _play(earcon_samples(name))


class OutputPump:
    """Persistent warm output stream (binding for BT devices): one OutputStream
    held open for the daemon's life, fed silence between sounds so a Bluetooth link never
    idles — the onset-buzz fix. play() enqueues without blocking; cut() drops all queued
    audio (the barge-in stop, ≤ 250 ms); the callback runs on PortAudio's thread, so the
    main loop stays free to read the mic while sound plays."""

    def __init__(self, rate: int = SAMPLE_RATE_OUT):
        self.rate = rate
        self._lock = threading.Lock()
        self._queue: deque = deque()    # pending float32 arrays; head may be part-played
        self._stream = None

    def __enter__(self) -> "OutputPump":
        import sounddevice as sd
        # ponytail: latency="low" keeps barge-in cuts inside the 250 ms budget; the
        # callback is a memcpy so underruns shouldn't happen — raise it if a device crackles.
        self._stream = sd.OutputStream(samplerate=self.rate, channels=1, dtype="float32",
                                       latency="low", callback=self._callback)
        self._stream.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stream.stop()
        self._stream.close()
        self._stream = None

    def _callback(self, outdata, frames, time_info, status) -> None:
        outdata.fill(0)                 # silence baseline — this line IS the BT keep-alive
        filled = 0
        with self._lock:
            while filled < frames and self._queue:
                chunk = self._queue[0]
                n = min(frames - filled, len(chunk))
                outdata[filled:filled + n, 0] = chunk[:n]
                if n == len(chunk):
                    self._queue.popleft()
                else:
                    self._queue[0] = chunk[n:]
                filled += n

    def play(self, samples) -> None:
        """Enqueue samples (float32 mono at self.rate). Returns immediately."""
        import numpy as np
        with self._lock:
            self._queue.append(np.ascontiguousarray(samples, dtype=np.float32))

    def cut(self) -> None:
        """Drop everything queued — output goes silent within one callback block."""
        with self._lock:
            self._queue.clear()

    def playing(self) -> bool:
        """True while queued audio remains (the device buffer adds ~latency ms after)."""
        with self._lock:
            return bool(self._queue)


# --- text-to-speech (Kokoro via kokoro-onnx) ---
# Model files aren't bundled (too big); fetched once to a local cache on first use.
_KOKORO_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
_KOKORO_FILES = {"kokoro-v1.0.onnx": _KOKORO_BASE + "/kokoro-v1.0.onnx",
                 "voices-v1.0.bin": _KOKORO_BASE + "/voices-v1.0.bin"}
_kokoro = None
_kokoro_lock = threading.Lock()    # see listen._ensure_whisper — since warm-up moved to
                                   # a background thread, two callers can race this lazy init.


def _ensure_kokoro():
    """The TTS model, built exactly once, whichever thread gets here first."""
    global _kokoro
    with _kokoro_lock:
        if _kokoro is None:
            from kokoro_onnx import Kokoro
            model, voices = _kokoro_model_paths()
            log.info("loading Kokoro TTS...")
            _kokoro = Kokoro(str(model), str(voices))
        return _kokoro


def _kokoro_model_paths() -> tuple[Path, Path]:
    import urllib.request
    cache = Path.home() / ".cache" / "nothal"
    cache.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, url in _KOKORO_FILES.items():
        p = cache / name
        if not p.exists():
            log.info("downloading %s (first run only)...", name)
            urllib.request.urlretrieve(url, p)
        paths[name] = p
    return paths["kokoro-v1.0.onnx"], paths["voices-v1.0.bin"]


def _sentence_chunks(text: str) -> list[str]:
    """Split text at sentence terminators for per-sentence synthesis. ponytail: 'Dr.'
    mis-splits into an extra pause — rare and mild; a later normalisation pass cleans inputs."""
    return [p for p in re.split(r"(?<=[.!?])\s+", text.strip()) if p]


def _voice_weights(spec: str) -> list[tuple[str, float]]:
    """Parse a voice spec into normalised (name, weight) pairs. 'af_heart' -> 100%;
    'af_heart:60,af_nicole:40' -> a 60/40 blend (weights needn't sum to anything)."""
    parts = [(n.strip(), float(w or 1)) for n, _, w in
             (p.partition(":") for p in spec.split(","))]
    total = sum(w for _, w in parts)
    return [(n, w / total) for n, w in parts]


def _voice_lang(weights: list[tuple[str, float]]) -> str:
    """espeak phonemizer language from the dominant blend voice — Kokoro's b* voices
    are British; American phonemes flatten their accent."""
    top = max(weights, key=lambda nw: nw[1])[0]
    return "en-gb" if top.startswith("b") else "en-us"


def synth(text: str, voice: str = VOICE, speed: float = 1.0):
    """Synthesise `text` to samples at SAMPLE_RATE_OUT (no playback — the orchestrator
    feeds these into the OutputPump). Kokoro's native rate is the schema's 24 kHz.

    Per-sentence synthesis, joined with SENTENCE_GAP_MS of silence: Kokoro rushes
    sentence boundaries and flattens prosody on long inputs, so pacing is ours and
    each sentence gets its natural contour. (Also the unit sentence-streamed TTS
    would need, if ever unparked.)

    `voice` may be a single name or a blend, e.g. 'af_heart:60,af_nicole:40' —
    Kokoro voices are style vectors, so a weighted mix is itself a voice."""
    import numpy as np
    kokoro = _ensure_kokoro()
    weights = _voice_weights(voice)
    style = (sum(kokoro.get_voice_style(n) * w for n, w in weights)
             if len(weights) > 1 else weights[0][0])
    lang = _voice_lang(weights)
    t0 = time.perf_counter()
    gap = np.zeros(SAMPLE_RATE_OUT * SENTENCE_GAP_MS // 1000, dtype=np.float32)
    pieces: list = []
    for sentence in _sentence_chunks(text):
        samples, rate = kokoro.create(sentence, voice=style, speed=speed, lang=lang)
        if rate != SAMPLE_RATE_OUT:  # never happens with Kokoro v1; loud if a swap breaks it
            log.warning("TTS rate %d != schema outbound %d", rate, SAMPLE_RATE_OUT)
        if pieces:
            pieces.append(gap)
        pieces.append(np.asarray(samples, dtype=np.float32))
    log.info("TTS %.0f ms for %d chars", (time.perf_counter() - t0) * 1000, len(text))
    out = np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)
    if len(out) and (peak := float(np.abs(out).max())) > 0:
        out *= 0.8 / peak   # ponytail: peak normalisation = consistent presence at the
                            # pump; a proper loudness (RMS/LUFS) pass is sound-design work
    return out


def speak(text: str, voice: str = VOICE, speed: float = 1.0) -> float:
    """Synthesise `text` and play it. Returns the spoken duration in seconds."""
    samples = synth(text, voice, speed)
    _play(samples, SAMPLE_RATE_OUT)
    return len(samples) / SAMPLE_RATE_OUT


def _selfcheck() -> None:
    """No audio/model: every schema earcon has a loadable WAV within its maxMs ceiling."""
    maxms = {e["id"]: e["maxMs"] for e in load_schemas()["earcons"]["earcons"]}
    for name in sorted(_earcon_ids()):
        samples = _load_earcon(name)
        dur_ms = len(samples) / SAMPLE_RATE_OUT * 1000
        assert len(samples) > 0, f"{name}: empty earcon WAV"
        assert dur_ms <= maxms[name] + 1, f"{name}: {dur_ms:.0f} ms exceeds schema maxMs {maxms[name]}"

    # Sentence splitter for per-sentence TTS pacing.
    assert _sentence_chunks("Sure! Mercury is small. And Neptune is windy.") == \
        ["Sure!", "Mercury is small.", "And Neptune is windy."]
    assert _sentence_chunks("no terminator") == ["no terminator"]
    assert _sentence_chunks("Really?  Yes.") == ["Really?", "Yes."]
    assert _sentence_chunks("") == []

    # Voice-blend spec parser + phonemizer-language inference.
    assert _voice_weights("af_heart") == [("af_heart", 1.0)]
    assert _voice_weights("af_heart:60,af_nicole:40") == [("af_heart", 0.6), ("af_nicole", 0.4)]
    assert _voice_weights("a:1, b:1") == [("a", 0.5), ("b", 0.5)]
    assert _voice_lang([("af_heart", 1.0)]) == "en-us"
    assert _voice_lang([("bf_emma", 0.7), ("af_heart", 0.3)]) == "en-gb"
    assert _voice_lang([("af_heart", 0.3), ("bm_george", 0.7)]) == "en-gb"

    # OutputPump buffer discipline, no device: drive the callback by hand.
    import numpy as np
    pump = OutputPump()
    out = np.ones((8, 1), dtype=np.float32)
    pump._callback(out, 8, None, None)
    assert not out.any(), "empty pump must emit pure silence (BT keep-alive)"
    pump.play(np.arange(1, 11, dtype=np.float32))          # 10 samples: 1..10
    pump.play(np.full(4, 99, dtype=np.float32))
    pump._callback(out, 8, None, None)
    assert list(out[:, 0]) == [1, 2, 3, 4, 5, 6, 7, 8], "callback must drain in order"
    assert pump.playing()
    pump._callback(out, 8, None, None)
    assert list(out[:, 0]) == [9, 10, 99, 99, 99, 99, 0, 0], "must cross chunks, then pad silence"
    assert not pump.playing()
    pump.play(np.ones(100, dtype=np.float32))
    pump.cut()
    assert not pump.playing(), "cut() must empty the queue"
    pump._callback(out, 8, None, None)
    assert not out.any(), "after cut(), silence"

    print(f"selfcheck OK: WAVs for {len(_earcon_ids())} earcons load within schema maxMs; "
          f"pump drains in order, pads silence, cut() empties")


def main() -> None:
    setup_logging()
    ap = argparse.ArgumentParser(description="Play the earcons and speak text")
    ap.add_argument("text", nargs="?", help="text to speak")
    ap.add_argument("--earcon", help="play an earcon id (or 'all' to audition every one)")
    ap.add_argument("--voice", default=VOICE,
                    help=f"Kokoro voice or blend like af_heart:60,af_nicole:40 (default {VOICE})")
    ap.add_argument("--speed", type=float, default=1.0,
                    help="speech rate; 0.90-0.95 adds weight (default 1.0)")
    ap.add_argument("--selfcheck", action="store_true",
                    help="verify tone generation without audio or the TTS model, then exit")
    args = ap.parse_args()
    if args.selfcheck:
        _selfcheck()
        return
    if args.earcon == "all":
        for name in sorted(_earcon_ids()):
            print(f"earcon: {name}")
            earcon(name)
            time.sleep(0.4)
    elif args.earcon:
        earcon(args.earcon)
    if args.text:
        speak(args.text, voice=args.voice, speed=args.speed)


if __name__ == "__main__":
    main()
