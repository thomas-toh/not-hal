"""Al, the mascot sprite renderer.

Reads the sprite kit (`frontend/al/al-sprites.json`, kit **v3**: palette-indexed 26×26
frames grouped into named CLIPS — the kit's own source of truth; never hand-edit it, the next
export overwrites it) and paints a frame to a QImage / QIcon. Two consumers:

  - the Windows taskbar / app icon (`app_icon`, `idle/rest` on a chip),
  - the settings window, via `AlImageProvider` (`image://al/<state>/<clip>/<frame>`), driven
    by `QmlAl` so QML never reimplements the kit's timing.

*(The tray is NOT a Al surface — it draws a mic-level ring of its own, `tray.py`.)*

The kit ships its behaviour, not just its art: a state has a base loop, optional enter/exit clips,
one-shot ACTIONS and holds, plus a `script` saying how often to fire an action. `idle/rest` is a
single frame, so without that script Al is frozen — hence `AlPlayer` here (a port of the kit's
own `al_sprites.py::AlPlayer`, minus Pillow).

*(The kit is Design's 26px build — the same artwork as their 32px one, cropped to a tighter cell, so
Al is 54% of the cell's width instead of 44% and renders 1.23× at a given box. Nothing here reads
the cell size as a constant: every scale derives from `cell()`.)*

Recolouring is a MAP over the kit's indices (README: "ship the indices, not the colours"), never a
repaint. The kit ships a light AND a dark hex per role, so each ground takes the set drawn for it;
only the BODY and EYE are overridden — the kit's near-white body is a shade off the app's ink.

    python -m frontend.al            # offline selfcheck (renders on Qt's offscreen platform)
"""
from __future__ import annotations

import json
import random
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import Property, QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtQuick import QQuickImageProvider

_JSON = Path(__file__).resolve().parent / "al" / "al-sprites.json"

# Idle fidgets this app never rolls. The kit still ships the frames and the script still
# weights them; the pick just skips them, in EITHER tier — `look-around` is a filler, `jump` a gag.
# Muting here rather than in the kit because Design's next export overwrites `al-sprites.json`.
MUTED = frozenset({"look-around", "jump"})


@lru_cache(maxsize=1)
def _data() -> dict:
    return json.loads(_JSON.read_text(encoding="utf-8"))


@lru_cache(maxsize=2)
def _ground(ground: str) -> dict:
    """The kit's own palette for a ground ("light" | "dark") — read from the JSON, never copied."""
    return {i: role[ground] for i, role in _data()["palette"].items()}


def palettes() -> tuple[dict, dict]:
    # ISLAND: for DARK surfaces (the island, the settings shell) — the kit's dark set, with the
    # body pulled to the app's off-white and the eyes knocked to true black so they read as holes.
    # NATIVE: for LIGHT surfaces — the kit exactly as it ships.
    return {**_ground("dark"), "1": "#f4f6f8", "2": "#000000"}, dict(_ground("light"))


ISLAND, NATIVE = palettes()


def cell() -> int:
    return _data()["cell"]


@lru_cache(maxsize=1)
def _ink_pad() -> dict[str, int]:
    """The empty cell rows/columns around the ink in the RESTING frame — the one every static
    surface draws. A cell is ink if its character is one of the palette's indices."""
    rows = frames("idle", base("idle"))[0]
    ink = set(ISLAND)
    lit = [i for i, r in enumerate(rows) if any(c in ink for c in r)]
    cols = [i for r in rows for i, c in enumerate(r) if c in ink]
    return {"top": lit[0], "bottom": len(rows) - 1 - lit[-1],
            "left": min(cols), "right": len(rows[0]) - 1 - max(cols)}


def states() -> list[str]:
    return list(_data()["states"])


def _state(name: str) -> dict:
    return _data()["states"][name]


def clips(state: str) -> list[str]:
    return list(_state(state)["order"])


