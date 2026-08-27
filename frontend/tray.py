"""System tray (Track P C3) — the only "not-hal is running" signal there is.

The island hides completely at idle (spec/40: "gone = asleep"), so without this you cannot
tell the overlay from a dead process. Since D29 it is also the door to the settings window:
the output toggles and the Groq key that used to live in this menu as a stopgap now have a
real home, so the menu is back to three items.

The icon is a mic-level ring — hollow while the mic is closed, a coral core with a halo that
tracks your voice while it is open. Al the mascot briefly lived here (D32); it moved off when
the kit went to v2, and the tray gets its own character later.
"""
from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

log = logging.getLogger("nothal.teleprompter")

# Windows 11 Fluent flyout colours — the SHELL's, not the app's. A tray menu is part of the
# desktop, so it follows the Windows light/dark setting and ignores not-hal's own palette
# entirely. Nothing here is duplicated from Theme.qml, because none of it should match.
MENU_COLOURS = {
    #                 background  text       border     hover                   separator
    "light": ("#f9f9f9", "#1b1b1b", "#e5e5e5", "rgba(0, 0, 0, 0.05)", "#e5e5e5", "#9d9d9d"),
    "dark":  ("#2c2c2c", "#ffffff", "#3d3d3d", "rgba(255, 255, 255, 0.07)", "#3d3d3d", "#8a8a8a"),
}


def windows_uses_light_theme() -> bool:
    """The user's Windows app-theme setting. Defaults to light if it cannot be read, which is
    the Windows default."""
    if sys.platform != "win32":
        return True
    try:
        import winreg
        key = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            return bool(winreg.QueryValueEx(k, "AppsUseLightTheme")[0])
    except OSError:
        return True


def windows_taskbar_is_light() -> bool:
    """The TASKBAR/system theme (`SystemUsesLightTheme`) — distinct from `AppsUseLightTheme` above.
    A tray icon sits on the taskbar, and the two settings differ on a common Windows 11 combo
    (light apps + dark taskbar), which was rendering Al's dark body invisible on the dark taskbar.
    Defaults to dark (False) so the fallback is Al's LIGHT body — readable on the usual dark
    taskbar."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        key = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            return bool(winreg.QueryValueEx(k, "SystemUsesLightTheme")[0])
    except OSError:
        return False


def menu_qss(light: bool) -> str:
    bg, fg, border, hover, sep, dim = MENU_COLOURS["light" if light else "dark"]
    return f"""
