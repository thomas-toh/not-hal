"""Track G step 3 (docs/04 §8): wake -> listen (VAD) -> transcribe -> console.

Extends step 2. On wake it captures speech, uses Silero VAD to find end-of-speech
(1 s of silence, spec/40; --silence-ms to tune), then transcribes the utterance with faster-whisper
(small.en) and prints the text. A little pre-roll from the ring buffer keeps the start
of the sentence. All audio stays in RAM and is dropped after transcription (spec/50
rule 3) -- nothing is written to disk.

Engine choice A (spec/40): faster-whisper, GPU (CUDA) if present else CPU -- same code
on Windows and macOS. transcribe() is the swap-point: a Mac-GPU engine (whisper.cpp /
MLX) can replace its body later without touching the pipeline.

Run:
    python -m backend.audio.listen             # say "hey jarvis", then speak
    python -m backend.audio.listen --selfcheck # no mic/models: end-of-speech logic only
"""
from __future__ import annotations

import argparse
import logging
import threading
import time

from backend.audio.wake import (
    SAMPLE_RATE, BLOCK_MS, BLOCK_SAMPLES, BUFFER_BLOCKS, WAKE_MODEL, THRESHOLD,
)
from shared.log import setup_logging

log = logging.getLogger("nothal.listen")

# --- listening-window params (all tunable; see spec/40) ---
VAD_CHUNK = 512                                   # Silero VAD needs 512-sample windows @ 16 kHz
VAD_CHUNK_MS = VAD_CHUNK * 1000 // SAMPLE_RATE    # 32 ms
VAD_THRESHOLD = 0.5                               # speech if Silero prob >= this
SILENCE_MS = 1000                                 # spec/40: end-of-speech silence (--silence-ms
                                                  # to tune live; semantic endpointing is the
                                                  # proper fix for long composed prompts, M1)
MAX_UTTERANCE_S = 30                              # assistant safety cap ONLY; VAD ends normal turns
DICTATION_MAX_UTTERANCE_S = 300                   # dictation's endpoint is the KEY (D20), so this is
                                                  # only a runaway backstop — generous, or a long
                                                  # dictation (an email, a paragraph) truncates mid-word
NOSPEECH_MS = 5000                                # spec/40: give up if nothing said after wake
PREROLL_MS = 200                                  # pre-roll from ring buffer (tune: onset vs
                                                  # bleeding the wake word into the transcript)

# Derived chunk counts. No hand-computed values in the comments: they are the one part that
# can lie (the give-up count read "93 (~3 s)" long after NOSPEECH_MS became 5000 → 156). The
# selfcheck prints the live numbers instead.
SILENCE_CHUNKS = (SILENCE_MS + VAD_CHUNK_MS - 1) // VAD_CHUNK_MS   # ceil: end-of-speech silence
MAX_CHUNKS = MAX_UTTERANCE_S * 1000 // VAD_CHUNK_MS                # assistant runaway cap
DICTATION_MAX_CHUNKS = DICTATION_MAX_UTTERANCE_S * 1000 // VAD_CHUNK_MS   # dictation backstop (spec/60)
NOSPEECH_CHUNKS = NOSPEECH_MS // VAD_CHUNK_MS                      # give up if speech never starts
PREROLL_BLOCKS = PREROLL_MS // BLOCK_MS                            # pre-roll from the ring

WHISPER_MODEL = "small.en"                        # spec/40 decision 2


class EndOfSpeech:
    """Decide when one utterance is over, from a stream of per-chunk speech flags.

    Normal turns end on SILENCE_CHUNKS of silence *after* speech starts (any length).
    MAX_CHUNKS is a runaway backstop only. NOSPEECH_CHUNKS gives up if speech never
    starts. Pure logic -> unit-tested in _selfcheck() without a mic or any model.
    """

    def __init__(self, silence_chunks=SILENCE_CHUNKS, max_chunks=MAX_CHUNKS,
                 nospeech_chunks=NOSPEECH_CHUNKS):
        self.silence_chunks = silence_chunks
        self.max_chunks = max_chunks
        self.nospeech_chunks = nospeech_chunks
        self.total = 0
        self.silence_run = 0
        self.speech_started = False

    def update(self, is_speech: bool) -> bool:
        """Feed one chunk's speech flag; return True when the turn should end."""
        self.total += 1
        if is_speech:
            self.speech_started = True
            self.silence_run = 0
        else:
            self.silence_run += 1
        if not self.speech_started:
            return self.total >= self.nospeech_chunks     # nothing said -> give up
        if self.silence_run >= self.silence_chunks:
            return True                                    # end of speech
        return self.total >= self.max_chunks               # safety cap


