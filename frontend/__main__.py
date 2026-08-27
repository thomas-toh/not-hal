"""The Teleprompter: a PySide6 + QML overlay on the daemon's status feed.

A dumb subscriber: it renders whatever arrives on the localhost feed and never
drives the voice loop. Start it before or after the daemon — it reconnects either way.

Run:
    python -m teleprompter                      # render the live daemon's feed
    python -m backend.broadcaster --fake         # ...or drive it with NO audio/mic/models
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import PySide6

# Store-Python quirk (NOTES.md; same class as listen.py's CUDA-DLL fix): Qt's QML plugin
# loader does not search the PySide6 package dir where the Qt6*.dll live, so qtquick2plugin
# fails with "module could not be found". Put it on the search path BEFORE importing Qt.
_pyside_dir = os.path.dirname(PySide6.__file__)
os.environ["PATH"] = _pyside_dir + os.pathsep + os.environ.get("PATH", "")
try:
    os.add_dll_directory(_pyside_dir)
except (AttributeError, OSError):
    pass

from PySide6.QtCore import QAbstractNativeEventFilter, QUrl              # noqa: E402
from PySide6.QtQml import (QQmlApplicationEngine, QQmlComponent,          # noqa: E402
                           QQmlEngine)
from PySide6.QtWidgets import QApplication, QSystemTrayIcon              # noqa: E402

from frontend.decode import HOST, PORT, m_dismiss, targets           # noqa: E402
from frontend.feed import Feed                                       # noqa: E402
from frontend.model import OverlayModel                              # noqa: E402
from frontend.settings_model import SettingsModel                    # noqa: E402
from frontend.tray import Tray                                       # noqa: E402
from frontend import al                                             # noqa: E402

log = logging.getLogger("nothal.teleprompter")

FONTS_DIR = Path(__file__).resolve().parent / "fonts"

# The design's face is **Inter**, bundled beside this package and registered at startup —
# so it needs no system install and travels to the Mac unchanged. (Inter → Hanken Grotesk
# → Archivo over 2026-07-24/25, then back to Inter on 2026-07-31.)
# The rest of the chain only matters if the
# bundled file goes missing: QML's font.family takes a single name and Qt substitutes silently (on a
# stock Windows box an absent family lands on Tahoma), so we walk the chain here and say out
# loud which one won.
FONT_STACK = ["Inter", "Segoe UI Variable Text", "Segoe UI", "Helvetica Neue", "Arial"]


# (There was a global +0.015 em tracking here, applied to the application font so every QML Text
# inherited it. Removed 2026-07-31 — it rode the previous face and Inter is spaced well
# enough without it. Per-element letterSpacing, like CodeLabel's -0.2, is untouched.)


def load_bundled_fonts() -> None:
    """Register every font shipped in frontend/fonts/ — no system install required.
    Needs a QApplication to exist first."""
    from PySide6.QtGui import QFontDatabase
    for path in sorted(FONTS_DIR.glob("*.ttf")):
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id == -1:
            log.warning("could not load bundled font %s", path.name)
        else:
            log.info("bundled font %s -> %s", path.name,
                     ", ".join(QFontDatabase.applicationFontFamilies(font_id)))


def pick_font() -> str:
    """First family in FONT_STACK that is actually available (bundled or installed)."""
    from PySide6.QtGui import QFontDatabase
    available = set(QFontDatabase.families())
    for family in FONT_STACK:
        if family in available:
            if family != FONT_STACK[0]:
                log.warning("%r unavailable — falling back to %r", FONT_STACK[0], family)
            return family
    log.warning("none of %s available; letting Qt choose", FONT_STACK)
    return ""


def check_qml_available() -> bool:
    """PySide6 can install HALF-completed on Windows without Long Paths: `import PySide6`
    succeeds while the deeply-nested QML module trees never extract, so the failure surfaces
    later as a baffling "module QtQuick is not installed". That cost an hour once. PySide6 is a
    core dependency, so any machine can inherit the half-install; say so plainly."""
    qml_dir = Path(_pyside_dir) / "qml" / "QtQuick"
    if qml_dir.is_dir():
        return True
    log.error("PySide6 is installed but its QML modules are missing (%s not found).", qml_dir)
    log.error("This is the Windows long-path half-install. Enable Long Paths, then:")
    log.error("    python -m pip install --force-reinstall --no-cache-dir PySide6")
    return False


def reduced_motion() -> bool:
    """Windows' "Show animations" accessibility setting — the desktop equivalent of CSS
    `prefers-reduced-motion`, which the mockup honoured. Off => the island's transitions go
    instant. Fails open (motion allowed) if the query fails."""
    if sys.platform != "win32":
        return False
    import ctypes
    SPI_GETCLIENTAREAANIMATION = 0x1042
    enabled = ctypes.c_int(1)
    ok = ctypes.windll.user32.SystemParametersInfoW(
        SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0)
    return bool(ok) and not enabled.value


# Win32 (winuser.h). The id is this process's own — the daemon's doors live in a different
# process and cannot collide. RegisterHotKey with a NULL window requires id < 0xC000.
_VK_ESCAPE, _MOD_NOREPEAT, _WM_HOTKEY, _ESC_ID = 0x1B, 0x4000, 0x0312, 0x0E5C
_WM_SETTINGCHANGE = 0x001A
_WM_NCHITTEST, _HTTRANSPARENT = 0x0084, -1


class DismissKey(QAbstractNativeEventFilter):
    """The overlay's one Win32 message hook: bare Esc + a re-read of the machine
    settings the overlay mirrors into QML.

    Esc is registered ONLY while the island is on screen and released the instant it hides.
    The daemon used to attempt exactly this discipline and could not keep it: it armed the key
    off its own idea of session state, which stayed non-idle for the whole answer dwell, so Esc
    was taken from every other app on the machine for up to a minute and a half at a stretch —
    while the loop that was actually running never looked at it. Here the question "is the
    island showing?" is not an inference: this process IS the window.

    A press hides the island immediately (locally, no round trip) and tells the daemon
    afterwards, so dismissal feels instant even if the daemon is busy or gone.

    Narrow registration and no keyboard hook, as with the daemon's own hotkeys:
    RegisterHotKey delivers only this combo and nothing else is observed.

    `on_settings` : the OS broadcasts WM_SETTINGCHANGE when any system setting changes, so
    we re-query reduced-motion (the one setting we mirror) event-driven — only when it actually
    changes — rather than polling it every time the island shows. It fires regardless of whether
    Esc is armed, because a setting can change while the island is hidden. Folded onto this one
    filter rather than a second: it is the same native-message stream.
    """

    def __init__(self, on_press, on_settings=None) -> None:
        super().__init__()
        self._on_press = on_press
        self._on_settings = on_settings
        self._armed = False

    def arm(self, on: bool) -> None:
        if sys.platform != "win32" or on == self._armed:
            return
        import ctypes
        user32 = ctypes.windll.user32
        if on:
            if not user32.RegisterHotKey(None, _ESC_ID, _MOD_NOREPEAT, _VK_ESCAPE):
                log.warning("could not register Esc — another app owns it; no dismiss key")
                return
        else:
            user32.UnregisterHotKey(None, _ESC_ID)
        self._armed = on

    def nativeEventFilter(self, event_type, message):
        # Qt hands us every message it pumps, including thread messages like WM_HOTKEY (which
        # has no window). We must look even while Esc is disarmed, because WM_SETTINGCHANGE
        # This is not gated by the island being on screen.
        # `event_type` is bytes or a QByteArray depending on the binding's mood; both convert,
        # and matching on a substring rather than the exact tag covers Qt's two Windows
        # dispatchers ("windows_generic_MSG" and "windows_dispatcher_MSG") without listing them.
        try:
            kind = bytes(event_type)
        except (TypeError, ValueError):           # pragma: no cover
            kind = str(event_type).encode("utf-8", "replace")
        if b"windows" not in kind:
            return False, 0
        from ctypes import wintypes
        try:
            msg = wintypes.MSG.from_address(int(message))
        except (TypeError, ValueError):      # pragma: no cover — Qt changed the payload shape
            return False, 0
        if msg.message == _WM_SETTINGCHANGE:
            if self._on_settings is not None:
                self._on_settings()          # a machine setting changed — re-read what we mirror
            return False, 0                  # never consume: every other window needs it too
        if self._armed and msg.message == _WM_HOTKEY and msg.wParam == _ESC_ID:
            self._on_press()
            return True, 0                   # consumed: nobody else should see this Esc
        return False, 0


class IslandHitTest(QAbstractNativeEventFilter):
    """Per-region click-through. The island silhouette takes hover + clicks — but
    ONLY while there is a settled answer to peek (or a peek is already open); the surrounding frame
    is always click-through, so a click over empty frame still reaches the app beneath.

    This replaces the blanket WS_EX_TRANSPARENT the island used to carry: a fully transparent window
    is skipped by hit-testing and never receives WM_NCHITTEST at all, so per-region has to answer the
    message itself. Mechanism proven in sandbox/qml_spike. The maths is DPR-invariant — the cursor's
    screen point is compared against the island's rect expressed as fractions of the window's screen
    rect (GetWindowRect), so no logical-vs-physical-pixel conversion is needed.
    """

    def __init__(self, win, is_passthrough=None) -> None:
        super().__init__()
        self._win = win
        # Called on every hit-test: return True to force the WHOLE overlay click-through, whatever
        # the island is doing. Used so a topmost HUD never steals a click from another window of this app
        # (the Settings window) sitting beneath it.
        self._is_passthrough = is_passthrough
        self._hwnd = 0
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            u = ctypes.windll.user32
            # argtypes so the 64-bit HWND is a full pointer, never truncated (the classic trap).
            u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
            u.GetWindowRect.restype = wintypes.BOOL

    def set_hwnd(self, hwnd: int) -> None:
        # A re-shown window can come back with a fresh HWND (see restamp), so this is refreshed
        # every time the island appears rather than cached once.
        self._hwnd = hwnd

    def nativeEventFilter(self, event_type, message):
        try:
            kind = bytes(event_type)
        except (TypeError, ValueError):        # pragma: no cover
            return False, 0
        if b"windows" not in kind:
            return False, 0
        from ctypes import wintypes
        try:
            msg = wintypes.MSG.from_address(int(message))
        except (TypeError, ValueError):        # pragma: no cover
            return False, 0
        if msg.message != _WM_NCHITTEST or int(msg.hWnd or 0) != self._hwnd:
            return False, 0
        # While another window of this app (the Settings window) is open, the overlay must NEVER eat a
        # click: it is a topmost HUD covering the top-centre of the screen, so its silhouette sits
        # over a real window the user is trying to use — which is why Settings buttons under it, and
        # the hover needed to reach a row's Edit menu, did not respond. Fully click-through then; the
        # peek is a convenience that can wait until Settings is closed.
        if self._is_passthrough is not None and self._is_passthrough():
            return True, _HTTRANSPARENT
        # Interactive only when there is something to peek; otherwise fully click-through — the old
        # blanket-transparent behaviour, so the island never eats a click over a live answer's tab.
        if not (self._win.property("peekable") or self._win.property("peeking")):
            return True, _HTTRANSPARENT
        import ctypes
        lp = int(msg.lParam) & 0xFFFFFFFF                 # screen point: two signed 16-bit words
        x = lp & 0xFFFF
        y = (lp >> 16) & 0xFFFF
        x = x - 0x10000 if x >= 0x8000 else x
        y = y - 0x10000 if y >= 0x8000 else y
        r = wintypes.RECT()
        if not ctypes.windll.user32.GetWindowRect(self._hwnd, ctypes.byref(r)):
            return False, 0
        w, h = r.right - r.left, r.bottom - r.top
        winw, winh = float(self._win.width()), float(self._win.height())
        if winw <= 0 or winh <= 0 or w <= 0 or h <= 0:
            return False, 0
        # island rect as fractions of the window (DPR-invariant), from the live QML geometry
        ix = float(self._win.property("islandX"))
        iw = float(self._win.property("animW"))
        ih = float(self._win.property("animH"))
        left = r.left + (ix / winw) * w
        right = r.left + ((ix + iw) / winw) * w
        bottom = r.top + (ih / winh) * h
        inside = (left <= x <= right) and (r.top <= y <= bottom)
        return (False, 0) if inside else (True, _HTTRANSPARENT)


def stamp_overlay_styles(win) -> None:
    """NOACTIVATE + TOPMOST on the HWND directly — the native guarantees the Qt flags alone don't
    reliably give on Windows.

    NOACTIVATE — BINDING: the overlay must never take keyboard focus, because during
    dictation focus decides where the paste lands. (Recipe proven in sandbox/qml_spike.)

    Click-through is NO LONGER a blanket WS_EX_TRANSPARENT here. The island now takes hover
    and clicks over its own silhouette (to peek), so the whole window cannot be transparent — and a
    fully transparent window never receives WM_NCHITTEST anyway. IslandHitTest answers hit-testing
    per region instead. WS_EX_TRANSPARENT is explicitly CLEARED in case an earlier build left it on.
    """
    if sys.platform != "win32":
        return
    import ctypes
    GWL_EXSTYLE = -20
    WS_EX_TRANSPARENT, WS_EX_TOPMOST, WS_EX_NOACTIVATE = 0x00000020, 0x00000008, 0x08000000
    user32 = ctypes.windll.user32
    hwnd = int(win.winId())
    cur = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                          (cur | WS_EX_NOACTIVATE | WS_EX_TOPMOST) & ~WS_EX_TRANSPARENT)


def round_corners(win) -> None:
    """Ask DWM to round the settings window's corners.

    The window draws its own caption (Edge/Chrome style), so it is frameless — and a frameless
    window keeps square corners unless it asks. DWMWA_WINDOW_CORNER_PREFERENCE (attribute 33,
    Windows 11) with DWMWCP_ROUND is the whole ask; on Windows 10 the call is simply ignored
    and the corners stay square, which is what Windows 10 does everywhere anyway.

    Also sets the dark caption attribute. There is no caption to darken while frameless, but it
    costs one call and stops a light strip flashing on any build where Qt gives the window a
    frame after all.
    """
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes
    DWMWA_USE_IMMERSIVE_DARK_MODE, DWMWA_WINDOW_CORNER_PREFERENCE = 20, 33
    DWMWCP_ROUND = 2
    try:
        hwnd = wintypes.HWND(int(win.winId()))
        for attr, val in ((DWMWA_USE_IMMERSIVE_DARK_MODE, 1),
                          (DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND)):
            v = ctypes.c_int(val)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, ctypes.c_int(attr), ctypes.byref(v), ctypes.sizeof(v))
    except Exception as e:                            # pragma: no cover — cosmetic only
        log.debug("could not set the window's DWM attributes: %s", e)


def main() -> int:
    logging.basicConfig(level=logging.INFO, datefmt="%H:%M:%S",
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="Run the overlay")
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--latency", action="store_true",
                    help="show the per-turn latency readout (the first-live-run "
                         "instrument); also togglable from the tray")
    args = ap.parse_args()

    # QApplication (not QGuiApplication as in sandbox/qml_spike): the tray lives in QtWidgets, and
    # the BINDING non-activation is cheaper to prove against the final app class now.
    if not check_qml_available():
        return 2

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)   # the island hides at idle — that is not a quit
    app.setWindowIcon(al.app_icon())      # Al, resting, on the taskbar (and any window)
    if sys.platform == "win32":
        # Running as python.exe, Windows groups the taskbar button under python and shows ITS icon,
        # ignoring the app's window icon. An explicit AppUserModelID makes Windows treat us as our
        # own app, so the window icon (above) is what the taskbar actually shows.
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NotHal.Teleprompter")
        except Exception as e:                                       # noqa: BLE001 — never fatal
            log.debug("could not set AppUserModelID: %s", e)
    # Text render mode is left at Qt Quick's DEFAULT (distance-field). Two alternatives were
    # tried on 2026-07-25 and both looked worse on this display: NativeTextRendering went jagged
    # on the translucent island / fractional DPI, and CurveTextRendering was no better. The
    # default at least antialiases everywhere; the softness it trades for that is the lesser
    # evil. Revisit per-surface only if a single crisp surface is worth the split.

    load_bundled_fonts()                   # must follow QApplication, precede pick_font()

    model = OverlayModel(show_latency=args.latency)
    engine = QQmlApplicationEngine()
    # Al the mascot: the taskbar icon (above) renders her directly; the settings window
    # draws her through this provider — `image://al/<state>/<clip>/<frame>`.
    engine.addImageProvider("al", al.AlImageProvider())
    # The repo root, so `import frontend` resolves this package's qmldir and its Theme
    # singleton (the design tokens).
    engine.addImportPath(str(Path(__file__).resolve().parent.parent))
    engine.rootContext().setContextProperty("overlay", model)   # not "model": Repeater shadows it
    # The settings window's model. Built now (it is a bare QObject over a JSON file) but the
    # WINDOW is not: spawned only when opened, so an unopened settings screen
    # costs nothing but this object.
    cfg = SettingsModel()
    engine.rootContext().setContextProperty("cfg", cfg)
    engine.rootContext().setContextProperty("fontFamily", pick_font())
    engine.rootContext().setContextProperty("targets", targets())   # latency targets
    # Al's player. The kit ships its own timing script (idle fidgets, enters, exits, holds), so
    # the window binds one URL and nothing else — it never counts frames. It follows the turn
    # itself off the model (QmlAl._follow) rather than being driven from QML.
    al_player = al.QmlAl(model=model)
    engine.rootContext().setContextProperty("alPlayer", al_player)
    reduce_state = reduced_motion()
    # Free-running at the kit's 9 fps: the island shows Al too, so the clock cannot belong to the
    # settings window. The tick is a small state machine and a signal; painting only happens when
    # a visible Image pulls the provider. Reduced motion stops it dead.
    al_player.running = not reduce_state
    engine.rootContext().setContextProperty("reducedMotion", reduce_state)
    if reduce_state:
        log.info("system 'show animations' is off — island transitions run instant")

    # Pin the QObjects exposed to QML to C++ ownership — the same guarantee the settings WINDOW is
    # given in open_settings. A QObject reachable from QML can be adopted by the engine's JavaScript
    # garbage collector and DELETED despite a live Python reference; that is what nulled `alPlayer`
    # "after a while", throwing on `alPlayer.source`/`padRight`/`padBottom` in BOTH windows (the
    # island's Al and the settings sidebar's) and taking the overlay down with it. Ownership is the
    # authority here, not the Python local, so it is set explicitly.
    for _pinned in (model, cfg, al_player):
        QQmlEngine.setObjectOwnership(_pinned, QQmlEngine.ObjectOwnership.CppOwnership)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parent / "Overlay.qml")))
    roots = engine.rootObjects()
    if not roots:
        log.error("QML failed to load — see the Qt errors above")
        return 1
    win = roots[0]

    # Kept in locals for the app's lifetime — both are garbage collected otherwise.
    feed = Feed(model, args.host, args.port)                             # noqa: F841

    def on_dismiss() -> None:
        """Esc. Hide first, tell the daemon second — the island must never look like it is
        waiting for permission to go away."""
        model.dismissed.emit()
        feed.send(m_dismiss())

    def on_settings_change() -> None:
        # WM_SETTINGCHANGE: re-read reduced-motion and push it only if it actually
        # flipped, so a user who toggles 'show animations' — the person most likely doing so
        # because motion is bothering them right now — sees it apply without restarting.
        nonlocal reduce_state
        now = reduced_motion()
        if now != reduce_state:
            reduce_state = now
            engine.rootContext().setContextProperty("reducedMotion", now)
            log.info("reduced-motion changed -> %s (live)", now)

    def settings_is_open() -> bool:
        # The overlay steps fully aside (click-through) while the Settings window is up, so it can
        # never intercept a click meant for it. Asked of Qt each time rather than tracked, for the
        # same reason open_settings does: our own bookkeeping fails open, the window list cannot.
        for w in app.topLevelWindows():
            if w.objectName() == "nothalSettings" and w.isVisible():
                return True
        return False

    dismiss_key = DismissKey(on_dismiss, on_settings_change)
    app.installNativeEventFilter(dismiss_key)
    island_hit = IslandHitTest(win, is_passthrough=settings_is_open)     # noqa: F841
    app.installNativeEventFilter(island_hit)                             # per-region click-through

    def restamp() -> None:
        # The island hides at idle; a re-shown window can come back with a fresh HWND, so
        # re-apply the non-activating style every time it appears. The dismiss key follows the
        # same signal: Esc is borrowed from the rest of the system for exactly as long as
        # there is something on screen to dismiss, and not one frame longer.
        showing = win.isVisible()
        if showing:
            stamp_overlay_styles(win)
            island_hit.set_hwnd(int(win.winId()))   # a re-shown window can have a fresh HWND
        dismiss_key.arm(showing)

    win.visibleChanged.connect(restamp)
    restamp()

    # Peek actions: the overlay owns the current turn's text (it arrives on the feed), so
    # Copy and Save are handled here in the host — a QML file has no business touching the clipboard
    # or a file dialog. Save is user-initiated export of an answer already on screen (and already in
    # logs/nothal.log), so it is strictly less exposure than the log itself.
    def on_copy(text: str) -> None:
        app.clipboard().setText(text)

    def on_save(text: str) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(None, "Save answer", "nothal-answer.txt",
                                              "Text (*.txt);;Markdown (*.md);;All files (*)")
        if not path:
            return
        try:
            Path(path).write_text(text, encoding="utf-8")
            log.info("answer saved to %s", path)
        except OSError as e:
            log.warning("could not save the answer: %s", e)

    win.copyRequested.connect(on_copy)
    win.saveRequested.connect(on_save)

    # Per-region click-through is now IslandHitTest (WM_NCHITTEST -> HTTRANSPARENT), NOT setMask:
    # Qt's setMask is SetWindowRgn on Windows, which clips PAINTING too (measured 70% painted
    # before, 10% after). The filter affects hit-testing only. Proven in sandbox/qml_spike.

    # The settings window, built on first open and kept afterwards — reopening is common
    # enough that rebuilding it each time would be wasteful, and it holds no feed state to go
    # stale. Closing it drops it off screen (and, natively, destroys its HWND — see show_settings);
    # app.setQuitOnLastWindowClosed(False) above means that is not a quit, exactly as the island
    # hiding is not.
    settings_win: dict = {}

    def show_settings(w) -> None:
        # Closing the window DESTROYS its HWND (Qt's close() calls destroy()), so a reopen comes
        # back with a fresh one and the DWM corner preference set on the old handle is gone with
        # it. Re-stamp on every open — AFTER show(), because a stamp on the replacement handle
        # before it is on screen does not take (measured on the real window: reopen came back
        # square either way until the call moved below show()).
        w.show()
        round_corners(w)
        w.raise_()
        w.requestActivate()

    def open_settings() -> None:
        # Ask QT what exists, not our own dict. The dict is a fast path and can go stale — a
        # window collected behind our back, a build that raised part-way — and every way it goes
        # stale fails OPEN, which is how a second window appeared. The window list cannot.
        for w in app.topLevelWindows():
            if w.objectName() == "nothalSettings":
                settings_win["win"] = w
                show_settings(w)
                return

        win_ = settings_win.get("win")
        if win_ is not None:
            # Reopening. The QWindow outlives its close, so showing it again is all that is
            # needed — unless its C++ half has gone, in which case the Python wrapper is a husk
            # and every call on it raises. PROBE first, with a call that has no side effect: the
            # earlier shape wrapped show/raise/activate together, so a failure part-way through
            # left the window on screen AND fell through to build a second one. Whether the old
            # one lives is a separate question from showing it, and has to be answered first.
            try:
                win_.isVisible()
            except RuntimeError:
                log.info("settings window was collected — rebuilding it")
                settings_win.pop("win", None)
                win_ = None
            if win_ is not None:
                show_settings(win_)
                return

        # Claim the slot before building. Creating the window is synchronous, but the tray can
        # deliver two activations (a double-click on the icon is two), and a second call arriving
        # mid-build would find the slot still empty and start its own.
        if settings_win.get("building"):
            return
        settings_win["building"] = True
        comp = QQmlComponent(
            engine, QUrl.fromLocalFile(str(Path(__file__).resolve().parent / "SettingsWindow.qml")))
        win_ = comp.create(engine.rootContext())
        settings_win.pop("building", None)
        if win_ is None:
            for err in comp.errors():
                log.error("settings window: %s", err.toString())
            return
        # The engine hands QML-created objects to its JavaScript garbage collector, which will
        # happily take this one once it is hidden — a Python reference does not stop that.
        # Claiming C++ ownership is what makes the window survive being closed.
        QQmlEngine.setObjectOwnership(win_, QQmlEngine.ObjectOwnership.CppOwnership)
        try:
            win_.setIcon(al.app_icon())  # the taskbar button for this window, explicitly
        except Exception as e:                                       # noqa: BLE001
            log.debug("could not set settings-window icon: %s", e)
        settings_win["win"] = win_
        show_settings(win_)

    tray = None
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = Tray(app, model, on_settings=open_settings)                # noqa: F841
    else:
        log.warning("no system tray available — no way to quit but Ctrl-C")

    log.info("teleprompter up — subscribing to %s:%d", args.host, args.port)
    rc = app.exec()
    # Destroy the QML engine — and with it every window and binding — BEFORE main()'s other
    # locals. Left to Python, these are freed in arbitrary order: `model`, `cfg` and `alPlayer`
    # went first, so every binding that reads a context property re-evaluated against null on the
    # way down and printed a TypeError. That was ~80 lines of noise on every quit, which is worse
    # than untidy — a real shutdown error would have been invisible in it.
    # `shiboken6.delete`, NOT `deleteLater()`: deleteLater only posts a deferred-delete event, and
    # app.exec() has already returned, so nothing is left to process it — a silent no-op.
    # Verified in frontend.settings_check, which reproduces the same teardown: 53 -> 0.
    import shiboken6
    shiboken6.delete(engine)
    return rc


if __name__ == "__main__":
    sys.exit(main())
