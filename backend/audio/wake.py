"""Mic -> ring buffer -> openWakeWord -> console.

Listens to the default input device, runs openWakeWord on the live 16 kHz mono
stream, and prints a line when the wake phrase fires. Untriggered audio lives only
in a <=3 s in-RAM ring buffer and is then discarded -- nothing is ever written to
disk.

Cross-platform (macOS is a full peer): sounddevice (PortAudio) and
onnxruntime both run on Windows and macOS; audio-endpoint access is the one
OS-specific seam and sounddevice hides it. No platform branches live here.

Engine: openWakeWord for now. LiveKit Wakeword is a noted future swap (it also feeds
on 16 kHz PCM and reads back a score), so it would drop in behind this same loop.

Run:
    python -m backend.audio.wake              # live: speak the wake phrase
    python -m backend.audio.wake --selfcheck  # no mic needed: verify buffer discipline
"""
from __future__ import annotations

import argparse
import logging
from collections import deque

from shared.config import load_schemas
from shared.log import setup_logging

log = logging.getLogger("nothal.wake")

# Sample rate is an audio schema constant -- load it, never hardcode (the schema is the truth).
SAMPLE_RATE = load_schemas()["audio"]["inbound"]["sampleRateHz"]

# --- implementation choices (NOT schema/spec constants; live here, not in spec) ---
WAKE_MODEL = "hey_jarvis"       # bundled openWakeWord model; a stand-in phrase
BLOCK_MS = 80                   # openWakeWord's native chunk = 1280 samples @ 16 kHz.
BLOCK_SAMPLES = SAMPLE_RATE * BLOCK_MS // 1000          # 1280
THRESHOLD = 0.5                 # ponytail: openWakeWord default; raise it if it false-fires

# Retention bound: <= 3 s of audio, in RAM only, untriggered audio discarded.
BUFFER_SECONDS = 3.0
BUFFER_BLOCKS = int(BUFFER_SECONDS * 1000 // BLOCK_MS)  # 37 blocks = 2.96 s (<= 3 s)


def _selfcheck() -> None:
    """No hardware: prove the ring buffer honours the retention bound (bounded, oldest-out)."""
    assert BUFFER_BLOCKS * BLOCK_MS <= BUFFER_SECONDS * 1000, "ring buffer exceeds 3 s cap"
    ring: deque = deque(maxlen=BUFFER_BLOCKS)
    for i in range(BUFFER_BLOCKS + 50):                # overfill past the cap
        ring.append(i)
    assert len(ring) == BUFFER_BLOCKS, "ring buffer grew past its cap"
    assert ring[0] == 50 and ring[-1] == BUFFER_BLOCKS + 49, "oldest block was not dropped"
    print(f"selfcheck OK: buffer holds {BUFFER_BLOCKS} blocks "
          f"({BUFFER_BLOCKS * BLOCK_MS / 1000:.2f} s <= {BUFFER_SECONDS:.0f} s), oldest-out; "
          f"capture {SAMPLE_RATE} Hz / {BLOCK_MS} ms blocks")


def listen() -> None:
    """Live wake detection on the default mic. Ctrl-C to stop. Needs a microphone."""
    import sounddevice as sd
    import openwakeword.utils
    from openwakeword.model import Model

    log.info("fetching openWakeWord model %r (first run only)...", WAKE_MODEL)
    openwakeword.utils.download_models([WAKE_MODEL])
    model = Model(wakeword_models=[WAKE_MODEL], inference_framework="onnx")

    ring: deque = deque(maxlen=BUFFER_BLOCKS)   # pre-roll handed to speech-to-text
    log.info("listening on default input @ %d Hz -- say the wake phrase (Ctrl-C to stop)",
             SAMPLE_RATE)
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                        blocksize=BLOCK_SAMPLES) as stream:
        while True:
            block, overflowed = stream.read(BLOCK_SAMPLES)
            if overflowed:
                log.warning("input overflow -- audio dropped")
            frame = block[:, 0]                 # (BLOCK_SAMPLES,) int16 mono
            ring.append(frame)                  # bounded: untriggered audio falls out and is gone
            for name, score in model.predict(frame).items():
                if score >= THRESHOLD:
                    print(f"WAKE  {name}  score={score:.2f}")


def main() -> None:
    setup_logging()
    ap = argparse.ArgumentParser(description="Listen for the wake word")
    ap.add_argument("--selfcheck", action="store_true",
                    help="verify ring-buffer discipline without a microphone, then exit")
    args = ap.parse_args()
    if args.selfcheck:
        _selfcheck()
        return
    try:
        listen()
    except KeyboardInterrupt:
        print()  # clean newline after ^C


if __name__ == "__main__":
    main()
