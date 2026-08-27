"""Offline check for the island's motion logic — `python -m frontend.overlay_check`.

decode.py's selfcheck covers the Qt-free half (framing, the reducer). This covers the half
that has actually produced the bugs: the interplay between the island growing, the text
scrolling, and the typewriter revealing words into a box that is still moving.

Runs on Qt's `offscreen` platform, so it needs no display and opens no window.

The invariant is the one a person actually sees: REVEALED TEXT NEVER RENDERS OUTSIDE THE
BLACK. It is measured against the *animated* height and y, not their target values, because
the whole class of bug here is the background lagging its contents. Two failure shapes:

    short  — the island is not yet tall enough for the lines already revealed
    below  — the newest line has been pushed past the island's inner bottom edge

Both were live defects: the Canvas silhouette repainted asynchronously and left a freshly
wrapped line over the desktop, and later the reveal timer gated on the island's GROWTH but
not on its SCROLL, so past three lines words landed while the text was still sliding. Delete
the gate in Overlay.qml and this check fails loudly — that is the point of it.
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import PySide6  # noqa: E402

# PySide6's DLLs are not on PATH when it is imported as a library (see __main__.py).
_d = os.path.dirname(PySide6.__file__)
os.environ["PATH"] = _d + os.pathsep + os.environ.get("PATH", "")
try:
    os.add_dll_directory(_d)
except (AttributeError, OSError):
    pass

from PySide6.QtCore import QObject, QUrl  # noqa: E402
from PySide6.QtGui import QFontDatabase, QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402

from shared import settings  # noqa: E402
from frontend.decode import targets  # noqa: E402
from frontend.model import OverlayModel  # noqa: E402
from frontend.settings_model import SettingsModel  # noqa: E402
from frontend import al  # noqa: E402

HERE = Path(__file__).resolve().parent
# Long enough to pass maxLines, so growth AND scroll are both exercised.
REPLY = ("The agent confirms the lease renews at the current rent for a further twelve "
         "months and they need your signature on the renewal by Friday afternoon at the "
         "latest, otherwise the holding deposit is forfeited.")


def _pump(app, ms: int) -> None:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.002)


def main() -> int:
    app = QGuiApplication([])
    # The offscreen platform ships no fonts, so the bundled family is what makes the metrics
    # real. Without it every line count below would be measured against a fallback. Load
    # whatever .ttf ships rather than a hard-coded name — the app face is swappable (Inter →
    # Hanken Grotesk, 2026-07-24), and these checks are about line-wrapping, not the face.
    fonts = sorted((HERE / "fonts").glob("*.ttf"))
    assert fonts, "no bundled font found — line metrics would be meaningless"
    for f in fonts:
        assert QFontDatabase.addApplicationFont(str(f)) != -1, f"bundled font {f.name} did not load"

    model = OverlayModel()
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(HERE.parent))
    engine.rootContext().setContextProperty("overlay", model)
    engine.rootContext().setContextProperty("fontFamily", "Inter")
    engine.rootContext().setContextProperty("reducedMotion", False)
    engine.rootContext().setContextProperty("targets", targets())
    # The island draws Al, so it needs the same player + provider the app wires, and `cfg` for
    # the "Show Al in the island" switch. Held in locals for the check's lifetime: a context
    # property does not own the Python object.
    engine.addImageProvider("al", al.AlImageProvider())
    al_player = al.QmlAl(model=model)
    cfg = SettingsModel()
    engine.rootContext().setContextProperty("alPlayer", al_player)
    engine.rootContext().setContextProperty("cfg", cfg)
    engine.load(QUrl.fromLocalFile(str(HERE / "Overlay.qml")))
    assert engine.rootObjects(), "Overlay.qml failed to load"
    win = engine.rootObjects()[0]
    body = win.findChild(QObject, "body")
    sweep = win.findChild(QObject, "sweep")
    assert body is not None and sweep is not None, "Overlay.qml lost its objectNames"

    # --- entrance is a binding on `st`, not an imperative handler ---
    model.apply({"type": "state", "state": "idle"})
    _pump(app, 400)
    assert float(win.property("entrance")) < 0.01, "idle should leave the island faded out"
    model.apply({"type": "state", "state": "thinking"})
    # Sample the WHOLE fade rather than one fixed-time reading. A single _pump(90) of a 220 ms
    # fade reads ~0.79 on this box, but on a slow CI runner (X-01) that pump can overrun the
    # fade and read ~1.0 — a false failure. Catching ANY mid-range frame proves it animated
    # through the middle instead of snapping, and is robust to how fast the runner is.
    saw_mid = False
    end = time.monotonic() + 1.5
    while time.monotonic() < end and float(win.property("entrance")) < 0.99:
        app.processEvents()
        time.sleep(0.002)
        if 0.01 < float(win.property("entrance")) < 0.99:
            saw_mid = True
    assert float(win.property("entrance")) > 0.99, "a live state should fade the island in"
    # Reaching 1.0 alone would also pass if the Behavior were dead and it simply snapped, so
    # the fade is only proven by having seen it part-way.
    assert saw_mid, "the island snapped in rather than fading (no mid-fade frame seen)"


    # --- the status word wipes between words rather than cutting ---
    seen, partial = set(), 0
    for _ in range(60):
        _pump(app, 60)
        word = sweep.property("shown")
        if word:
            seen.add(word)
            partial += 1 if word != sweep.property("wordTo") else 0
    assert partial, "the status word never showed a mid-wipe frame — it is not animating"
    assert len({w for w in seen if len(w) > 3}) > 3, f"the status word never rotated: {seen}"

    # --- the reveal gate ---
    model.apply({"type": "state", "state": "speaking"})
    model.apply({"type": "response", "delta": REPLY})

    line_box = int(win.property("lineBox"))
    pad_bottom, base_h = int(win.property("padBottom")), int(win.property("baseH"))
    max_lines = int(win.property("maxLines"))
    short: list[str] = []
    below: list[str] = []
    revealed, peak_lines = 0, 0
    prev_len = len(body.property("text"))
    start = time.monotonic()
    while time.monotonic() - start < 6.0:
        _pump(app, 6)
        # animH, not the window height — the window is a fixed frame now and the island
        # animates inside it.
        height, y = float(win.property("animH")), float(body.property("y"))
        lines = int(body.property("lineCount"))
        peak_lines = max(peak_lines, lines)
        at = round((time.monotonic() - start) * 1000)
        if lines:
            needed = base_h + (min(lines, max_lines) - 1) * line_box
            if height < needed - 0.5:
                short.append(f"t={at}ms {lines} lines revealed, island {height:.0f}px, "
                             f"needs {needed}px")
            ink_bottom, inner = y + lines * line_box, height - pad_bottom
            if ink_bottom > inner + 0.5:
                below.append(f"t={at}ms {lines} lines, ink to {ink_bottom:.0f}px, "
                             f"inner edge {inner:.0f}px")
        now_len = len(body.property("text"))
        revealed += 1 if now_len > prev_len else 0
        prev_len = now_len

    # Guards against the check quietly measuring nothing — the failure mode that let the
    # ungated scroll ship in the first place.
    assert int(win.property("scrolled")) > 0, "the reply never scrolled; scroll path untested"
    assert peak_lines >= max_lines, f"only reached {peak_lines} lines; growth path untested"
    assert revealed > 5, "no words were revealed; the check measured nothing"
    for line in short[:5] + below[:5]:
        print(f"  {line}", file=sys.stderr)
    assert not short, f"island too short for its own text in {len(short)} frames"
    assert not below, f"text rendered below the island edge in {len(below)} frames"

    # The hidden measurer drives the gate, so it must lay out IDENTICALLY to the visible text.
    # Once everything is revealed the two hold the same string, so any difference in line count
    # is a difference in layout — the exact failure that silently un-gates the reveal.
    measure = win.findChild(QObject, "measure")
    assert measure is not None, "Overlay.qml lost the measure objectName"
    assert measure.property("text") == body.property("text"), "reveal did not finish in time"
    assert int(measure.property("lineCount")) == int(body.property("lineCount")), (
        f"measurer wraps to {measure.property('lineCount')} lines but the visible text wraps "
        f"to {body.property('lineCount')} — they have drifted apart")
    assert float(body.property("contentWidth")) <= float(win.property("textW")) + 0.5, (
        f"text is {body.property('contentWidth'):.0f}px wide in a "
        f"{win.property('textW'):.0f}px column — a long token is overhanging the island")

    # --- the latency instrument must never sit on top of the reply it is timing ---
    # Both readings show at once during the acceptance run, which is the case a flat 96px
    # gutter did not cover. Checked at absurd readings so the guarantee is not luck.
    latency = win.findChild(QObject, "latency")
    assert latency is not None, "Overlay.qml lost the latency objectName"
    model.toggle_latency(True)
    model.apply({"type": "latency", "metric": "feedback", "ms": 88888})
    model.apply({"type": "latency", "metric": "first_word", "ms": 88888})
    _pump(app, 250)
    assert float(win.property("latencyGutter")) > 0, "the gutter did not open for the readout"
    text_right = float(win.property("flare")) + int(win.property("padSide")) \
        + float(win.property("textW"))
    assert float(latency.property("x")) >= text_right - 0.5, (
        f"latency readout starts at x={latency.property('x'):.0f} but the reply runs to "
        f"{text_right:.0f} — the instrument overlaps the text")

    # --- the reclassification is DATA the renderer obeys (D25) ---
    # first_word is 'measured', not a gate, so a first-word reading way over target must NOT
    # colour the readout — while a feedback reading over its gate MUST. If someone flips the
    # colour expression back to treating first_word as a gate, the first assert fails.
    tg = targets()
    assert tg["first_word"]["kind"] == "measured", "test fixture assumes first_word is measured"
    model.apply({"type": "latency", "metric": "feedback", "ms": 200})       # well under gate
    model.apply({"type": "latency", "metric": "first_word", "ms": 99999})   # absurdly over
    _pump(app, 60)
    calm = latency.property("color")
    model.apply({"type": "latency", "metric": "feedback", "ms": 99999})     # now over the gate
    _pump(app, 60)
    hot = latency.property("color")
    assert calm != hot, ("first_word over target coloured the readout — it is 'measured', "
                         "only the feedback GATE may (D25)")

    # --- the reply must not cut the prompt off mid-reveal (D24) ---
    # The live defect: `bodyText` flipped to the reply on the FIRST model delta, the prefix
    # test below it read the new string as "not a continuation" and reset the typewriter to
    # zero — so a prompt past ~11 words lost its tail on every warm turn. Reproduce exactly
    # that: a long prompt, with a reply arriving while it is still typing.
    model.toggle_latency(False)
    model.apply({"type": "state", "state": "listening"})       # clears the turn
    model.apply({"type": "state", "state": "thinking"})
    prompt = ("Can you say how many states there are in the US and interesting facts "
              "about three of them")
    model.apply({"type": "transcript", "text": prompt})
    _pump(app, 300)                                            # a few words in, not finished
    assert body.property("text") != prompt, "prompt revealed too fast to test the gate"
    model.apply({"type": "response", "delta": "There are fifty states. "})
    _pump(app, 150)
    shown = body.property("text")
    assert prompt.startswith(shown) and shown, \
        f"the reply replaced the prompt mid-reveal — island is showing {shown!r}"
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline and not win.property("promptShown"):
        _pump(app, 20)
    assert win.property("promptShown"), "the prompt never finished revealing and holding"
    # The handover is not instant: the island shrinks back to one line first, and the reveal
    # gate holds every word until it has stopped moving. Wait for it rather than guessing.
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline and not body.property("text").startswith("There"):
        _pump(app, 20)
    assert body.property("text").startswith("There"), \
        "the reply never took over once the prompt had had its turn"

    # --- the island hides ITSELF, and never while text is still appearing (D24) ---
    # The daemon owns none of this now. It publishes `idle` when IT is finished; the clock
    # that matters starts when the reveal does.
    dwell = win.findChild(QObject, "answerDwell")
    assert dwell is not None, "Overlay.qml lost the answerDwell objectName"
    model.apply({"type": "response", "delta": REPLY})           # plenty left to reveal
    model.apply({"type": "response", "done": True})
    model.apply({"type": "state", "state": "idle"})             # daemon done, island is not
    _pump(app, 200)
    assert win.property("showing"), "idle blanked an answer that was still being revealed"
    assert not dwell.property("running"), "the dwell started before the text had appeared"
    while time.monotonic() < deadline + 8 and not win.property("revealDone"):
        _pump(app, 20)
    assert win.property("revealDone"), "the reply never finished revealing"
    assert dwell.property("running"), "the dwell did not start once the text had appeared"
    dwell.setProperty("interval", 60)                           # don't sit here for 20 s
    _pump(app, 400)
    assert win.property("hidden") and not win.property("showing"), \
        "the island never hid itself after its dwell"
    assert float(win.property("entrance")) < 0.99, "the island did not begin fading out"

    # --- two dwells, chosen by the daemon, timed by the user (D43) ---
    # A turn that ACTED has nothing to read, so it goes quickly; one that ANSWERED stays long
    # enough to walk back to the desk. The daemon sends the word, the seconds are the setting.
    schema = settings.schema()["settings"]
    for key in ("dwell_quick", "dwell_slow"):
        for choice in schema[key]["choices"]:
            # The island reads the number off the FRONT of the choice, so every choice must
            # start with one. This is the guard that makes that shortcut safe: add "Never" to
            # the schema and it fails HERE, not silently on someone's screen.
            head = re.match(r"[\d.]+", choice)
            assert head and float(head.group()) > 0, \
                f"{key} choice {choice!r} must start with a duration the island can read"

    def _dwell_ms(kind):
        model.apply({"type": "state", "state": "listening"})     # clear the last turn
        model.apply({"type": "response", "delta": "Opening Spotify."})
        model.apply({"type": "response", "done": True, "dwell": kind})
        _pump(app, 120)
        return int(dwell.property("interval"))

    was_q, was_s = cfg.values.get("dwell_quick"), cfg.values.get("dwell_slow")
    cfg.set("dwell_quick", "1.5 s")
    cfg.set("dwell_slow", "40 s")
    _pump(app, 60)
    assert _dwell_ms("quick") == 1500, f"an action must take the quick dwell: {_dwell_ms('quick')}"
    assert _dwell_ms("slow") == 40000, f"an answer must take the slow dwell: {_dwell_ms('slow')}"
    # An unstamped reply is an ANSWER. This is the direction that must never fail open: a daemon
    # that forgot to stamp, or an older one that cannot, keeps the readable dwell.
    model.apply({"type": "state", "state": "listening"})
    model.apply({"type": "response", "delta": "It is noon."})
    model.apply({"type": "response", "done": True})
    _pump(app, 120)
    assert int(dwell.property("interval")) == 40000, "an unstamped reply must dwell as an answer"
    # ...and a setting that cannot be read falls back to the shipped default rather than to zero,
    # which would blink the island away the instant the text landed.
    cfg.set("dwell_quick", "not a duration")
    _pump(app, 60)
    assert _dwell_ms("quick") == 2500, f"a junk setting must fall back: {_dwell_ms('quick')}"
    cfg.set("dwell_quick", was_q)
    cfg.set("dwell_slow", was_s)
    _pump(app, 60)

    # --- Esc dismisses locally, without waiting for the daemon (D24) ---
    model.apply({"type": "state", "state": "thinking"})
    model.apply({"type": "transcript", "text": "what's the weather"})
    _pump(app, 200)
    assert win.property("showing") and not win.property("hidden"), "a new turn must come back"
    model.dismissed.emit()                                      # what __main__.py's Esc does
    model.apply({"type": "state", "state": "idle"})             # ...and the daemon's dismiss
                                                                # handler then publishes idle
    _pump(app, 50)
    assert win.property("hidden") and not win.property("showing"), \
        "a dismiss must hide the island immediately — it never waits on the daemon"

    # ...and when a NEW turn re-opens the pill, it must appear at the CORRECT width, not fade in
    # at the last turn's width and animate down (the wide-then-shrink bug). Since idle no longer
    # clears the turn (D24), animW sits at the wide value while hidden; the fix snaps size while
    # the pill is not fully shown, so it is right the instant it re-appears — for ANY re-open
    # path, not just this one.
    deadline2 = time.monotonic() + 1.0
    while time.monotonic() < deadline2 and float(win.property("entrance")) > 0.02:
        _pump(app, 20)
    assert float(win.property("entrance")) < 0.05, "the pill never finished hiding"
    assert float(win.property("animW")) > 400, \
        f"precondition: the hidden pill should still be wide (animW={win.property('animW'):.0f})"
    compact_w = float(win.property("compactW")) + 2 * float(win.property("flare"))
    model.apply({"type": "state", "state": "listening"})        # a new turn re-opens the pill
    _pump(app, 40)                                              # far less than the resize anim
    assert win.property("showing"), "a new turn must re-open the pill"
    assert float(win.property("animW")) <= compact_w + 8, (
        f"the pill re-appeared at animW={win.property('animW'):.0f}px and is animating down to "
        f"{compact_w:.0f} — its width must be set BEFORE it appears, not after (re-open bug)")

    # --- boot island (status.json v0.7.0): a NARROW pill showing the shared circular loader
    # (Spinner.qml, the settings Test button's own mark), no status word and no Al, until the
    # daemon clears `booting` when warm-up finishes. ---
    boot_spin = win.findChild(QObject, "bootSpinner")
    assert boot_spin is not None, "Overlay.qml lost the bootSpinner objectName"
    model.apply({"type": "state", "state": "listening"})        # clear any leftover turn first
    model.apply({"type": "state", "state": "booting"})
    _pump(app, 500)                                             # let the narrow width settle
    assert win.property("showing") and win.property("booting"), "booting must show the boot island"
    assert boot_spin.property("visible") and boot_spin.property("running"), \
        "the boot loader must be visible and spinning while booting"
    boot_w = float(win.property("bootW")) + 2 * float(win.property("flare"))
    assert abs(float(win.property("animW")) - boot_w) < 2, \
        f"the boot island must be its narrow bootW, got animW={win.property('animW'):.0f}"
    assert boot_w < compact_w, "the boot island must be narrower than the listening pill"
    al_item = win.findChild(QObject, "al")
    assert al_item is None or not al_item.property("visible"), \
        "Al must be hidden during boot — the circular loader replaces her"

    # Warm-up done -> `idle` is a HIDE. WHILE STILL VISIBLE, the boot pill must fade AT ITS NARROW
    # WIDTH and keep Al hidden — it must NOT balloon to the compact pill and flash Al on the way
    # out (the boot-flash bug). The `bootLatch` holds the boot look through the fade; once fully
    # hidden the width may snap to whatever is next (invisible, so it does not matter).
    al_out = win.findChild(QObject, "al")
    model.apply({"type": "state", "state": "idle"})
    flashed = ""
    for _ in range(40):
        _pump(app, 8)
        if float(win.property("entrance")) < 0.02:
            break                                               # fully faded — later changes unseen
        if float(win.property("animW")) > boot_w + 4:
            flashed = f"grew to animW={win.property('animW'):.0f}px (boot is {boot_w:.0f}px)"
            break
        if al_out is not None and al_out.property("visible"):
            flashed = "Al became visible"
            break
    assert not flashed, f"the boot island flashed while fading out — {flashed}"
    # ...once fully hidden the latch clears and the loader stops.
    deadline_b = time.monotonic() + 1.0
    while time.monotonic() < deadline_b and float(win.property("entrance")) > 0.02:
        _pump(app, 20)
    _pump(app, 20)
    assert not win.property("booting") and not win.property("bootLatch"), \
        "a fully-hidden boot island must clear booting and the latch"
    assert not boot_spin.property("running"), "the loader must stop once the boot island is hidden"

    # --- both edges must move at the same rate, and the island must stay inside its frame ---
    # The island is centred in a FIXED window, so its centre is a constant no matter how wide
    # it is. Any drift means one edge is moving before the other — which is what a native
    # window move racing a native resize looked like (the pill contracted faster on the left).
    # The containment assert covers the other half: the silhouette is drawn at animH, so if
    # that ever exceeded the frame its bottom corners would be clipped away mid-growth.
    # `listening` is what contracts the island (D24): opening a capture window IS the clear
    # (status.json `clearsTurn`), so the wide text pill drops to the compact wave pill. This
    # assertion has now been round the houses — `listening`, then `idle` when the follow-up
    # window made an open mic mean something else, and back again now that every capture is
    # user-initiated and `idle` merely means the daemon is free.
    model.apply({"type": "state", "state": "thinking"})
    model.apply({"type": "response", "delta": REPLY})
    _pump(app, 500)                                # let it open out to full width first
    model.apply({"type": "state", "state": "listening"})
    frame_w, frame_h = float(win.property("width")), float(win.property("height"))
    frame_x = float(win.property("x"))
    centre, drift, widths = frame_w / 2, 0.0, []
    start = time.monotonic()
    while time.monotonic() - start < 0.7:
        _pump(app, 6)
        x, w, h = (float(win.property("islandX")), float(win.property("animW")),
                   float(win.property("animH")))
        drift = max(drift, abs((x + w / 2) - centre))
        widths.append(w)
        # The load-bearing one. A centred island samples as perfectly centred even while the
        # real window tears, because the tear happens below Qt in the compositor — so this
        # cannot be caught by watching the island. What CAN be checked is that the tear has
        # nothing left to happen to: the window's own geometry must never change at all.
        assert (float(win.property("width")), float(win.property("height")),
                float(win.property("x"))) == (frame_w, frame_h, frame_x), \
            "the window itself resized or moved — a native move can race a native resize again"
        assert x >= -0.5 and x + w <= frame_w + 0.5, f"island escaped the frame sideways (x={x})"
        assert h <= frame_h + 0.5, f"island {h}px tall in a {frame_h}px frame — corners clipped"
    assert max(widths) - min(widths) > 50, "the island never actually contracted; nothing measured"
    print(f"contraction: {len(widths)} frames, {max(widths) - min(widths):.0f}px of travel, "
          f"worst centre drift {drift:.3f}px")
    assert drift < 0.5, f"the island's two edges moved at different rates (drift {drift:.2f}px)"

    # --- U-01: the native filter routes WM_SETTINGCHANGE to the reduced-motion re-query ---
    # Pure Win32 message dispatch, no window needed. Proves the live-update wiring survives an
    # edit that (e.g.) re-adds an `if not armed: return` guard and silently kills settings.
    # Windows-only: `ctypes.wintypes` RAISES on import elsewhere, so an unguarded import here
    # takes the whole check down on any other platform — and everything above this line is
    # portable and is the valuable part. macOS has no WM_SETTINGCHANGE; its reduced-motion
    # signal is a different one and wants its own check. Printed, not skipped silently.
    if sys.platform == "win32":
        from ctypes import addressof, wintypes

        from frontend.__main__ import _WM_SETTINGCHANGE, DismissKey
        settings_hits: list[int] = []
        filt = DismissKey(lambda: None, on_settings=lambda: settings_hits.append(1))
        sc = wintypes.MSG(); sc.message = _WM_SETTINGCHANGE
        handled, _ = filt.nativeEventFilter(b"windows_generic_MSG", addressof(sc))
        assert settings_hits == [1], "WM_SETTINGCHANGE must reach the re-query even while Esc is disarmed"
        assert handled is False, "a settings change must not be consumed — every window needs it"
        other = wintypes.MSG(); other.message = 0x0000
        filt.nativeEventFilter(b"windows_generic_MSG", addressof(other))
        assert settings_hits == [1], "only WM_SETTINGCHANGE should trigger the re-query"
    else:
        print(f"SKIPPED on {sys.platform}: WM_SETTINGCHANGE dispatch (Win32-only)")

    # --- expanded view / peek (D27) ---
    # A settled answer is peekable; peeking grows the island to the peek size, pauses the dwell,
    # and Esc collapses the peek BEFORE it would dismiss the island.
    dwell.setProperty("interval", 20000)                       # don't let it fire during the checks
    model.apply({"type": "state", "state": "listening"})       # clear the turn
    model.apply({"type": "state", "state": "thinking"})
    model.apply({"type": "transcript", "text": "Summarise the leasing email."})
    model.apply({"type": "response", "delta": REPLY})
    model.apply({"type": "response", "done": True})
    model.apply({"type": "state", "state": "idle"})
    # Wait for the REPLY (not just the prompt) to take over and finish revealing, so the dwell is
    # genuinely eligible — otherwise the reply is still typing when we peek and the dwell can't run.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not (
            win.property("revealDone") and body.property("text").startswith("The agent")):
        _pump(app, 20)
    assert win.property("revealDone") and body.property("text").startswith("The agent"), \
        "the reply never took over and finished revealing"
    assert win.property("peekable"), "a settled reply must be peekable"
    assert dwell.property("running"), "precondition: the dwell should be running before the peek"

    peek_panel = win.findChild(QObject, "peekPanel")
    assert peek_panel is not None, "Overlay.qml lost the peekPanel objectName"
    flare = float(win.property("flare"))
    peek_w = float(win.property("peekW"))
    peek_min, peek_max = float(win.property("peekMinH")), float(win.property("peekMaxH"))
    win.setProperty("peeking", True)
    # The peek REVEALS (clip), never REFLOWS (D27): the reply viewport is pinned to the FINAL height
    # (bodyHeight) from the first frame, so the fade bars never flash mid-grow. Capture it while the
    # grow is still animating; re-tie the layout to the animating height and this collapses toward 0.
    _pump(app, 40)                                             # ~2 frames — the grow is NOT settled yet
    peek_flick = win.findChild(QObject, "peekReply")
    assert peek_flick is not None, "Overlay.qml lost the peekReply objectName"
    early_flick_h = float(peek_flick.property("height"))
    _pump(app, 400)                                            # ...now let it settle
    aw, ah = float(win.property("animW")), float(win.property("animH"))
    assert abs(early_flick_h - float(peek_flick.property("height"))) < 1.5, (
        f"the peek reply reflowed during the grow ({early_flick_h:.0f} -> "
        f"{float(peek_flick.property('height')):.0f}px) — the fade bars will flash mid-transition")
    nat = float(peek_panel.property("naturalHeight"))
    assert abs(aw - (peek_w + 2 * flare)) < 1, f"island did not widen to the peek width (animW={aw:.0f})"
    assert abs(ah - max(peek_min, min(peek_max, nat))) < 1, \
        f"peek height {ah:.0f} is not the clamped natural height {nat:.0f} in [{peek_min:.0f},{peek_max:.0f}]"
    assert float(win.property("peekFade")) > 0.99, "the peek content never faded in"
    assert win.property("showing"), "the island must stay showing while peeked"
    assert not dwell.property("running"), "the dwell must pause while peeking"

    # Bug fix (D27): a new capture clears the reply -> not peekable -> the peek must let go, so the
    # island returns to the compact view instead of a stuck, large, empty box mid-turn.
    model.apply({"type": "state", "state": "listening"})       # the hotkey / a new turn opens the mic
    _pump(app, 100)
    assert not win.property("peekable"), "listening clears the reply, so it is no longer peekable"
    assert not win.property("peeking"), "a new capture must drop out of the peek"

    # Esc from a peek dismisses the island OUTRIGHT (D27) — not back to the compact 3-line view,
    # where the whole answer can't be read.
    model.apply({"type": "state", "state": "thinking"})
    model.apply({"type": "transcript", "text": "and again"})
    model.apply({"type": "response", "delta": REPLY})
    model.apply({"type": "response", "done": True})
    model.apply({"type": "state", "state": "idle"})
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not (
            win.property("revealDone") and body.property("text").startswith("The agent")):
        _pump(app, 20)
    win.setProperty("peeking", True)
    _pump(app, 200)
    assert win.property("peeking"), "precondition: peeking before the Esc test"
    model.dismissed.emit()                                     # Esc
    # The dismiss fades out AT the peek size — peeking (and thus the size) reset only once fully
    # hidden, so there is no on-screen shrink first. hidden/showing flip at once; peeking waits.
    assert win.property("hidden") and not win.property("showing"), "Esc must hide the island at once"
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and win.property("visible"):
        _pump(app, 20)
    assert not win.property("visible"), "the island never finished fading out after Esc"
    assert not win.property("peeking"), \
        "peeking must reset only once fully hidden, so the dismiss fades at peek size (no shrink, D27)"

    # --- IslandHitTest: nothing to peek -> the whole island is click-through (D27) ---
    # The load-bearing gate: with no answer to peek, the island must NEVER eat a click — it sits
    # over a live app's tab strip. Testable headless because the not-peekable branch returns before
    # any GetWindowRect; a tiny stub stands in for the QML window.
    # Windows-only, same reason as the block above. macOS has no WM_NCHITTEST at all, so the
    # island's click-through there is a different mechanism — and until it exists, the
    # frame swallows every click over the top-centre of the screen. Its own check comes with it.
    if sys.platform == "win32":
        from ctypes import addressof, wintypes

        from frontend.__main__ import _HTTRANSPARENT, _WM_NCHITTEST, IslandHitTest

        class _StubWin:
            def __init__(self, peekable, peeking):
                self._props = {"peekable": peekable, "peeking": peeking}
            def property(self, name):
                return self._props.get(name, 0)
            def width(self):
                return 100
            def height(self):
                return 100

        ht = IslandHitTest(_StubWin(False, False)); ht.set_hwnd(0x1234)
        m = wintypes.MSG(); m.message = _WM_NCHITTEST; m.hWnd = 0x1234; m.lParam = (10 << 16) | 10
        assert ht.nativeEventFilter(b"windows_generic_MSG", addressof(m)) == (True, _HTTRANSPARENT), \
            "a non-peekable island must return HTTRANSPARENT — fully click-through over a live app"
        other_win = wintypes.MSG(); other_win.message = _WM_NCHITTEST; other_win.hWnd = 0x9999
        assert ht.nativeEventFilter(b"windows_generic_MSG", addressof(other_win)) == (False, 0), \
            "the hit-test must ignore messages for other windows"
    else:
        print(f"SKIPPED on {sys.platform}: IslandHitTest click-through (Win32-only)")

    # --- dictation states (D2): the island is a pure status indicator, no reply body ---
    # Recording reuses `listening` (covered above); these are the three states after it. The
    # daemon publishes transcribing -> transforming -> pasted -> idle; the overlay shows a steady
    # status word for the first two, then a latched "Pasted ✓" beat, then hides itself. `idle`
    # lands while the confirmation is still up, so it must not blank it (the latch, not `st`, holds).
    status = win.findChild(QObject, "statusWord")
    paste_dwell = win.findChild(QObject, "pasteDwell")
    assert status is not None and paste_dwell is not None, "Overlay.qml lost a dictation objectName"

    model.apply({"type": "state", "state": "listening"})       # recording — clears any prior turn
    model.apply({"type": "state", "state": "transcribing"})
    _pump(app, 60)
    assert win.property("showing") and win.property("open"), "transcribing must open the island"
    assert status.property("visible") and status.property("text") == "Transcribing…", \
        f"transcribing should show a steady status word, got {status.property('text')!r}"
    assert win.property("bodyText") == "", "dictation must not render a text body"

    # A stray transcript during dictation must NOT surface as a prompt: the daemon does not
    # broadcast it, but if it ever did the status word must still win (and it must not pollute
    # the prompt history the assistant reads).
    model.apply({"type": "transcript", "text": "um so like hello there"})
    _pump(app, 30)
    assert win.property("bodyText") == "", "a dictation transcript leaked into the body"

    model.apply({"type": "state", "state": "transforming"})
    _pump(app, 40)
    assert status.property("text") == "Tidying…", \
        f"transforming should show 'Tidying…', got {status.property('text')!r}"

    model.apply({"type": "state", "state": "pasted"})
    _pump(app, 40)
    assert win.property("pasted"), "the pasted state must latch the confirmation"
    assert status.property("text") == "Pasted", \
        f"pasted should show 'Pasted', got {status.property('text')!r}"
    mark = win.findChild(QObject, "pasteMark")
    assert mark is not None and mark.property("visible"), \
        "the pasted state must show its Lucide check"
    assert paste_dwell.property("running"), "the paste dwell must be counting down"
    model.apply({"type": "state", "state": "idle"})            # daemon done — island stays for the beat
    _pump(app, 40)
    assert win.property("showing") and win.property("pasted"), \
        "idle blanked the paste confirmation — the latch (not st) must hold it through the dwell"

    paste_dwell.setProperty("interval", 40)                    # don't sit here for the full beat
    _pump(app, 200)
    assert not win.property("pasted") and win.property("hidden") and not win.property("showing"), \
        "the island never hid itself after the paste confirmation"

    classic = check_al_switch(app, win, cfg, model)

    print(f"selfcheck OK: entrance binds to state, status word wipes and rotates, dictation runs "
          f"transcribing->transforming->pasted->hide, across "
          f"{revealed} revealed words at up to {peak_lines} lines (scrolled "
          f"{win.property('scrolled')}) no text ever rendered outside the island, and "
          f"Al off restores the pre-Al island exactly ({classic})")
    return 0


def check_al_switch(app, win, cfg, model) -> str:
    """"Show Al in the island" OFF must restore the island EXACTLY, not approximately.

    Al costs the pill a 76px left column and cuts the wave from 20 samples to 14, so "you can
    turn her off" is a claim about geometry that would rot the first time one of those numbers
    moved. This recomputes the pre-Al formulas from the island's own constants and demands the
    live values match — so the classic theme cannot drift while nobody is looking.
    """
    def g(name):
        return win.property(name)

    was = cfg.values.get("al_in_island", True)
    try:
        model.apply({"type": "state", "state": "listening"})
        _pump(app, 120)

        cfg.set("al_in_island", False)
        _pump(app, 160)
        assert g("alOn") is False, "the switch did not reach the island"
        pad, flare, gutter = g("padSide"), g("flare"), g("latencyGutter")
        # ...the originals, written out rather than referenced, so a change to the live
        # expression cannot quietly redefine what "the same as before" means.
        assert g("waveCount") == 20, f"classic wave is 20 samples, got {g('waveCount')}"
        assert g("leftInset") == pad, f"classic inset is padSide, got {g('leftInset')}"
        assert g("compactW") == round(g("waveWidth")) + 20, \
            f"classic compact pill must hug the wave: {g('compactW')}"
        assert abs(g("textW") - (g("openW") - 2 * pad - gutter)) < 0.01, \
            f"classic text column changed: {g('textW')}"
        al_item = win.findChild(QObject, "al")
        assert al_item is not None and al_item.property("visible") is False, \
            "Al is still drawn with the switch off"
        classic_w = int(g("islandW"))

        cfg.set("al_in_island", True)
        _pump(app, 160)
        assert g("alOn") is True
        assert g("waveCount") == 14, f"Al's wave is cut to 14, got {g('waveCount')}"
        assert g("leftInset") == g("alCol"), "Al's column must own the left inset"
        # she sits inside her own column, and the wave starts after it — the overlap that left
        # no gap between them was exactly this going wrong.
        assert g("alLeft") + g("alPx") <= g("alCol"), "Al's cell overflows her column"
        bars = win.findChild(QObject, "bars")
        assert bars is not None
        wave_x = float(bars.property("x")) - float(g("islandX"))
        assert wave_x >= flare + g("alCol") - 0.5, \
            f"the wave starts inside Al's column ({wave_x} < {flare + g('alCol')})"
        assert al_item.property("visible") is True
        # Whole pixels. `islandX` is a real, so an odd pill width would put her on a half pixel
        # and blur every cell edge — the sprite must be snapped whatever the width works out to.
        for state, mic in (("thinking", 0.0), ("listening", 0.5)):
            model.apply({"type": "state", "state": state})
            if mic: model.apply({"type": "mic", "level": mic})
            _pump(app, 140)
            gx, gy = float(al_item.property("x")), float(al_item.property("y"))
            assert gx == int(gx) and gy == int(gy),                 f"Al is on a half pixel in {state}: {gx},{gy} (islandW {g('islandW')})"
        # ...ending on `listening`, so the width quoted below is the COMPACT pill in both themes.
        al_w = int(g("islandW"))
        return f"classic {classic_w}px vs Al {al_w}px compact"
    finally:
        cfg.set("al_in_island", was)
        _pump(app, 120)


if __name__ == "__main__":
    raise SystemExit(main())