QMenu {{
    background: {bg};
    color: {fg};
    border: 1px solid {border};
    border-radius: 8px;
    padding: 5px 4px;
    font-size: 14px;
}}
QMenu::item {{
    padding: 8px 32px 8px 36px;
    margin: 1px 4px;
    border-radius: 5px;
}}
QMenu::item:selected {{ background: {hover}; color: {fg}; }}
QMenu::item:disabled {{ color: {dim}; }}
QMenu::separator     {{ height: 1px; background: {sep}; margin: 5px 10px; }}
QMenu::icon          {{ left: 12px; }}
"""


# Stroked 24×24 paths, the same set the settings window draws from.
GEAR = ("M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M18.4 5.6L17 7"
        "M7 17l-1.4 1.4M12 8.6a3.4 3.4 0 1 0 0 6.8a3.4 3.4 0 1 0 0-6.8")
CHECK = "M4.5 12.6 9.5 17.5 19.5 6.5"
POWER = "M12 4v8M7.8 6.8a7 7 0 1 0 8.4 0"


def glyph_icon(path_d: str, colour: str, px: int = 16) -> QIcon:
    """A menu icon painted from a path, in whatever colour the shell's theme calls for.

    Generated rather than loaded from frontend/icons/: those are fixed-colour assets for
    the peek, and a tray icon has to flip with the Windows light/dark setting.
    """
    from PySide6.QtCore import QByteArray
    from PySide6.QtSvg import QSvgRenderer
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
           f'<path d="{path_d}" fill="none" stroke="{colour}" stroke-width="2" '
           f'stroke-linecap="round" stroke-linejoin="round"/></svg>')
    pm = QPixmap(px * 2, px * 2)                  # 2× then downscaled: crisp on hiDPI
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(p)
    p.end()
    pm.setDevicePixelRatio(2.0)
    return QIcon(pm)


def style_menu_native(menu: QMenu, light: bool) -> None:
    """Dress a Qt menu as a Windows 11 flyout: rounded, shadowed, and in the SHELL's theme.

    Rounding and the dark/light popup frame come from DWM, which applies both to any top-level
    window that asks — including a Qt popup — so these two attributes do what a stylesheet
    cannot (a QSS `border-radius` leaves square corners outside the painted area).

    Re-read on every open rather than cached, so switching Windows between light and dark takes
    effect on the next right-click with no restart and no settings-change hook.

    ponytail: a Qt menu dressed as a native one, not a native one. A genuine Windows 11 context
    menu is a WinUI 3 control, which PySide6 cannot host without an XAML island — a far bigger
    dependency than a three-item tray menu is worth. Revisit only if the app ships WinUI anyway.
    """
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes
    DWMWA_USE_IMMERSIVE_DARK_MODE, DWMWA_WINDOW_CORNER_PREFERENCE = 20, 33
    DWMWCP_ROUND = 2
    try:
        hwnd = wintypes.HWND(int(menu.winId()))
        for attr, val in ((DWMWA_USE_IMMERSIVE_DARK_MODE, 0 if light else 1),
                          (DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND)):
            v = ctypes.c_int(val)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, ctypes.c_int(attr), ctypes.byref(v), ctypes.sizeof(v))
    except Exception as e:                        # pragma: no cover — cosmetic only
        log.debug("could not style the tray menu natively: %s", e)


# --- the mic ring: the tray's whole picture -----------------------------------------------
# Theme.flare — the app's on-air coral, so the tray and the settings window agree on what
# "capturing" looks like. The idle ink follows the TASKBAR theme, not the app's, since that is
# the surface it sits on.
ON_AIR = "#cf6142"
GAIN = 4          # real mic RMS sits low; the island lifts it by the same factor (Overlay.qml)


def mic_icon(level: float, capturing: bool, light_taskbar: bool, px: int = 64) -> QIcon:
    """A level ring, the way a call app draws one: a hollow ink circle while the mic is CLOSED,
    a filled coral core the moment it opens, and a halo that pushes outward with your voice.

    Truthful by construction (spec/50 rule 4): `capturing` is the daemon's real capture state and
    `level` its real RMS — nothing here is inferred or faked while the mic is shut."""
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = px / 2
    if capturing:
        lvl = max(0.0, min(1.0, level * GAIN))
        halo = QColor(ON_AIR)
        halo.setAlphaF(0.18 + 0.32 * lvl)             # louder reads as brighter, not just bigger
        r = px * (0.24 + 0.22 * lvl)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(QRectF(c - r, c - r, 2 * r, 2 * r))
        p.setBrush(QColor(ON_AIR))                    # the core says "open", at any volume
        p.drawEllipse(QRectF(c - px * 0.2, c - px * 0.2, px * 0.4, px * 0.4))
    else:
        ink = QColor("#1b1b1b" if light_taskbar else "#ffffff")
        ink.setAlphaF(0.75)
        pen = QPen(ink)
        pen.setWidthF(px * 0.09)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        r = px * 0.28
        p.drawEllipse(QRectF(c - r, c - r, 2 * r, 2 * r))
    p.end()
    return QIcon(pm)


class Tray(QSystemTrayIcon):
    def __init__(self, app, model=None, on_settings=None) -> None:
        super().__init__()
        self._app = app
        self._model = model
        self.setToolTip("Teleprompter")

        # The mic ring, driven by the live status feed (spec/50 rule 4 — the tray shows only what
        # the daemon is really doing, never inferred). No timer: mic frames ARE the clock.
        self._light = windows_taskbar_is_light()
        self._shown = None                        # last (capturing, level bucket) painted
        if model is not None:
            model.changed.connect(self._on_model)
        self._on_model()

        menu = QMenu()
        self._act_settings = None
        self._act_lat = None

        if on_settings is not None:
            self._act_settings = QAction("Settings", menu)
            self._act_settings.triggered.connect(on_settings)
            menu.addAction(self._act_settings)
            # Double-clicking a tray icon opening its window is the Windows convention, and it
            # is the gesture people try first.
            self.activated.connect(
                lambda reason: on_settings()
                if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        if model is not None:
            # A dev tool (the acceptance-run instrument), deliberately not a setting.
            # NOT a checkable action: Qt draws that as a box in its own indicator column, which
            # is not how Windows shows a toggled menu item — it puts a tick in the icon column
            # beside everything else's icon. So the state is ours to hold and to draw.
            self._act_lat = QAction("Show latency", menu)
            self._act_lat.triggered.connect(self._toggle_latency)
            menu.addAction(self._act_lat)
        menu.addSeparator()
        act_quit = QAction("Quit", menu)
        act_quit.triggered.connect(app.quit)
        menu.addAction(act_quit)
        self._act_quit = act_quit
        # Restyled on every open, not once at construction: the DWM attributes need a real
        # window (a menu has none until it is about to show, and a re-shown popup can get a
        # fresh HWND), and re-reading the registry here is what makes both the colours and the
        # icons follow Windows switching between light and dark.
        menu.aboutToShow.connect(self._restyle)
        # Held deliberately: setContextMenu does not take ownership, and a menu that is
        # garbage collected takes the tray's right-click with it. Assigned before the first
        # _restyle(), which reads it.
        self._menu = menu
        self._restyle()                          # so the first right-click is already styled
        self.setContextMenu(menu)
        self.show()

    def _toggle_latency(self) -> None:
        self._model.toggle_latency(not self._model.showLatency)
        self._restyle()                          # the tick follows immediately

    def _restyle(self) -> None:
        """Colours and icons, both taken from the Windows theme rather than not-hal's."""
        light = windows_uses_light_theme()
        ink = MENU_COLOURS["light" if light else "dark"][1]
        self._menu.setStyleSheet(menu_qss(light))
        if self._act_settings is not None:
            self._act_settings.setIcon(glyph_icon(GEAR, ink))
        if self._act_lat is not None:
            # An empty icon still reserves the column, so the labels stay aligned whether or
            # not the tick is showing.
            self._act_lat.setIcon(glyph_icon(CHECK, ink) if self._model.showLatency else QIcon())
        self._act_quit.setIcon(glyph_icon(POWER, ink))
        style_menu_native(self._menu, light)

    # --- the mic ring (spec/50 rule 4: the tray reflects real status, never inferred) ---------

    def _on_model(self) -> None:
        """`changed` fires on every feed message. Repaint only when the drawn picture would
        actually differ — the level is quantised to 12 steps, so a steady voice is a handful of
        `setIcon` calls a second rather than one per mic frame."""
        capturing = self._model is not None and self._model.state == "listening"
        level = self._model.mic if capturing else 0.0
        shown = (capturing, round(min(1.0, level * GAIN) * 12))
        if shown == self._shown:
            return
        if self._shown is None or shown[0] != self._shown[0]:
            # Re-read the taskbar theme on the capture edge, not per frame: winreg is not free,
            # and the idle ring is the only thing the theme affects.
            self._light = windows_taskbar_is_light()
        self._shown = shown
        self.setIcon(mic_icon(level, capturing, self._light))