def base(state: str) -> str:
    return _state(state)["base"]


def policy(state: str, clip: str) -> str:
    return _state(state)["clips"][clip]["policy"]


def fps(state: str) -> int:
    return _state(state).get("fps") or _data()["fps"]


def frames(state: str, clip: str) -> list[list[str]]:
    return _state(state)["clips"][clip]["frames"]


def frame_count(state: str, clip: str) -> int:
    return len(frames(state, clip))


def frame_image(state: str, clip: str, index: int, palette: dict | None = None) -> QImage:
    """One cell-sized frame as a transparent QImage, painted from the palette-indexed grid. Needs no
    QApplication (QImage is not a platform paint device), so the pixel logic is CI-testable bare."""
    c = cell()
    rows = frames(state, clip)[index % frame_count(state, clip)]
    cols = {k: QColor(v) for k, v in (palette or ISLAND).items()}
    img = QImage(c, c, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            col = cols.get(ch)
            if col is not None:                       # '.' is transparent; unknown index skipped
                img.setPixelColor(x, y, col)
    return img


def frame_pixmap(state: str, clip: str, index: int, px: int,
                 palette: dict | None = None) -> QPixmap:
    """Scaled with FastTransformation (nearest-neighbour) — smooth scaling blurs the cells."""
    img = frame_image(state, clip, index, palette)
    if px != img.width():
        img = img.scaled(px, px, Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.FastTransformation)
    return QPixmap.fromImage(img)


def _ink_box(state: str, clip: str, index: int) -> tuple[int, int, int, int]:
    """The tight bounding box of one frame's drawn pixels, as (x, y, w, h)."""
    rows = frames(state, clip)[index]
    xs = [x for row in rows for x, ch in enumerate(row) if ch != "."]
    ys = [y for y, row in enumerate(rows) if any(ch != "." for ch in row)]
    return min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1


def app_icon(px: int = 256) -> QIcon:
    """The app / Windows-taskbar icon: the resting Al on a rounded near-black chip. The chip is
    the light-body Al's ground, so it reads on a light OR dark taskbar (a bare light body would
    disappear on a light one). v2 dropped v1's `portrait.plain`; `idle/rest` is its replacement —
    a genuine one-frame still, which is what a static icon wants.

    Cropped to the FRAME's own ink, not to the kit's `anchor` box: the cell carries room for props
    that a resting pose does not use, and `anchor` is not a tight box in the 26px build (it is the
    whole cell). Measuring the pose fills the chip whatever the cell does next."""
    bx, by, bw, bh = _ink_box("idle", "rest", 0)
    img = frame_image("idle", "rest", 0, ISLAND).copy(bx, by, bw, bh)
    # An INTEGER scale, nearest-neighbour, and the same one on both axes (kit rule) — so the pose
    # keeps its proportions and its cells stay square whatever the crop's aspect is.
    scale = max(1, round(px * 0.72) // max(bw, bh))
    g = QPixmap.fromImage(img.scaled(bw * scale, bh * scale, Qt.AspectRatioMode.IgnoreAspectRatio,
                                     Qt.TransformationMode.FastTransformation))
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, px, px), px * 0.22, px * 0.22)
    p.fillPath(path, QColor("#000000"))
    p.drawPixmap((px - g.width()) // 2, (px - g.height()) // 2, g)
    p.end()
    return QIcon(pm)


class AlPlayer:
    """The kit's script, run for you — a Qt-free port of `al_sprites.py::AlPlayer`.

    One `tick()` per frame. The player owns which clip is on screen: the base loop plays while
    nothing else is queued; after `script.restHold` passes it fires one FILLER (a blink, a look
    around) from `script.filler`; every `script.gagEvery` fillers it fires a GAG from
    `script.weights` instead (the guitar, the phone, the disguise); `set_state()` plays the old
    state's exit, then the new state's enter, then settles; `hold()` freezes on a hold clip's last
    frame until `release()` or the next state.

    The two tiers exist because one weight table cannot say "blink often, play guitar rarely" —
    sharing a pool makes every blink a missed gag. A state with no `filler` key draws everything
    from `weights`, which is the v2 behaviour, so both kit generations run here.
    """

    def __init__(self, state: str = "idle", rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()
        self.state = state
        self._pending: str | None = None
        self._frozen = False
        self._fillers = 0
        self._play(base(state))
        self._gag_target = self._roll_gag()

    def set_state(self, name: str) -> None:
        """Ask for a state. Exits and enters are handled here, not by the app."""
        if name == self.state and not self._frozen and self._pending is None:
            return
        exit_clip = _state(self.state).get("exit")
        if exit_clip and not self._frozen and name != self.state:
            self._pending = name
            self._play(exit_clip)
        else:
            self._arrive(name)

    def hold(self, clip: str) -> None:
        """Play a HOLD clip (misheard, sparkle, fail) and stay on its last frame. A clip the
        current state does not own is ignored rather than raised — a hold is a decoration, and
        it is not worth taking the UI down for one."""
        if clip in _state(self.state)["clips"]:
            self._play(clip)

    def release(self) -> None:
        self._play(base(self.state))

    @property
    def frozen(self) -> bool:
        """Parked on a hold clip's last frame, waiting for the app to release it."""
        return self._frozen

    def tick(self) -> float:
        """Advance one frame. Returns how long to wait before the next tick, in seconds."""
        n = frame_count(self.state, self.clip)
        self.index += 1
        if self.index < n:
            self._frozen = False
        elif policy(self.state, self.clip) == "hold":
            self.index = n - 1
            self._frozen = True
        elif policy(self.state, self.clip) == "oneshot":
            self._arrive(self._pending) if self._pending else self._play(base(self.state))
        else:                                        # base loop finished a pass
            self.index = 0
            self._passes += 1
            if self._passes >= self._rest_target:
                self._fire()
        return 1.0 / fps(self.state)

    def _play(self, clip: str) -> None:
        self.clip = clip
        self.index = 0
        self._frozen = False
        self._passes = 0
        self._rest_target = self._roll_rest()

    def _arrive(self, name: str) -> None:
        self.state = name
        self._pending = None
        self._fillers = 0
        self._play(_state(name).get("enter") or base(name))
        self._gag_target = self._roll_gag()

    def _script(self) -> dict:
        return _state(self.state).get("script") or {}

    def _pick(self, table: dict | None) -> str | None:
        """Weighted pick over a script table, skipping holds — `listening/misheard` is a statement
        the app makes, never a fidget — skipping names the state does not own, and skipping the
        fidgets this app mutes."""
        usable = {c: w for c, w in (table or {}).items()
                  if w > 0 and c in _state(self.state)["clips"]
                  and policy(self.state, c) != "hold" and c not in MUTED}
        if not usable:
            return None
        names = list(usable)
        return self.rng.choices(names, weights=[usable[n] for n in names])[0]

    def _roll(self, key: str, default: tuple[int, int]) -> int:
        lo, hi = (self._script().get(key) or list(default))[:2]
        return self.rng.randint(int(lo), int(max(lo, hi)))

    def _roll_rest(self) -> int:
        return self._roll("restHold", (4, 8))

    def _roll_gag(self) -> int:
        return self._roll("gagEvery", (1, 1))

    def _fire(self) -> None:
        """One filler — or a gag, once enough fillers have gone by."""
        script = self._script()
        filler = script.get("filler")
        if not filler:                                   # v2-shaped script: one pool
            pick = self._pick(script.get("weights"))
            self._play(pick) if pick else self._defer()
            return
        self._fillers += 1
        if self._fillers >= self._gag_target:
            self._fillers = 0
            self._gag_target = self._roll_gag()
            pick = self._pick(script.get("weights"))
            if pick:
                self._play(pick)
                return
        pick = self._pick(filler)
        self._play(pick) if pick else self._defer()

    def _defer(self) -> None:
        """Nothing to play — wait another rest span rather than re-rolling every pass."""
        self._rest_target += self._roll_rest()


class QmlAl(QObject):
    """`AlPlayer` on a QTimer, exposed to QML as one bindable `source` URL.

    QML binds an Image to `source` and nothing else; the script (idle fidgets, enters, exits,
    holds) stays here, in the kit's terms, rather than being re-implemented in JavaScript.

    Given a `model`, it also picks the STATE itself, following the turn. That decision lives here
    and not in QML because Al now shows on two surfaces — the island and the settings bar — off
    one player: two QML Bindings writing `alState` would fight, and neither window is reliably
    the one that is open (the settings window is spawned on demand).
    """

    changed = Signal()

    def __init__(self, state: str = "idle", palette: str = "island", model=None) -> None:
        super().__init__()
        self._p = AlPlayer(state)
        self._palette = palette
        self._model = model
        # What the app has ASKED for, which is not the same as what the player is showing: a
        # state with an exit clip (working -> typewriter-out) stays on the old state until that
        # clip has played out. Tracking the request separately is what stops a QML Binding —
        # which re-evaluates freely — from restarting the exit on every pass and never arriving.
        self._want = state
        self._running = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)              # the delay is per-state, so re-armed per tick
        self._timer.timeout.connect(self._tick)
        if model is not None:
            # Two signals: the feed's blanket `changed`, and `revealing`'s own — it is kept off
            # `changed` to break a binding loop (see OverlayModel), so it must be wired here too.
            model.changed.connect(self._follow)
            model.revealingChanged.connect(self._follow)
            self._follow()

    def _follow(self) -> None:
        """Which sprite the turn calls for, in ONE expression so it cannot be internally
        inconsistent (a ladder of separate flags used to drop through a rung mid-update and flash
        the wrong clip). Two of the rungs are less obvious than they look:

        `speaking` is the kit's CLIP, never the feed's state word — the orchestrator only
        publishes `speaking` when TTS actually plays, and TTS is off by default, so the
        reply arrives while the feed still says `thinking`. And it is driven by the ISLAND's
        typewriter (`revealing`), not the daemon's stream, so Al mimes what is on screen: the
        two finish seconds apart on a long answer, in either order. `not done` covers the case
        where the reveal catches up mid-stream.
        """
        m = self._model
        if m.state == "listening":
            # First, and above the fault: the listening indicator is binding, so a stale error
            # must never be able to mask an open mic. (In practice they cannot coexist —
            # `listening` is in `clearsTurn`, so opening a capture clears the fault — but the
            # ordering says which one wins if that ever changes.)
            want = "listening"
        elif m.error:
            want = "error"                            # `fail`, then it settles onto `held`
        elif m.reply:
            want = "speaking" if (m.revealing or not m.done) else "done"
        elif m.state in ("thinking", "transcribing", "transforming"):
            # The typewriter, for anything where the machine is chewing on it: the model
            # composing, and dictation's transcribe + tidy passes. These three read identically
            # to the user — the island shows a status word and nothing else is asked of them.
            want = "working"
        elif m.state == "pasted":
            want = "done"                             # dictation landed: the same sparkle
        else:
            want = "idle"
        self.alState = want

    @Property(str, notify=changed)
    def source(self) -> str:
        return f"image://al/{self._p.state}/{self._p.clip}/{self._p.index}"

    # How many empty CELL rows/columns the resting frame carries around Al's ink. A caller that
    # puts her against an edge needs these: the cell is 26×26 and her ink occupies rather less, so
    # an image placed flush floats away from the edge by the difference. Constant — read off the
    # kit rather than measured by eye, so Design re-exporting cannot silently move her.
    # In display pixels: pad * (drawn size / cell).
    @Property(int, constant=True)
    def padBottom(self) -> int:
        return _ink_pad()["bottom"]

    @Property(int, constant=True)
    def padRight(self) -> int:
        return _ink_pad()["right"]

    def _get_state(self) -> str:
        return self._want

    def _set_state(self, name: str) -> None:
        if name in _data()["states"] and name != self._want:
            self._want = name
            self._p.set_state(name)
            self.changed.emit()

    alState = Property(str, _get_state, _set_state, notify=changed)

    def _get_running(self) -> bool:
        return self._running

    def _set_running(self, on: bool) -> None:
        if on == self._running:
            return
        self._running = on
        self._timer.start(round(1000 / fps(self._p.state))) if on else self._timer.stop()

    running = Property(bool, _get_running, _set_running, notify=changed)

    def _tick(self) -> None:
        delay = self._p.tick()
        # `done` and `error` have a HOLD as their enter (`sparkle`, `fail`), so they freeze on the
        # last frame by design — the kit is explicit that a hold is the app's to release and must
        # never sit on screen indefinitely. Releasing it here, once it has played through, is what
        # settles `done` onto `settled` and `error` onto `held`. A hold that is NOT an enter
        # (`misheard`) still waits for a deliberate `release()`, which is the point of it.
        if self._p.frozen and self._p.clip == (_state(self._p.state).get("enter") or ""):
            self._p.release()
        self.changed.emit()
        if self._running:
            self._timer.start(max(1, round(delay * 1000)))


def _parse_id(image_id: str) -> tuple[str, str, int]:
    """`"<state>/<clip>/<frame>"` -> (state, clip, frame). An unknown state or clip falls back to
    the resting Al, so a QML typo shows the character rather than a broken-image glyph."""
    parts = image_id.split("/")
    state = parts[0] if parts and parts[0] in _data()["states"] else "idle"
    clip = parts[1] if len(parts) > 1 and parts[1] in _state(state)["clips"] else base(state)
    try:
        return state, clip, int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        return state, clip, 0


class AlImageProvider(QQuickImageProvider):
    """Serves `image://al/<state>/<clip>/<frame>` to QML in the ISLAND palette (every app surface
    Al shows on is dark). Registered once on the QML engine; `QmlAl` steps the URL.
    `sourceSize` on the Image drives the (nearest-neighbour) scale."""

    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)

    def requestImage(self, image_id, size, requestedSize):
        state, clip, frame = _parse_id(image_id)
        img = frame_image(state, clip, frame, ISLAND)
        w = requestedSize.width() if requestedSize and requestedSize.width() > 0 else img.width()
        h = requestedSize.height() if requestedSize and requestedSize.height() > 0 else img.height()
        if (w, h) != (img.width(), img.height()):
            img = img.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                             Qt.TransformationMode.FastTransformation)
        if size is not None:
            size.setWidth(img.width())
            size.setHeight(img.height())
        return img