def _silero_model_path() -> str:
    """Path to the Silero VAD ONNX model that openWakeWord already ships. Reusing it
    lets us run VAD on onnxruntime with **no torch** — which also sidesteps torch failing
    to install on this box (Windows long-path limit). ponytail: reuses a bundled asset;
    vendor the ~2 MB model if that coupling ever breaks."""
    import glob
    import os

    import openwakeword
    base = os.path.dirname(openwakeword.__file__)
    hits = glob.glob(os.path.join(base, "**", "silero_vad*.onnx"), recursive=True)
    if not hits:
        raise FileNotFoundError("silero_vad.onnx not found in the openwakeword package")
    return hits[0]


class SileroVAD:
    """Silero VAD (v4 ONNX) via onnxruntime; no torch. Stateful across chunks — call
    reset() at the start of each utterance to clear the LSTM state."""

    def __init__(self, path: str):
        import numpy as np
        import onnxruntime as ort
        self.sess = ort.InferenceSession(path)
        self.sr = np.array(SAMPLE_RATE, dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        import numpy as np
        self.h = np.zeros((2, 1, 64), dtype=np.float32)
        self.c = np.zeros((2, 1, 64), dtype=np.float32)

    def prob(self, samples_int16) -> float:
        """Speech probability (0–1) for one chunk of int16 samples."""
        x = (samples_int16.astype("float32") / 32768.0).reshape(1, -1)
        out, self.h, self.c = self.sess.run(
            ["output", "hn", "cn"],
            {"input": x, "sr": self.sr, "h": self.h, "c": self.c})
        return float(out[0][0])


_whisper = None
_whisper_lock = threading.Lock()   # D39: run() warms this on a background thread while the
                                   # hotkeys are already live, so two threads can race the
                                   # lazy init below and build two CUDA models.


_cuda_dlls: list = []          # holds the loaded modules; see _load_cuda_dlls


def _load_cuda_dlls() -> None:
    """Windows: make the pip `nvidia-*-cu12` CUDA libraries reachable by ctranslate2.

    The packages drop cuBLAS/cuDNN/cudart inside the `nvidia` package dir, which is not on the
    DLL search path. `add_dll_directory` alone is NOT enough and was the bug (measured
    2026-08-01): `ctypes` honours it, but ctranslate2 resolves cuBLAS by a route that does not,
    so every transcribe raised "Library cublas64_12.dll is not found or cannot be loaded" and
    fell back to CPU — silently costing ~700 ms on each one, on a box with a working GPU.

    So we also PRELOAD each DLL by absolute path. Windows keys loaded modules by base name, so
    ctranslate2's later `LoadLibrary("cublas64_12.dll")` finds the copy already in the process
    and never searches at all. Load order follows the dependency chain; the modules are kept in
    `_cuda_dlls` because nothing else holds a reference.

    No-op off Windows, or when the packages aren't installed — then transcribe uses CPU, which
    is the documented fallback, not a failure. See pyproject's `[gpu-cuda]` extra."""
    import os
    if not hasattr(os, "add_dll_directory") or _cuda_dlls:   # non-Windows, or already done
        return
    import ctypes
    import glob
    import importlib.util
    spec = importlib.util.find_spec("nvidia")
    if spec is None or not spec.submodule_search_locations:
        return
    base = spec.submodule_search_locations[0]
    bins = [d for d in glob.glob(os.path.join(base, "*", "bin")) if os.path.isdir(d)]
    for d in bins:
        os.add_dll_directory(d)                  # kept: other loaders DO honour it
    order = ("cudart64", "cublasLt64", "cublas64", "cudnn")
    dlls = [p for d in bins for p in glob.glob(os.path.join(d, "*.dll"))]
    dlls.sort(key=lambda p: next((i for i, k in enumerate(order)
                                  if os.path.basename(p).startswith(k)), len(order)))
    for p in dlls:
        try:
            _cuda_dlls.append(ctypes.WinDLL(p))
        except OSError:
            pass          # a lib we don't need, or one whose own deps are absent — CPU still works
    if _cuda_dlls:
        log.info("preloaded %d CUDA libraries for ctranslate2", len(_cuda_dlls))


def _load_whisper(device: str, compute_type: str):
    """Load the model, preferring the copy already on disk (D39). faster-whisper otherwise
    asks huggingface.co for the current revision on EVERY start — an internet dependency at
    launch for a model we already have, and dead weight in the cold-start measurement. We
    fall back to a networked load rather than hard-failing, because `local_files_only` is
    also how a genuinely-absent model reports itself, and a fresh machine must still work."""
    from faster_whisper import WhisperModel
    log.info("loading faster-whisper %r on %s...", WHISPER_MODEL, device)
    try:
        return WhisperModel(WHISPER_MODEL, device=device, compute_type=compute_type,
                            local_files_only=True)
    except Exception as e:                       # not cached yet, or the cache is unusable
        log.info("%r not in the local cache (%s) — downloading it (first run only)...",
                 WHISPER_MODEL, type(e).__name__)
        return WhisperModel(WHISPER_MODEL, device=device, compute_type=compute_type)


def _ensure_whisper():
    """The model, built exactly once. The lock is load-bearing since D39: run() warms this
    on a background thread while the hotkeys are already live, so a real capture can reach
    here mid-warm-up. Unlocked, both callers would see None and each build a CUDA model —
    two copies of the weights on the GPU. The late arrival blocks on the load instead, which
    is the wait it would have had anyway."""
    global _whisper
    with _whisper_lock:
        if _whisper is None:
            import ctranslate2
            if ctranslate2.get_cuda_device_count() > 0:
                _load_cuda_dlls()
                _whisper = _load_whisper("cuda", "float16")
            else:
                _whisper = _load_whisper("cpu", "int8")
        return _whisper


def _fallback_to_cpu():
    """GPU inference failed at run time — rebuild on CPU. ponytail: two threads failing
    together rebuild twice; harmless (CPU load, right answer either way), fix if it shows."""
    global _whisper
    with _whisper_lock:
        _whisper = _load_whisper("cpu", "int8")
        return _whisper


def _run(model, audio_f32) -> str:
    # beam_size=5 is faster-whisper's own default. It was 1 (greedy) for latency until
    # 2026-08-03, when accuracy needed it — the condition the old ponytail note named.
    # Greedy commits to the top token at every step and cannot reconsider, which is exactly
    # the shape of the errors seen: "Edge" -> "itch", a short low-context word whose
    # acoustic neighbour won one step. Costs ~2-3x on the DECODE half of a ~35 ms transcribe,
    # invisible against cleanup's 240-450 ms. Raise it further only with a measurement.
    segments, _ = model.transcribe(audio_f32, language="en", beam_size=5)
    return "".join(s.text for s in segments).strip()


def transcribe(audio_f32) -> str:
    """STT seam (spec/40 engine choice A). Uses the GPU (CUDA) when it's actually usable,
    else CPU — one code path on Windows and macOS. A Mac-GPU engine (whisper.cpp / MLX)
    could replace this body later, same signature.

    'GPU where present' means present *and loadable*: a CUDA device with missing runtime
    libs (cuBLAS/cuDNN) only fails at inference, so we fall back to CPU on first use."""
    model = _ensure_whisper()
    try:
        t0 = time.perf_counter()
        text = _run(model, audio_f32)
    except RuntimeError as e:
        log.warning("GPU transcribe failed (%s) -- falling back to CPU", e)
        model = _fallback_to_cpu()
        t0 = time.perf_counter()
        text = _run(model, audio_f32)
    log.info("STT %.0f ms", (time.perf_counter() - t0) * 1000)   # real transcription latency
    return text


def listen(silence_ms: int = SILENCE_MS) -> None:
    """Full wake -> listen -> transcribe loop on the default mic. Ctrl-C to stop.
    silence_ms = how long a pause ends your turn (bigger = more pause-tolerant, but adds
    that much delay before every reply)."""
    from collections import deque

    import numpy as np
    import sounddevice as sd
    import openwakeword.utils
    from openwakeword.model import Model

    silence_chunks = (silence_ms + VAD_CHUNK_MS - 1) // VAD_CHUNK_MS
    log.info("loading models (first run downloads them)...")
    openwakeword.utils.download_models([WAKE_MODEL])
    wake_model = Model(wakeword_models=[WAKE_MODEL], inference_framework="onnx")
    vad = SileroVAD(_silero_model_path())

    ring: deque = deque(maxlen=BUFFER_BLOCKS)
    log.info("listening @ %d Hz (end-of-speech %d ms) -- say '%s', then speak (Ctrl-C to stop)",
             SAMPLE_RATE, silence_ms, WAKE_MODEL.replace("_", " "))
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                        blocksize=0) as stream:
        while True:
            # --- wake phase: 80 ms blocks into openWakeWord ---
            block, _ = stream.read(BLOCK_SAMPLES)
            frame = block[:, 0]
            ring.append(frame)
            if not any(s >= THRESHOLD for s in wake_model.predict(frame).values()):
                continue

            # --- listen phase: 512-sample chunks into Silero VAD ---
            print("[wake] listening...")
            vad.reset()
            eos = EndOfSpeech(silence_chunks=silence_chunks)
            captured = [np.concatenate(list(ring)[-PREROLL_BLOCKS:])]   # pre-roll
            while True:
                chunk, _ = stream.read(VAD_CHUNK)
                samples = chunk[:, 0]
                captured.append(samples)
                if eos.update(vad.prob(samples) >= VAD_THRESHOLD):
                    break

            if eos.total >= MAX_CHUNKS:
                log.warning("hit the %d s utterance cap -- transcribing what we have",
                            MAX_UTTERANCE_S)
            if not eos.speech_started:
                print("[wake] (nothing heard)")
            else:
                audio = np.concatenate(captured).astype("float32") / 32768.0
                text = transcribe(audio)
                print(f"> {text}" if text else "[wake] (no transcript)")
            ring.clear()