def _selfcheck() -> None:
    """`python -m frontend.tray` — the ring's pixels and the repaint gate, offscreen."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication
    QGuiApplication.instance() or QGuiApplication([])

    def coral(icon):
        """Opaque-ish pixels that are warm — white ink and coral both have a high red, so the
        test is red-vs-green, not red alone."""
        img = icon.pixmap(64, 64).toImage()
        return sum(1 for y in range(64) for x in range(64)
                   if img.pixelColor(x, y).alpha() > 40
                   and img.pixelColor(x, y).red() > img.pixelColor(x, y).green() + 50)

    closed, quiet, loud = (mic_icon(0.0, False, False), mic_icon(0.0, True, False),
                           mic_icon(1.0, True, False))
    assert all(not i.isNull() for i in (closed, quiet, loud))
    # Closed reads as ink, open reads as coral: the honest difference (spec/50 rule 4).
    assert coral(closed) == 0, "the closed ring must not be coral"
    assert coral(quiet) > 0, "an open mic must show the coral core"
    # ...and louder is a bigger picture, which is the whole point of the ring.
    assert coral(loud) > coral(quiet) * 1.5, (coral(quiet), coral(loud))

    # The repaint gate: identical levels must not repaint, a real change must.
    class FakeModel:
        state, mic = "listening", 0.05         # below the gain's ceiling, so a change still moves

    class Probe(Tray):
        def __init__(self):                       # no QSystemTrayIcon, no menu — just the gate
            self._model, self._light, self._shown, self.painted = FakeModel(), False, None, 0

        def setIcon(self, _icon):
            self.painted += 1

    t = Probe()
    t._on_model()
    t._on_model()
    assert t.painted == 1, f"an unchanged level must not repaint ({t.painted})"
    FakeModel.mic = 0.15                          # several buckets up
    t._on_model()
    assert t.painted == 2, "a real level change must repaint"
    FakeModel.state = "idle"
    t._on_model()
    assert t.painted == 3 and t._shown == (False, 0), t._shown

    print("tray selfcheck OK: mic ring (closed=ink, open=coral, louder=bigger), "
          "repaint gate quantised to 12 steps")


if __name__ == "__main__":
    _selfcheck()
