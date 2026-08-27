"""The settings window's offline check, the sibling of overlay_check.

    python -m frontend.settings_check

Runs on Qt's `offscreen` platform, so it needs no display and no credential store. What it
guards is the thing QML fails at quietly: a binding that throws leaves the window looking
almost right and only prints a warning, so this drives the window through every state it has
— empty, one provider, two, each pane, the Manage sheet — and fails on ANY warning.

It also guards the schema→UI contract: the window is generated from
`shared/schemas/settings.json`, so a knob added there with a missing label or an unknown pane
would render as a blank row rather than an error. Checked here instead.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Must precede the Qt import, exactly as overlay_check does.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import PySide6  # noqa: E402

# Store-Python quirk (NOTES.md): Qt's QML plugin loader does not search the PySide6 package
# dir where the Qt6*.dll live. Same fix __main__.py applies, needed again here.
_d = os.path.dirname(PySide6.__file__)
os.environ["PATH"] = _d + os.pathsep + os.environ.get("PATH", "")
try:
    os.add_dll_directory(_d)
except (AttributeError, OSError):
    pass

from PySide6.QtCore import QObject, QUrl, qInstallMessageHandler        # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine                          # noqa: E402
from PySide6.QtWidgets import QApplication                               # noqa: E402

from shared import settings                                              # noqa: E402
from frontend.model import OverlayModel                              # noqa: E402
from frontend.settings_model import SettingsModel                    # noqa: E402
from frontend import al                                             # noqa: E402

HERE = Path(__file__).resolve().parent


def check_icon_font() -> None:
    """Every icon in the app is one Lucide glyph. Three ways that fails silently, so three
    assertions: the bundled file goes missing or is renamed, a codepoint in `Theme.ico` has no
    glyph behind it (tofu, with no QML warning), or an icon creeps back in as an SVG.
    """
    import re
    from PySide6.QtGui import QFontDatabase, QFont, QRawFont
    ttf = HERE / "fonts" / "lucide.ttf"
    assert ttf.exists(), f"icon font missing: {ttf}"
    fid = QFontDatabase.addApplicationFont(str(ttf))
    fams = QFontDatabase.applicationFontFamilies(fid) if fid != -1 else []
    # The literal must match Theme.qml's `fontIcon`; both name the same bundled family.
    assert "lucide" in fams, f"icon font family changed or failed to load: {fams}"

    # Every codepoint in Theme.ico must actually resolve to a glyph. A mistyped or stale one
    # renders as an empty box and nothing anywhere complains — the failure that made re-mapping
    # the whole set off Material Symbols worth verifying rather than eyeballing.
    theme = (HERE / "Theme.qml").read_text(encoding="utf-8")
    icons = re.findall(r'readonly property string (\w+):\s*"\\u([0-9A-Fa-f]{4})"', theme)
    assert len(icons) > 20, f"Theme.ico looks empty or reformatted — found {len(icons)} codepoints"
    raw = QRawFont.fromFont(QFont("lucide", 24))
    tofu = [name for name, cp in icons if not raw.supportsCharacter(int(cp, 16))]
    assert not tofu, f"{len(tofu)} icon(s) have no glyph in the font: {tofu}"

    # Icons are font glyphs, never SVG. A `d: "M…"` fed to a Glyph, or an Image pointed at an
    # icons/*.svg, both render with no QML warning. `not-hal-mark.svg` is the project's own mark
    # rather than an icon, and the Mark logo uses `PathSvg { path: … }`, so neither trips this.
    for qml in ("SettingsWindow.qml", "PeekPanel.qml", "Overlay.qml"):
        src = (HERE / qml).read_text(encoding="utf-8")
        assert not re.findall(r'\bd:\s*"[Mm][\d\s.\-]', src), f"{qml}: a Glyph is fed an SVG path"
        stray = re.findall(r'source:.*"[^"]*icons/(?!not-hal-mark)[^"]*\.svg"', src)
        assert not stray, f"{qml}: icon(s) drawn from SVG instead of the font: {stray}"
    print(f"  icon font: {ttf.name} -> {fams[0]}; {len(icons)} glyphs present, none SVG")


def check_al_turn(player, overlay, settle) -> None:
    """Al in the top bar mimes the turn. Walked end to end because the mapping is the sort of
    thing that looks right in a diff and is wrong on screen — and because two of its rules exist
    only to survive orderings a still frame never shows:

      * with TTS off (the default) the daemon NEVER publishes `speaking`, so Al's
        `speaking` has to be driven by the reply, not by the feed's state word;
      * the island's typewriter and the daemon's stream finish at different times, in either
        order, and Al must not flicker out of `speaking` in the gap.
    """
    def phase() -> str:
        settle()
        return player.alState

    overlay.apply({"type": "state", "state": "idle"})
    overlay.revealing = False
    assert phase() == "idle", phase()
    overlay.apply({"type": "state", "state": "listening"})
    assert phase() == "listening", "the mic is open: listening is never inferred"
    overlay.apply({"type": "state", "state": "thinking"})
    assert phase() == "working", "composing an answer draws the typewriter"
    overlay.apply({"type": "transcript", "text": "when is my meeting", "final": True})
    assert phase() == "working", "the prompt showing is not yet an answer"

    overlay.apply({"type": "response", "delta": "Half past "})
    overlay.revealing = True
    assert phase() == "speaking", "the island is laying the answer down"
    overlay.apply({"type": "response", "delta": "two.", "done": True})
    assert phase() == "speaking", \
        "the daemon finished but the typewriter has not — Al follows the SCREEN"
    overlay.revealing = False
    assert phase() == "done", "the answer has landed"

    # ...and the other order: a short answer the reveal catches up with mid-stream.
    overlay.feed_lost()
    overlay.apply({"type": "state", "state": "thinking"})
    overlay.apply({"type": "response", "delta": "Half"})
    overlay.revealing = True
    assert phase() == "speaking", phase()
    overlay.revealing = False
    assert phase() == "speaking", \
        "the reveal caught up before the daemon did — Al must not drop out of speaking"
    overlay.apply({"type": "response", "delta": " past two.", "done": True})
    assert phase() == "done", phase()

    # A new capture clears the turn (clearsTurn), which must return her to the mic.
    overlay.apply({"type": "state", "state": "listening"})
    assert phase() == "listening", phase()

    # DICTATION: the transcribe and tidy passes are the machine chewing, so they draw the same
    # typewriter as composing; the paste landing gets the same sparkle as a finished answer. All
    # three fell through to `idle` before — Al resting while the island said "Transcribing…".
    overlay.feed_lost()
    for state in ("transcribing", "transforming"):
        overlay.apply({"type": "state", "state": state})
        assert phase() == "working", f"{state} should draw the typewriter, got {phase()}"
    overlay.apply({"type": "state", "state": "pasted"})
    assert phase() == "done", f"a landed paste should sparkle, got {phase()}"

    # A FAULT gets the error sprite (`fail`, settling onto `held`). This had no branch at all for
    # a while, so an error fell through to `idle` — Al sat there resting while the island showed
    # a failure. It outranks the reply, exactly as `isError` does in Overlay.qml.
    overlay.feed_lost()
    overlay.apply({"type": "state", "state": "thinking"})
    overlay.apply({"type": "response", "delta": "Half past two."})
    overlay.apply({"type": "response", "delta": "", "done": True})
    overlay.revealing = False
    assert phase() == "done", phase()
    overlay.apply({"type": "error", "message": "I could not reach the model.", "kind": "network"})
    assert phase() == "error", f"a fault must outrank the reply, got {phase()}"
    # ...and the mic still wins over a fault (the open-mic rule holds without exception).
    overlay.apply({"type": "state", "state": "listening"})
    assert phase() == "listening", f"an open mic must outrank a stale fault, got {phase()}"


def check_recorder(engine, app) -> None:
    """Instantiate a KeyRecorder in isolation, feed it Ctrl+Alt+1, release, and confirm it
    commits 'ctrl+alt+1' — then that a bare key is rejected. Standalone rather than found in the
    live window: the recorder is its own file precisely so its state machine is testable without
    spelunking a Flickable's delegate tree. `cfg` is in the engine's context, so validateBinding
    works exactly as in the window."""
    from PySide6.QtCore import Qt, QEvent, QUrl
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtQml import QQmlComponent

    comp = QQmlComponent(engine, QUrl.fromLocalFile(str(HERE / "KeyRecorder.qml")))
    rec = comp.create(engine.rootContext())
    assert rec is not None, "KeyRecorder.qml did not load:\n  " + "\n  ".join(
        e.toString() for e in comp.errors())

    committed: list[str] = []
    rec.committed.connect(lambda c: committed.append(c))

    def press(key, text=""):
        rec.event(QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier, text))
        app.processEvents()

    def release(key):
        rec.event(QKeyEvent(QEvent.Type.KeyRelease, key, Qt.KeyboardModifier.NoModifier))
        app.processEvents()

    rec.metaObject().invokeMethod(rec, "start")
    app.processEvents()
    press(Qt.Key.Key_Control)
    press(Qt.Key.Key_Alt)
    press(Qt.Key.Key_1, "1")
    assert rec.property("keyName") == "1", rec.property("keyName")
    release(Qt.Key.Key_1)
    assert committed == ["ctrl+alt+1"], committed
    assert rec.property("recording") is False, "commit must end the recording"

    # A bare key must not commit — the daemon would reject it, so the recorder must too.
    committed.clear()
    rec.metaObject().invokeMethod(rec, "start")
    app.processEvents()
    press(Qt.Key.Key_5, "5")
    release(Qt.Key.Key_5)
    assert committed == [], "a modifier-less binding must be rejected, not committed"
    assert rec.property("invalid") is True, "a rejected capture should flag itself"
    rec.metaObject().invokeMethod(rec, "stop")
    app.processEvents()
    # Abandoning a rejected capture (Esc / click-away / tab-off, all routed through stop()) must
    # clear the danger-red border — otherwise the box sits at rest wearing an error over a valid
    # value until the next click.
    assert rec.property("invalid") is False, "stop() must clear the invalid flag on abandon"
    print("  recorder: Ctrl+Alt+1 captured and committed; bare key rejected; abandon clears invalid")


def check_new_fixes(win, cfg, settle) -> None:
    """Guards for the 2026-08-02 fix batch. Each is written to FAIL when its fix is reverted —
    a warning-only gate cannot see any of these."""
    import time

    from backend.llm import providers as _providers

    def walk(item):
        yield item
        for ch in item.childItems():
            yield from walk(ch)

    # M5 — the Dictate section routes rows by TYPE. A bool (local_server_stop_on_quit) rendered by
    # the provider-shaped CleanupRow came out dead: toggle stuck off, dropdown showing the literal
    # "true". Every live CleanupRow must carry a provider row (a real toggledBy), never a bool.
    win.setProperty("section", "models")
    settle()
    cleanup_rows = [it for it in walk(win.contentItem()) if it.property("toggleKey") is not None]
    assert cleanup_rows, "no CleanupRow on the Models pane — the item walk missed the delegates"
    for cr in cleanup_rows:
        k = cr.property("key")
        assert cr.property("toggleKey"), \
            f"a CleanupRow with an empty toggleKey — the non-provider row {k!r} got the wrong delegate"
        assert cfg.meta[k]["type"] == "provider", \
            f"CleanupRow is rendering a {cfg.meta[k]['type']} setting ({k!r}); route it by type"

    # A LOCAL provider must be addable end to end through the real controls. Local Add now
    # requires a successful trial exactly as cloud does (uniform canCommit), where before it was
    # committable on an endpoint alone yet could never populate a model, and so was a dead end.
    win.setProperty("addEditing", False)
    win.setProperty("addKind", "local")
    win.setProperty("addProviderId", "ollama")
    win.setProperty("addEndpoint", "127.0.0.1:11434")
    win.setProperty("addModel", "llama3.1:8b")
    win.setProperty("addTested", False)
    win.setProperty("addStep", 2)
    cfg.clearTrial()
    settle()
    assert win.property("addProbe") == "untested", win.property("addProbe")
    assert win.property("canCommit") is False, \
        "local Add must require a successful Test (addProbe ok), not merely an endpoint"
    _saved = _providers.probe
    _providers.probe = lambda pid, endpoint=None, timeout=None, key=None: (["llama3.1:8b"], "ok")
    try:
        cfg.trialProvider("ollama", "", "127.0.0.1:11434")   # exactly what the local Test button calls
        for _ in range(100):
            settle()
            if cfg.trial.get("status") == "ok":
                break
            time.sleep(0.02)
        assert cfg.trial.get("status") == "ok", f"the local trial never completed: {cfg.trial}"
        _ml = win.property("addModelList")
        _ml = _ml.toVariant() if hasattr(_ml, "toVariant") else _ml
        assert _ml == ["llama3.1:8b"], f"the local trial's models must reach the picker: {_ml}"
        win.setProperty("addModel", "llama3.1:8b")
        settle()
        assert win.property("canCommit") is True, \
            "a local provider must be committable once its trial has come back ok"
    finally:
        _providers.probe = _saved

    # m0 — the typed key must not survive the sheet closing (the singleton-window leak). Every exit
    # routes through manageOpen=false, so this covers Cancel, the X, the scrim and a Remove.
    win.metaObject().invokeMethod(win, "openAdd")
    settle()
    win.setProperty("addKind", "cloud")
    win.setProperty("addProviderId", "anthropic")
    win.setProperty("addKey", "sk-CANARY-should-clear")
    settle()
    win.setProperty("manageOpen", False)
    settle()
    assert win.property("addKey") == "", "the typed key must be cleared on every sheet exit (m0)"

    # ...and closing the WHOLE window closes the sheet and clears the key (reopen must be clean).
    win.setProperty("visible", True)
    settle()
    win.setProperty("manageOpen", True)
    win.setProperty("addKey", "sk-CANARY-2")
    settle()
    win.setProperty("visible", False)
    settle()
    assert win.property("manageOpen") is False, "hiding the window must close the sheet (m0)"
    assert win.property("addKey") == "", "hiding the window must clear the typed key (m0)"

    # #38 — Done with a typed-but-untested key keeps the sheet OPEN rather than dropping the key
    # silently. (Edit's Done is always enabled, which is the shape the bug took.)
    win.metaObject().invokeMethod(win, "openAdd")
    settle()
    win.setProperty("addKind", "cloud")
    win.setProperty("addProviderId", "anthropic")
    win.setProperty("addEditing", True)
    win.setProperty("addModel", "claude-opus-4-8")
    win.setProperty("addKey", "sk-typed-but-never-tested")
    win.setProperty("addTested", False)
    cfg.clearTrial()
    settle()
    assert win.property("addProbe") != "ok"
    win.metaObject().invokeMethod(win, "commitAdd")
    settle()
    assert win.property("manageOpen") is True, \
        "Done with an untested key must keep the sheet open, not drop the key silently (#38)"
    win.setProperty("addEditing", False)
    win.setProperty("manageOpen", False)
    settle()
    print("  new fixes: M5 delegate-by-type, local Add completable, key cleared on exit, "
          "untested key not silently dropped")


def check() -> None:
    # A throwaway settings file: the check must never read or write the real one.
    tmp = tempfile.mkdtemp(prefix="nothal-settings-check-")
    os.environ["NOTHAL_SETTINGS"] = str(Path(tmp) / "settings.json")

    # OFFLINE by construction (2026-08-02): the addProvider calls below auto-refresh, which
    # without this would read the REAL credential store and issue authenticated GETs to the cloud
    # providers on the development machine — the check is documented as needing "no credential
    # store". probe's network behaviour is providers' own selfcheck; here it returns a
    # deterministic offline answer.
    from backend.llm import providers as _providers
    _providers.probe = lambda pid, endpoint=None, timeout=None, key=None: ([], "unreachable")

    # --- the schema→UI contract, before any window exists -----------------------
    schema = settings.schema()
    pane_ids = {p["id"] for p in schema["panes"]}
    assert pane_ids, "the schema declares no panes — Config would have no bands"
    for key, s in schema["settings"].items():
        assert s.get("label"), f"{key}: a row with no label renders blank"
        assert s["pane"] is None or s["pane"] in pane_ids, f"{key}: unknown pane {s['pane']!r}"
        # A row that pairs a toggle with a value must name a toggle that exists, or the switch
        # binds to nothing and silently reads false.
        if s.get("toggledBy"):
            assert s["toggledBy"] in schema["settings"], f"{key}: no such toggle {s['toggledBy']!r}"
            assert schema["settings"][s["toggledBy"]]["type"] == "bool", key
    # Not "no unbuilt switch defaults on" — some should (the cleanup steps are on by default).
    # The rule that matters is narrower: nothing that removes a safety gate starts removed.
    assert schema["settings"]["skip_permissions"]["default"] is False, (
        "a permission bypass must never default on")
    # Connectors. The card is generated from both schemas at once, so the failure to guard
    # against is a mismatch BETWEEN them: a tool naming a connector no setting declares fails
    # closed in backend/tools.py, which is safe but invisible — the tool would simply never be
    # offered and no warning would say why.
    from shared.config import load_schemas
    declared = {s["connector"] for s in schema["settings"].values() if "connector" in s}
    assert declared, "the connectors pane declares nothing (schemas/settings.json)"
    for t in load_schemas()["tools"]["tools"]:
        assert t.get("connector") in declared, (
            f"{t['name']}: connector {t.get('connector')!r} has no setting — the tool would be "
            f"silently withheld from the model forever")
        assert t.get("label"), f"{t['name']}: no label, so its connector card lists a blank line"
    for key, s in schema["settings"].items():
        if "connector" not in s:
            continue
        assert s["type"] == "bool" and s["pane"] == "connectors", key
        assert s.get("help"), f"{key}: a connector card with no 'Reaches' text is consent to nothing"
        # The default posture, stated as a rule rather than trusted per entry: consent to
        # anything personal is asked for, never assumed. System is the one exception — the time
        # and the battery are not personal data.
        assert s["default"] is (key == "connector_system"), (
            f"{key}: only the System connector may default on")

    for pid, p in schema["providers"].items():
        assert p.get("name"), f"{pid}: a provider with no name renders blank"
        assert isinstance(p.get("capabilities", {}), dict), pid
        assert p.get("auth") in ("key", "endpoint"), pid
        if p["auth"] == "key":
            assert p.get("credential"), f"{pid}: a keyed provider needs a credential name"

    app = QApplication.instance() or QApplication(sys.argv)
    check_icon_font()
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(HERE.parent))          # so `import frontend` finds Theme

    # Both held in locals for the check's lifetime: a context property does not own the
    # Python object, so an inline `setContextProperty("overlay", OverlayModel())` is collected
    # and every binding onto it reads null. (The lamp reads `overlay.state`, so it fails first.)
    cfg = SettingsModel()
    overlay = OverlayModel()
    engine.rootContext().setContextProperty("cfg", cfg)
    engine.rootContext().setContextProperty("overlay", overlay)
    engine.rootContext().setContextProperty("fontFamily", "Arial")
    engine.rootContext().setContextProperty("reducedMotion", False)
    # Al in the top bar: the same provider + player the real host wires, so the
    # window's Al row renders here instead of throwing an unknown-source / undefined warning.
    engine.addImageProvider("al", al.AlImageProvider())
    al_player = al.QmlAl(model=overlay)
    engine.rootContext().setContextProperty("alPlayer", al_player)

    warnings: list[str] = []
    engine.warnings.connect(lambda ws: warnings.extend(w.toString() for w in ws))

    # Widen the gate to RUNTIME binding errors (2026-08-02). `engine.warnings` catches
    # load-time warnings but NOT a TypeError/ReferenceError thrown when a binding RE-EVALUATES in a
    # state the check drives — exactly the class this check most needs to catch: an unguarded
    # `overlay.state` read null and printed a TypeError on screen while the gate said "no warnings".
    # Those go through Qt's message log, so a handler sees them; filter to the binding-error shapes.
    runtime_errors: list[str] = []

    def _capture_qml_errors(_mode, _ctx, message):
        print(message, file=sys.stderr)   # keep Qt's own output visible, as the default handler does
        if any(k in message for k in ("TypeError", "ReferenceError", "is not defined",
                                      "Unable to assign", "Cannot read property",
                                      "Cannot assign")):
            runtime_errors.append(message)

    qInstallMessageHandler(_capture_qml_errors)

    engine.load(QUrl.fromLocalFile(str(HERE / "SettingsWindow.qml")))
    roots = engine.rootObjects()
    assert roots, "SettingsWindow.qml did not load:\n  " + "\n  ".join(warnings)
    win = roots[0]

    def settle() -> None:
        for _ in range(3):
            app.processEvents()

    # --- every state the window has ---------------------------------------------
    # Empty: Model selection before any provider is added. This is the first-run screen and
    # the one most likely to break, because every card binding is evaluated against nothing.
    assert cfg.models == {}, "the check must start from an empty profile"
    # Every section, so every binding in each view is evaluated — a throw in Connectors or
    # Config would otherwise hide until that section is shown.
    # Every pane the schema declares — the sidebar and this property share one vocabulary, so a
    # pane added to the JSON is walked here without touching this list.
    for pane in pane_ids:
        win.setProperty("section", pane)
        settle()
    # A Repeater over an empty list throws nothing and draws nothing, so the pane being EMPTY is
    # exactly the failure the warning gate cannot see. One card per declared connector.
    assert len(cfg.rowsFor("connectors")) == len(declared), cfg.rowsFor("connectors")

    win.setProperty("section", "models")
    cfg.addProvider("anthropic")                   # one provider: no Primary pill yet
    settle()
    assert cfg.values["primary"] == "anthropic"

    cfg.addProvider("groq")                        # two: Primary appears on both cards
    settle()
    # Capability-driven rows: the pane must not offer a control the provider lacks.
    caps = cfg.catalog["anthropic"]["capabilities"]
    assert "effort" in caps, "Claude should offer the effort row"

    # Effort is a WORDS dropdown reading its labels from the schema (2026-08-03) — it was a dot
    # cluster, which was hard to read and drew `none` as one dot, i.e. "a little effort" when on
    # Ollama's wire it is the OFF switch. Every level any card offers must have a word, or the
    # picker prints a wire token at the user.
    labels = cfg.effortLabels
    assert labels, "effort_labels missing from the schema"
    for pid, card_ in cfg.catalog.items():
        for level in (card_.get("capabilities") or {}).get("effort") or []:
            assert level in labels, f"{pid} offers effort {level!r} with no word in effort_labels"
    assert labels.get("xhigh") == "Extra" and labels.get("none") == "None", labels
    assert not cfg.catalog["groq"]["capabilities"], "Groq offers neither — its card is one row"

    # A CAPABILITY IS A PROMISE THAT SOMETHING SENDS IT (2026-08-03). `thinking` was declared on
    # five cards and consumed by NOTHING in backend/ — the window drew a live toggle that no
    # adapter could act on, on every provider that declared it. Removed rather than relabelled,
    # the same ruling the dead `context` capability got. This asserts the rule, not the absence:
    # re-adding `thinking` to a card is fine the day an adapter reads it, and fails until then.
    for pid, card_ in cfg.catalog.items():
        declared = (card_.get("capabilities") or {}).get("thinking")
        assert not declared, (
            f"{pid} declares capabilities.thinking, but nothing in backend/ reads it — either wire "
            f"a consumer or drop the declaration (the `context` precedent)")

    cfg.addProvider("ollama")                      # local: the second group appears
    settle()
    assert any(cfg.catalog[p]["where"] == "local" for p in cfg.models), "no local provider added"

    # The two-model VRAM note, RENDERED (2026-08-02). settings_model checks the condition; this
    # checks the Dictate row can actually grow to hold it — the row's height is computed from its
    # label and pickers, and the note is added below with the centred children offset up by half.
    # Drive it through the real settings so the binding chain is the one the window uses.
    _before = win.property("height")
    settings.set("models", {"ollama": {"on": True, "model": "qwen3:8b"}})
    settings.set("primary", "ollama")
    settings.set("cleanup_dictation", "ollama")
    settings.set("cleanup_dictation_model", "")
    cfg.changed.emit()
    settle()
    assert cfg.localTwoModelNote == "", "one model on both roles must not warn"
    settings.set("cleanup_dictation_model", "qwen3:14b")     # now they differ
    cfg.changed.emit()
    settle()
    assert cfg.localTwoModelNote != "", "two local models must warn"
    settings.set("cleanup_dictation_model", "")              # back to a quiet window
    cfg.changed.emit()
    settle()
    assert win.property("height") == _before, "the note must not leave the window resized"

    win.setProperty("manageOpen", True)
    settle()

    # The Add-a-model form, both sides. Step 2's rows are bound to the chosen provider's
    # capabilities, so walking the whole catalogue is what proves a provider with no effort
    # scale, or no models to list, does not throw.
    win.setProperty("addStep", 2)
    for where in ("cloud", "local"):
        win.setProperty("addKind", where)
        for pid in cfg.providersFor(where):
            win.setProperty("addProviderId", pid)
            for has_key in (False, True):
                win.setProperty("addHasKey", has_key)
                settle()
    win.setProperty("addStep", 1)
    settle()
    # Credential state is a property, not a slot call, so the chips refresh after a save.
    # (The expanded key editor's own bindings are still constructed with the delegate — QML
    # builds a collapsed item, it just does not paint it — so a throw in there is caught here.
    # What is NOT covered is the interaction itself; that needs a real mouse.)
    assert set(cfg.keys) == set(cfg.catalog), "every provider needs a credential state"
    assert all(v in ("stored", "none", "unavailable") for v in cfg.keys.values()), cfg.keys
    # The page must go inert while a modal is up, or it scrolls out from under the sheet when the
    # wheel turns (2026-07-31). Asserted as a PROPERTY rather than by driving a synthetic wheel
    # event: this harness delivers only one wheel per run, so an event-based version of this check
    # passed with the fix removed — it could not tell "blocked" from "never arrived".
    scroller = win.findChild(QObject, "scroller")
    assert scroller is not None, "no Flickable named 'scroller' — the scroll lock cannot be checked"
    assert scroller.property("enabled") is False, (
        "the page is still live behind an open sheet — bind the scroll area's `enabled` to "
        "`root.modalOpen`, and OR every modal into that one property")
    win.setProperty("manageOpen", False)
    settle()
    assert scroller.property("enabled") is True, "the page stayed inert after the sheet closed"

    # Toggling through the model card's own controls, which is where most bindings live.
    cfg.setModel("anthropic", "on", False)
    settle()
    cfg.setModel("anthropic", "on", True)
    cfg.setModel("anthropic", "thinking", True)
    cfg.setModel("anthropic", "effort", "max")
    settle()

    # The one accent control in the app, and the lamp that follows it.
    cfg.set("listen_for_me", True)
    settle()
    cfg.set("listen_for_me", False)
    settle()

    # Back to empty — removing the last provider must restore the empty state cleanly.
    for pid in list(cfg.models):
        cfg.removeProvider(pid)
    settle()
    assert cfg.models == {} and cfg.values["primary"] == ""

    # The keybind recorder: drive it with synthetic key events and confirm it captures a combo
    # and commits the validated string. Done here because its logic (modifier accretion, the
    # commit-on-release) has no non-Qt half to unit-test.
    # The stored-key leak (2026-08-01): the Add sheet must answer ONLY from the key typed into it.
    # Anthropic already has a live model list in the provider cache by this point (addProvider +
    # the roster's fetch), which is exactly the state that used to make a junk key look valid — so
    # this asserts the sheet stays empty and uncommittable until a trial succeeds.
    cfg.addProvider("anthropic")
    settle()
    win.setProperty("addEditing", False)
    win.setProperty("addProviderId", "anthropic")
    win.setProperty("addKind", "cloud")
    win.setProperty("addKey", "sk-obviously-not-a-real-key")
    win.setProperty("addHasKey", True)
    win.setProperty("addTested", False)
    win.setProperty("addStep", 2)
    settle()
    # A QML `var` crosses as a QJSValue on some paths and as a plain list on others, depending on
    # what the binding returned — so unwrap defensively, or a REGRESSION shows up as an
    # AttributeError here instead of as the assertion that explains it.
    _models = win.property("addModelList")
    _models = _models.toVariant() if hasattr(_models, "toVariant") else _models
    assert _models == [], (
        "an untested key must offer no models, whatever the provider cache holds: "
        f"{_models}")
    # It prompts, but it must not report a VERDICT it has not got.
    _msg = win.property("addProbeMessage")
    assert "Press Test" in _msg, _msg
    for _claim in ("models available", "rejected"):
        assert _claim not in _msg, f"an untested key must not report a verdict: {_msg}"
    assert win.property("canCommit") is False, "Add must not commit on an untested key"
    # ...and a successful trial is what unlocks it, not merely having typed something.
    assert win.property("addProbe") == "untested", win.property("addProbe")
    for pid in list(cfg.models):
        cfg.removeProvider(pid)
    settle()

    # The Add form's values must SURVIVE the crossing into Python. A QML object literal arrives
    # as a QJSValue, not a dict, so an isinstance check silently dropped every one of them and
    # stored the schema fallbacks instead — a chosen model vanished on Add (2026-08-01).
    # addKey is cleared first: commitAdd now refuses to close on a typed-but-untested key (the
    # silent-drop fix), and the prior sub-test left one in the box.
    win.setProperty("addKey", "")
    win.setProperty("addProviderId", "openai")
    win.setProperty("addKind", "cloud")
    win.setProperty("addModel", "gpt-5.6-sol")
    win.setProperty("addEditing", False)
    win.setProperty("addStep", 2)
    settle()
    win.metaObject().invokeMethod(win, "commitAdd")
    settle()
    _entry = cfg.models.get("openai", {})
    assert _entry.get("model") == "gpt-5.6-sol", (
        f"the Add form's chosen model did not survive commitAdd: {_entry}")
    cfg.removeProvider("openai")
    settle()

    check_new_fixes(win, cfg, settle)

    check_recorder(engine, app)

    check_al_turn(al_player, overlay, settle)

    qInstallMessageHandler(None)                     # restore the default handler
    assert not warnings, (
        f"{len(warnings)} QML warning(s) — a binding is throwing:\n  "
        + "\n  ".join(warnings))
    # Runtime binding errors (the class the load-time gate above cannot see). Deduped: one broken
    # binding re-evaluates many times over a run.
    assert not runtime_errors, (
        f"{len(set(runtime_errors))} runtime QML binding error(s) — a binding threw while "
        f"evaluating (an unguarded null read is the usual cause):\n  "
        + "\n  ".join(sorted(set(runtime_errors))))

    # Destroy the engine HERE, while `cfg` and `overlay` are still alive. Left to Python, this
    # function's locals are freed in arbitrary order — the context objects went first, and every
    # binding that reads one re-evaluated against null on the way down: 53 TypeErrors printed
    # AFTER the assertions above had passed, so the check announced "no QML warnings" while a
    # wall of them scrolled by. Measured 53 -> 0, with the check still passing.
    #
    # `shiboken6.delete`, NOT `deleteLater()`: deleteLater only POSTS a deferred-delete event,
    # and by this point there is no event loop left to process it — it is a silent no-op here
    # (measured: still 53). shiboken6.delete runs the C++ destructor immediately.
    import shiboken6
    shiboken6.delete(engine)
    os.environ.pop("NOTHAL_SETTINGS", None)
    print(f"settings_check OK: window built from {len(schema['settings'])} declared settings, "
          f"{len(pane_ids)} panes, {len(schema['providers'])} providers; "
          f"empty/one/two/local/manage states clean, no QML warnings (load-time or runtime)")


if __name__ == "__main__":
    check()