def _selfcheck() -> None:
    # QImage/QColor need no app, but QPixmap/QIcon/the provider do — so render on the offscreen
    # platform (same as the QML checks). The logic worth guarding: the kit loads, the palettes
    # come from the JSON, the script drives the player, and the provider tolerates a bad id.
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QGuiApplication
    QGuiApplication.instance() or QGuiApplication([])

    ss = states()
    for need in ("idle", "listening", "working", "done", "error"):
        assert need in ss, f"{need} missing from the kit"
    assert _data()["version"] == 3 and cell() == 26, "the v3 kit is Design's 26px build"
    assert fps("idle") >= 1 and base("idle") == "rest"

    # The palette is a MAP over the kit's indices, taken from the JSON per ground — not hardcoded.
    assert ISLAND["3"] == _ground("dark")["3"] and NATIVE["3"] == _ground("light")["3"], \
        "the accents must come from the kit's own light/dark sets"
    assert ISLAND["1"] != NATIVE["1"], "the body must differ between dark and light grounds"
    assert "6" in ISLAND, "the shade role must be in the palette"

    c = cell()
    img = frame_image("idle", "rest", 0, ISLAND)
    assert img.width() == c and img.height() == c
    opaque = [(x, y) for y in range(c) for x in range(c) if img.pixelColor(x, y).alpha() > 0]
    assert opaque, "the resting frame rendered entirely transparent"
    # A body pixel must be the ISLAND body, not the kit's — proof the map is applied.
    assert any(img.pixelColor(x, y) == QColor(ISLAND["1"]) for x, y in opaque), \
        "no body pixel took the island palette"

    # The 26px cell is its own tight box, so nothing may reintroduce a fixed 32/20 margin:
    # the resting pose has to fill most of the chip whatever the kit's cell becomes next.
    bx, by, bw, bh = _ink_box("idle", "rest", 0)
    assert bw >= c * 0.5 and bh >= c * 0.4, f"the resting pose is small in the cell: {bw}x{bh}/{c}"
    icon = app_icon(64)
    assert not icon.isNull(), "the app icon failed to build"
    shot = icon.pixmap(64, 64).toImage()
    lit = [x for y in range(64) for x in range(64) if shot.pixelColor(x, y) == QColor(ISLAND["1"])]
    assert lit and max(lit) - min(lit) > 64 * 0.5, \
        f"Al does not fill the app-icon chip (spans {max(lit) - min(lit)}px of 64)"

    prov = AlImageProvider()
    out = prov.requestImage("listening/listen/2", QSize(), QSize(64, 64))
    assert not out.isNull() and out.width() == 64, (out.isNull(), out.size())
    assert not prov.requestImage("nope/nope/0", QSize(), QSize()).isNull(), \
        "an unknown state must fall back to idle, never a null image"
    assert _parse_id("idle") == ("idle", "rest", 0), "a bare state must resolve to its base clip"

    # The script: rest is ONE frame, so an unscripted player would freeze. Ticking must fire
    # clips, and only clips this state owns.
    script = _state("idle")["script"]
    p = AlPlayer("idle", random.Random(7))
    played, seen = [], {p.clip}
    for _ in range(40_000):                       # ~74 minutes of idle at 9 fps
        prev = p.clip
        p.tick()
        if p.clip != prev and p.clip != "rest":
            played.append(p.clip)
        seen.add(p.clip)
    assert played, f"the idle script never fired anything (stuck on {seen})"
    assert seen <= set(clips("idle")), f"the script played a clip outside idle: {seen}"

    # ...and the TWO TIERS must stay separate. This is the whole point of the v3 script: a v2
    # loader runs a v3 kit happily but draws gags where fillers belong, so Al performs
    # constantly instead of blinking. Gags must be the rare tier.
    fillers, gags = set(script["filler"]) - MUTED, set(script["weights"]) - MUTED
    assert not fillers & gags, "a clip cannot be both a filler and a gag"
    # The muted fidgets must never fire, in either tier. The kit still weights them, so this is
    # the assertion that catches Design's next export quietly putting them back.
    assert MUTED <= set(clips("idle")), f"MUTED names a clip idle does not own: {MUTED}"
    assert not MUTED & set(played), f"a muted fidget fired: {MUTED & set(played)}"
    nf = sum(1 for c in played if c in fillers)
    ng = sum(1 for c in played if c in gags)
    assert nf and ng, f"both tiers must fire: {nf} fillers, {ng} gags"
    lo, hi = script["gagEvery"]
    ratio = nf / ng
    assert lo - 1 <= ratio <= hi + 1, \
        f"gags are not the rare tier: 1 gag per {ratio:.1f} fillers, want {lo}-{hi}"
    assert all(c in fillers | gags for c in played), f"unexpected clip: {set(played) - fillers - gags}"

    # An enter clip leads into the new base; a hold freezes on its last frame until released.
    p = AlPlayer("idle", random.Random(1))
    p.set_state("working")
    assert p.clip == "typewriter-in", f"the enter clip must play first, got {p.clip}"
    for _ in range(frame_count("working", "typewriter-in")):
        p.tick()
    assert p.clip == "typewriter", f"the enter must resolve into the base loop, got {p.clip}"
    p.set_state("listening")                                 # plays working's exit, then arrives
    p.hold("misheard")                                       # ignored: still exiting `working`
    assert p.clip == "typewriter-out", f"a foreign hold must not hijack the exit, got {p.clip}"
    for _ in range(frame_count("working", "typewriter-out")):
        p.tick()
    assert (p.state, p.clip) == ("listening", "listen"), (p.state, p.clip)
    p.hold("misheard")
    for _ in range(10):
        p.tick()
    assert (p.clip, p.index) == ("misheard", frame_count("listening", "misheard") - 1), \
        f"a hold must freeze on its last frame, got {p.clip}/{p.index}"
    p.release()
    assert p.clip == "listen", "release must return to the base loop"

    q = QmlAl()
    assert q.source.startswith("image://al/idle/"), q.source
    q.alState = "listening"
    assert q.alState == "listening" and "/listening/" in q.source

    # A QML Binding re-evaluates whenever anything it reads changes, so it will assign the same
    # state repeatedly. While an EXIT clip is playing the player is still on the old state, so
    # reporting that back would make each re-assignment look like a change and restart the exit
    # — Al would loop `typewriter-out` and never arrive. `alState` reports the REQUEST.
    q.alState = "working"
    for _ in range(frame_count("working", "typewriter-in") + 1):
        q._tick()
    assert q._p.clip == "typewriter", q._p.clip
    q.alState = "speaking"                       # working has an exit: typewriter-out plays first
    assert q.alState == "speaking", "the request must be visible immediately, not after the exit"
    assert q._p.clip == "typewriter-out", q._p.clip
    q._tick(); q._tick()
    mid = q._p.index
    assert mid > 0, "the exit did not advance"
    for _ in range(3):
        q.alState = "speaking"                   # the binding, firing again mid-exit
    assert q._p.clip == "typewriter-out" and q._p.index == mid, \
        f"re-requesting the same state restarted the exit ({q._p.clip}/{q._p.index}, was {mid})"
    for _ in range(frame_count("working", "typewriter-out") + 1):
        q._tick()
    assert (q._p.state, q._p.clip) == ("speaking", "speak"), (q._p.state, q._p.clip)

    # `done` and `error` enter on a HOLD (sparkle, fail), which freezes by design. QmlAl must
    # release an enter-hold once it has played, or Al stares at the sparkle forever — the kit
    # forbids leaving one up. A hold that is NOT an enter must still wait to be released by hand.
    for st, spark, settle in (("done", "sparkle", "settled"), ("error", "fail", "held")):
        q.alState = st
        assert q._p.clip == spark, f"{st} must enter on {spark}, got {q._p.clip}"
        for _ in range(frame_count(st, spark) + 2):
            q._tick()
        assert (q._p.clip, q._p.frozen) == (settle, False), \
            f"{st}/{spark} must settle onto {settle}, got {q._p.clip} frozen={q._p.frozen}"
    q.alState = "listening"
    q._p.hold("misheard")
    for _ in range(6):
        q._tick()
    assert q._p.clip == "misheard" and q._p.frozen, \
        f"a non-enter hold must stay put until released, got {q._p.clip}"

    print(f"al selfcheck OK: kit v{_data()['version']}, {len(ss)} states / "
          f"{sum(len(clips(s)) for s in ss)} clips at {cell()}px, palettes from the JSON, "
          f"script-driven player, image://al provider (bad id -> idle)")


if __name__ == "__main__":
    _selfcheck()