def _selfcheck() -> None:
    """No mic/models: prove the end-of-speech state machine (spec/40 timings)."""
    # 1) speech then silence -> ends after exactly SILENCE_CHUNKS of silence
    eos = EndOfSpeech()
    for _ in range(20):
        assert not eos.update(True)
    ended_at = next(i for i in range(1, 500) if eos.update(False))
    assert ended_at == SILENCE_CHUNKS, (ended_at, SILENCE_CHUNKS)
    assert eos.speech_started

    # 2) pure silence -> gives up at NOSPEECH_CHUNKS, no speech ever seen
    eos = EndOfSpeech()
    n = 0
    while not eos.update(False):
        n += 1
    assert n + 1 == NOSPEECH_CHUNKS, (n + 1, NOSPEECH_CHUNKS)
    assert not eos.speech_started

    # 3) unbroken speech -> stops at the MAX_CHUNKS safety cap
    eos = EndOfSpeech()
    n = 0
    while not eos.update(True):
        n += 1
    assert n + 1 == MAX_CHUNKS, (n + 1, MAX_CHUNKS)

    # 3b) a caller can RAISE the cap — dictation does, since its endpoint is the key not the clock,
    # and a 30 s cap truncated long dictations. Prove the param is honoured and is larger.
    assert DICTATION_MAX_CHUNKS > MAX_CHUNKS, (DICTATION_MAX_CHUNKS, MAX_CHUNKS)
    eos = EndOfSpeech(max_chunks=DICTATION_MAX_CHUNKS)
    n = 0
    while not eos.update(True):
        n += 1
    assert n + 1 == DICTATION_MAX_CHUNKS, (n + 1, DICTATION_MAX_CHUNKS)

    # 4) CUDA preload (no GPU needed): must be safe to call anywhere, and must not re-load on a
    # second call — `transcribe` may reach it again on the CPU-fallback path, and reloading the
    # same 17 libraries per turn would be a slow no-op nobody would notice.
    _load_cuda_dlls()
    n_first = len(_cuda_dlls)
    _load_cuda_dlls()
    assert len(_cuda_dlls) == n_first, "preloading CUDA twice must not reload the libraries"
    if n_first:
        names = {__import__("os").path.basename(getattr(d, "_name", "")).lower()
                 for d in _cuda_dlls}
        assert any(n.startswith("cublas64") for n in names), \
            f"cuBLAS must be among the preloaded libraries, got {sorted(names)}"

    print(f"selfcheck OK: end-of-speech after {SILENCE_CHUNKS} silent chunks "
          f"(~{SILENCE_CHUNKS * VAD_CHUNK_MS} ms), give-up at {NOSPEECH_CHUNKS} "
          f"(~{NOSPEECH_MS} ms), safety cap {MAX_CHUNKS} (~{MAX_UTTERANCE_S} s) / dictation "
          f"{DICTATION_MAX_CHUNKS} (~{DICTATION_MAX_UTTERANCE_S} s)")


def main() -> None:
    setup_logging()
    ap = argparse.ArgumentParser(description="not-hal wake+listen+transcribe (Track G step 3)")
    ap.add_argument("--selfcheck", action="store_true",
                    help="verify end-of-speech logic without a mic or models, then exit")
    ap.add_argument("--silence-ms", type=int, default=SILENCE_MS,
                    help=f"end-of-speech silence in ms (default {SILENCE_MS}); tune by ear")
    args = ap.parse_args()
    if args.selfcheck:
        _selfcheck()
        return
    try:
        listen(args.silence_ms)
    except KeyboardInterrupt:
        print()  # clean newline after ^C


if __name__ == "__main__":
    main()
