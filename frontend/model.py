"""Qt-side model — a thin QObject wrapper over the Qt-free OverlayState (decode.py).

Deliberately thin: every rule about what the island shows lives in decode.py so it stays
testable in CI without PySide6. This file only exposes those fields as bindable properties.
"""
from __future__ import annotations

from PySide6.QtCore import Property, QObject, Signal

from frontend.decode import OverlayState


class OverlayModel(QObject):
    """One `changed` signal for the lot — the island is small enough that re-evaluating
    every binding per message is far cheaper than per-field plumbing."""

    changed = Signal()
    # D24: the user dismissed the island (Esc). A one-shot signal rather than a property,
    # because it is an EVENT — the island hides itself on it and the daemon is told separately.
    dismissed = Signal()

    def __init__(self, show_latency: bool = False) -> None:
        super().__init__()
        self._s = OverlayState()
        self._show_latency = show_latency
        self._revealing = False

    def feed_lost(self) -> None:
        """The daemon went away. Show nothing rather than a frozen last frame — `idle` alone
        cannot say this any more, since an answer now deliberately outlives it (D24)."""
        self._s.clear_turn()
        self._s.state = "idle"
        self._s.mic = 0.0
        self.changed.emit()

    # --- a UI preference, not feed state, so it lives here rather than in OverlayState ---

    def toggle_latency(self, on: bool) -> None:
        self._show_latency = on
        self.changed.emit()

    @Property(bool, notify=changed)
    def showLatency(self) -> bool:
        return self._show_latency

    # Written BY the overlay, read by everything else: is the island's typewriter still laying
    # down the reply? Not feed state — the daemon finishes streaming well before the reveal
    # catches up, and only the overlay can see how far along it is. It lives here because it is
    # the one place a second surface can read it: Al mimes the reply as it lands, on both the
    # island and the settings bar.
    #
    # It has its OWN notify signal rather than sharing `changed`. `changed` is deliberately
    # blanket — every feed field re-evaluates on it — and the overlay computes `revealing` from
    # two of those fields, so routing it through `changed` made the write invalidate its own
    # inputs: Qt reported a binding loop on `replyReady`. Harmless in effect (the setter below
    # is idempotent) but a real cycle, and this breaks it.
    revealingChanged = Signal()

    def _get_revealing(self) -> bool:
        return self._revealing

    def _set_revealing(self, on: bool) -> None:
        if on != self._revealing:
            self._revealing = on
            self.revealingChanged.emit()

    revealing = Property(bool, _get_revealing, _set_revealing, notify=revealingChanged)

    def apply(self, msg: dict) -> None:
        self._s.apply(msg)
        self.changed.emit()

    def set_mic(self, level: float) -> None:
        """Used by the feed's watchdog to drop the bars when mic frames stop arriving."""
        if self._s.mic != level:
            self._s.mic = level
            self.changed.emit()

    @Property(str, notify=changed)
    def state(self) -> str:
        return self._s.state

    @Property(str, notify=changed)
    def transcript(self) -> str:
        return self._s.transcript

    @Property(str, notify=changed)
    def reply(self) -> str:
        return self._s.reply

    @Property(bool, notify=changed)
    def done(self) -> bool:
        """The reply is complete (response done). Drives the peek's 'generating…' cue (D27)."""
        return self._s.done

    @Property(str, notify=changed)
    def model(self) -> str:
        """The model that produced the reply — the peek footer names it (D34)."""
        return self._s.model

    @Property(int, notify=changed)
    def tokens(self) -> int:
        """The turn's total input+output tokens — shown beside the model in the peek footer (D34)."""
        return self._s.tokens

    @Property(str, notify=changed)
    def dwell(self) -> str:
        """"quick" if this turn ACTED, "slow" if it answered (D43). The island turns the word
        into seconds using the user's own setting — the daemon never names a duration."""
        return self._s.dwell

    @Property(float, notify=changed)
    def mic(self) -> float:
        return self._s.mic

    @Property(str, notify=changed)
    def tool(self) -> str:
        """The Contract-T tool running right now, named for a person, or "" between calls (D38).
        NOTHING RENDERS THIS YET — the island's treatment of it is the design pass owed on the
        tool-activity indicator (STATE, Track T). The seam is here so that pass is a QML change
        and nothing else."""
        return self._s.tool

    @Property(str, notify=changed)
    def error(self) -> str:
        return self._s.error

    @Property(float, notify=changed)
    def feedbackMs(self) -> float:
        return self._s.feedback_ms

    @Property(float, notify=changed)
    def firstWordMs(self) -> float:
        return self._s.first_word_ms

    @Property(list, notify=changed)
    def history(self) -> list:
        """Prior prompts this session, oldest first — for the expanded view (D22, which
        superseded D14's ⌄ caret). No consumer yet; RAM only, never written to disk."""
        return self._s.history
