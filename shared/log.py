"""Minimal logging setup for the bridge. Named log.py (not logging.py) to avoid
shadowing the stdlib module."""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

# One directory, so "user-purgeable in one action" is literally one delete.
# Transcripts and replies reach the log through the orchestrator's turn events, which is
# why this is a privacy-relevant location and not just a debugging convenience.
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_FILE = LOG_DIR / "nothal.log"


def setup_logging(level: int = logging.INFO, to_file: bool = True) -> None:
    """Console always; a rotating file as well unless `to_file` is off.

    The daemon used to log to stderr ONLY, which meant its output lived in whatever
    terminal launched it — and when that was a background task, nowhere a person would
    ever look. Diagnosing a live fault meant it had to be reproducible on demand.
    """
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if to_file:
        try:
            LOG_DIR.mkdir(exist_ok=True)
            handlers.append(logging.handlers.RotatingFileHandler(
                LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8"))
        except OSError as exc:                      # read-only dir, full disk, locked file
            # Console-only is a fine outcome; losing the log must never take the daemon
            # down mid-turn. Reported once, on the console, rather than silently.
            logging.getLogger("nothal.log").warning("file logging off (%s)", exc)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )


def _selfcheck() -> None:
    """Logging reaches the file, and a broken destination does not raise."""
    import tempfile

    def reset() -> None:
        """Drop the handlers AND close them — Windows will not delete a file that a
        RotatingFileHandler still holds open."""
        for h in logging.root.handlers[:]:
            logging.root.removeHandler(h)
            h.close()

    global LOG_DIR, LOG_FILE
    with tempfile.TemporaryDirectory() as tmp:
        LOG_DIR, LOG_FILE = Path(tmp), Path(tmp) / "nothal.log"
        reset()
        setup_logging()
        logging.getLogger("nothal.selfcheck").info("hello from the selfcheck")
        assert LOG_FILE.exists(), "no log file was created"
        text = LOG_FILE.read_text(encoding="utf-8")
        assert "hello from the selfcheck" in text, f"message missing from {text!r}"
        reset()

    # An unwritable destination must degrade to console, not raise. A directory path that
    # runs THROUGH an existing file is the realistic shape of this (NotADirectoryError,
    # an OSError) — the same class as a read-only dir or a full disk.
    with tempfile.NamedTemporaryFile(suffix=".notadir", delete=False) as blocker:
        blocked = Path(blocker.name)
    try:
        LOG_DIR = blocked / "logs"
        LOG_FILE = LOG_DIR / "nothal.log"
        setup_logging()      # must not raise
        reset()
    finally:
        blocked.unlink(missing_ok=True)
    print("selfcheck OK: log reaches the rotating file, and an unwritable path degrades "
          "to console instead of raising")


if __name__ == "__main__":
    _selfcheck()
