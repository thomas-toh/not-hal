// The Teleprompter island. Locked design: sandbox/teleprompter-mockup.html;
// window recipe + concave-corner path proven in sandbox/qml_spike/ (see NOTES.md).
//
// Solid black, fused to the top screen edge: bottom corners round inward, top corners flare
// OUTWARD into the edge. Built as a plain Rectangle plus two small flare pieces — only the
// outward flares need a real path, because Rectangle (like CSS border-radius) rounds inward.
//
// Everything here is driven by the `overlay` context property (frontend/model.py); the
// island renders what arrives and never talks back.
import QtQuick
import QtQuick.Shapes
import QtQuick.Window
import frontend                            // Theme — the design tokens (Theme.qml)

Window {
    id: root

    // --- locked geometry / palette (mockup v7) ---
    // Al, inside the pill on the left — switchable (settings → preferences, "Show Al in the
    // island"). OFF is not an approximation of the pre-Al island: every expression she touches
    // is written `alOn ? … : <the original>`, so the classic pill collapses to exactly the
    // geometry it had before she existed. `overlay_check` asserts that, because "drop-in" is a
    // claim that rots silently.
    //
    // She is drawn at 52 (2× the kit's 26 cell — integer scales only) in a pill that is 46 tall,
    // and that is deliberate: the kit's cell carries 3–4 cells of dead margin around the ghost,
    // so what actually spills past the silhouette is at most 3px, and only `done/sparkle` (1px,
    // top) and `needs-permission/granted` (3px, bottom) spill at all. Letting the box overhang
    // rather than growing `baseH` to contain it is a deliberate choice: the margin is a safety area,
    // and using it is cheaper than making the island 35% taller.
    readonly property bool alOn: cfg.values.al_in_island !== false
    readonly property int alPx: 52
    readonly property int alLeft: 4                     // inner-left edge -> Al's CELL. Far less
                                                         // than padSide because the cell already
                                                         // carries ~12px of its own margin before
                                                         // the ghost, so padSide was paid twice.
                                                         // The floor is ~3: below that the props
                                                         // she holds (the listening tablet, the
                                                         // guitar) crowd the pill's bottom-left
                                                         // corner radius, which they overhang.
    readonly property int alGap: 6                      // her cell -> the wave / the text. Small
                                                         // because the cell ALSO carries its own
                                                         // right margin (~14px at rest), so the
                                                         // visual gap is roughly 20px, not 6.
    readonly property int alCol: alLeft + alPx + alGap
    // What the content is inset by on the left: Al's whole column, or the plain padding.
    readonly property int leftInset: alOn ? alCol : padSide
    readonly property int openW: 440
    readonly property int baseH: 46
    readonly property real flare: 18            // outward concave fillet at the top edge
    readonly property real botR: 13.5           // bottom corner radius (convex)
    // Scrolling listening waveform — a level history flowing right->left (dots in silence, swelling
    // into bars on sound). Params tuned in sandbox/teleprompter-waveform-mockup.html.
    // Cut when Al is in: she takes 76px of the compact pill, and at 20 samples the pill grew
    // from 230 to 306. Fourteen keeps it near its old width, and the wave is a rolling history
    // whose oldest samples fade out on the left anyway — so the cut comes off the end that was
    // already disappearing.
    readonly property int  waveCount: alOn ? 14 : 20
    readonly property real waveBarW: 3
    readonly property real waveGap: 6
    readonly property real waveMaxH: 24
    readonly property real waveGain: 4          // real mic RMS sits low; lift it so speech reads as bars
    readonly property int  waveTickMs: 60       // flow rate (also the per-slot height smoothing)
    // The fade was tuned when the wave floated alone in the middle of an empty pill. With Al as
    // its left neighbour, 22px of it ate the wave's first two bars ON TOP of the gap, which read
    // as a hole rather than as breathing room — so the Al theme fades less.
    readonly property real waveFade: alOn ? 10 : 22   // ends fade into the black pill
    readonly property real waveWidth: waveCount * waveBarW + (waveCount - 1) * waveGap
    readonly property int  compactW: alOn ? (alCol + Math.round(waveWidth) + padSide)
                                           : (Math.round(waveWidth) + 20)   // hugs the wave
    // Colour, opacity, type and motion all come from the Theme singleton (Theme.qml) — see
    // the note there on why island geometry stays local while those do not.
    // Whole pixels by construction. Qt rounds the WINDOW height to integers, so a fractional
    // line box (18 * 1.3 = 23.4) left the real height short of what the layout assumed — the
    // shortfall came out of the bottom, shrinking the gap AND clipping the last line's
    // descenders by up to 0.8px at three lines. Snapping the box to a whole pixel makes every
    // derived height exact, so the bottom gap is identical at 1, 2 and 3 lines.
    readonly property int lineBox: Math.round(Theme.fontSize * Theme.lineHeight)   // 23
    readonly property int padSide: 20                    // inside the body, excluding the flare
    // padTop + lineBox + padBottom is FORCED to equal baseH — otherwise the pill would grow the
    // moment a single line of text appears. So the vertical padding can only be redistributed,
    // never added to. floor() (not round()) hands the odd pixel to the BOTTOM, so text sits a
    // hair high — the mockup's optical intent, which split 12/14 the same way.
    readonly property int padTop: Math.floor((baseH - lineBox) / 2)                // 11
    readonly property int padBottom: baseH - padTop - lineBox                      // 12
    readonly property int maxLines: 3                    // island stops growing here, then scrolls

    // --- expanded view / "peek" (the island now takes input over its
    // silhouette, wired natively in __main__.py). Hover a showing answer for a hint, click to grow
    // it into the full turn (PeekPanel). Everything here is inert until `peeking`, so the island's
    // normal behaviour is unchanged. ---
    readonly property int peekW: 560                      // peek slab content width (cf. openW)
    readonly property int peekMinH: 150                   // floor: a short peek never looks cramped
    readonly property int peekMaxH: Math.round(Screen.height * 0.62)  // ceiling; reply scrolls past it
    readonly property int hintNudge: 5                    // hover-hint downward nudge
    property bool peeking: false
    property bool hovering: false
    // there is a settled answer to expand (not merely listening/thinking, not a fault)
    readonly property bool peekable: overlay.reply !== "" && !isError
    property real peekFade: peeking ? 1 : 0
    Behavior on peekFade { NumberAnimation { duration: root.reducedMotion ? 0 : Theme.durationPeek; easing.type: Easing.OutCubic } }
    // Host (frontend/__main__.py) does the clipboard write and the save dialog — a QML file
    // has no business with either (user-initiated export, host-owned).
    signal copyRequested(string text)
    signal saveRequested(string text)
    // A new capture (the hotkey, a new turn) clears the reply → not peekable → leave the peek, so
    // the island returns to the compact view instead of a large empty box mid-turn.
    onPeekableChanged: if (!peekable) peeking = false
    // Reset the peek only once the island has FULLY faded out (visible → false), so a dismiss fades
    // at the peek size instead of shrinking on screen first (the size-snap is then invisible). The
    // mid-turn case (a new capture, peekable→false above) DOES shrink on screen — that is wanted.
    onVisibleChanged: if (!visible) { peeking = false; bootLatch = false }  // fully hidden — reset
    // (The viewport starts at the very top of the window — y = 0 — so a scrolled-off line peeks
    // through above rather than being clipped. There was a `fadeTop` knob for a non-zero top
    // inset; it was always 0, so it is gone, U-02.)
    // FINAL layout width — the text never reflows mid-animation. Shrinks by the gutter when
    // the latency readout is on, so an instrument can never overlap a reply.
    readonly property real textW: openW - leftInset - padSide - latencyGutter
    // Measured from the font, at the WIDEST reading it can ever show (both readings appear at
    // once during the acceptance run). A guessed constant undersized it; sizing to the CURRENT
    // reading would reflow the reply every time a number arrived.
    readonly property string latencyWidest: "fb 88888ms   word 88888ms"
    readonly property int latencyGutter:
        overlay.showLatency ? Math.ceil(latencyFm.advanceWidth(latencyWidest)) + 16 : 0
    // `reducedMotion` is a context property (Windows' "Show animations" setting, resolved in
    // __main__.py). Layout transitions collapse to instant; the mic bars keep their smoothing,
    // because they carry information and unsmoothed they read as jitter rather than as level.
    readonly property int moveMs: reducedMotion ? 0 : (peeking ? Theme.durationPeek : Theme.durationResize)
    readonly property int scrollMs: reducedMotion ? 0 : Theme.durationScroll
    readonly property int fadeMs: reducedMotion ? 0 : Theme.durationFade
    // The family arrives as the `fontFamily` context property: QML's font.family takes ONE
    // name (there is no CSS-style chain), so __main__.py walks FONT_STACK against the fonts
    // actually installed and hands in the winner. Install Instrument Sans for the real thing.

    // --- what to show ---
    readonly property string st: overlay.state
    // The reply replaces the prompt — never stacked (locked design). A fault outranks both.
    readonly property bool isError: overlay.error !== ""
    // ...but NOT until the prompt has finished revealing. `bodyText` used to flip the instant
    // the first model delta arrived, and the prefix test further down then read the new string
    // as "not a continuation" and reset the typewriter to zero — so any prompt longer than
    // about eleven words lost its tail, every warm turn. A *time* dwell cannot fix that: any
    // dwell shorter than the reveal truncates it exactly the same way. The invariant is
    // finish revealing → hold → swap, and only this side knows when the first part is done.
    // A fault swaps immediately: it outranks the prompt and is the more urgent thing to read.
    readonly property string prompt: overlay.transcript
    property bool promptShown: false
    onPromptChanged: promptShown = false            // a new turn earns a fresh hold
    readonly property bool replyReady: overlay.reply !== "" && (promptShown || prompt === "")
    readonly property string bodyText: isError ? overlay.error
                                     : dictWord !== "" ? ""       // dictation shows a status word, not text
                                     : (replyReady ? overlay.reply : prompt)
    // Has the typewriter caught up with everything it has been given?
    readonly property bool revealDone: reveal.shown >= bodyText.length
    // Published to the model so the OTHER window can follow the typewriter. The daemon's stream
    // finishes well before the reveal does, so `overlay.done` is the wrong clock for anything
    // miming the answer — and this is the only window that can see the real one. The settings
    // Al reads it today; Al-on-the-island will read the same flag.
    Binding {
        target: overlay
        property: "revealing"
        value: root.replyReady && !root.revealDone
    }

    Timer {                                   // the prompt's hold, before the reply takes over
        id: promptHold
        interval: Theme.durationPromptHold
        running: !root.promptShown && !root.isError && root.prompt !== ""
                 && root.bodyText === root.prompt && root.revealDone
        onTriggered: root.promptShown = true
    }

    // --- when the island stops showing ---
    // `idle` from the daemon means the DAEMON is finished — not "blank". How long an answer
    // stays up is a fact about the reveal, and this is the only process that can see it. The
    // daemon owned this decision for two revisions and blanked answers mid-sentence both times.
    // Startup (status.json v0.7.0): the daemon publishes `booting` until warm-up finishes, and the
    // island shows a small circular loader — the SAME mark as the settings Test button (Spinner.qml)
    // — with no status word. The doors are dropped meanwhile (the daemon gates them), so this is a
    // pure "not ready yet" indicator, narrower than any other mode to match the little loader.
    readonly property bool booting: st === "booting"
    readonly property int bootW: 64
    // `bootLatch` only EXTENDS the boot look through the fade-OUT: when warm-up ends the state goes
    // `idle`, whose width is the compact pill and whose Al is shown, so without it the loader
    // briefly balloons to the wide pill and flashes Al as it fades. It is set when booting begins
    // and dropped once the pill is fully hidden, or the instant a real turn takes over.
    property bool bootLatch: false
    onBootingChanged: if (booting) bootLatch = true
    // What the island DRAWS as a boot pill. Keyed on `booting` DIRECTLY (a value binding — correct
    // on the very first frame, including when `booting` is the first state the overlay ever sees),
    // ORed with the latch so it also covers the fade. Driving the visuals off the latch alone left
    // the loader missing whenever the latch's change-handler had not run for the initial value.
    readonly property bool bootShown: booting || bootLatch
    readonly property bool busy: st === "listening" || st === "thinking"
                                 || st === "speaking" || st === "error"
                                 || st === "transcribing" || st === "transforming"
                                 || st === "booting"
    property bool hidden: false                     // dwell expired, or the user dismissed
    // Dictation's terminal confirmation. `st` flips to `idle` the instant after `pasted`,
    // so this LATCH — not `st` — is what keeps the ✓ on screen for its short dwell; a new turn
    // (the next `busy`) clears it.
    property bool pasted: false
    onStChanged: {
        if (st === "pasted") pasted = true
        // Boot ends into `idle` (a hide) — keep the latch so the pill fades at its narrow size. Any
        // OTHER state after boot is a real turn taking over, so drop the latch and render normally.
        if (st !== "booting" && st !== "idle") bootLatch = false
    }
    onBusyChanged: if (busy) { hidden = false; pasted = false }  // a new turn brings the island back
    // `hidden` outranks `busy` deliberately: pressing Esc while the app is still thinking must
    // take the island away THAT INSTANT. If this read `busy || …` the island would linger
    // until the daemon got round to publishing its abort — which is the round trip that local
    // hiding exists to remove, and it would be longest exactly when the daemon is wedged.
    readonly property bool showing: !hidden && (busy || bodyText !== "" || pasted)

    // How long finished text stays put, in milliseconds, read off the user's own choice
    // (General > Preferences). The setting is a WORD like "2.5 s" rather than a number: a
    // dropdown of sensible durations needs no new control and cannot be set to zero. `parseFloat`
    // reads the number off the front and stops at the space; anything unparseable falls back to
    // the Theme constant, which is also the shipped default — so a hand-broken setting looks
    // exactly like an untouched one instead of freezing the island on screen.
    function dwellMs(choice, fallback) {
        var s = parseFloat(choice)
        return (isFinite(s) && s > 0) ? Math.round(s * 1000) : fallback
    }

    Timer {                                   // the answer's dwell — the walked-away backstop
        id: answerDwell
        objectName: "answerDwell"             // reached by name from the self-check
        // A turn that ACTED has nothing to read — you watched it happen — so it goes quickly.
        // A turn that ANSWERED stays long enough to walk back to the desk. The daemon decides
        // which; the two durations are the user's.
        interval: overlay.dwell === "quick"
                  ? root.dwellMs(cfg.values.dwell_quick, Theme.durationActionDwell)
                  : root.dwellMs(cfg.values.dwell_slow, Theme.durationAnswerDwell)
        // Restarts on every newly revealed word, so the count only ever runs from the moment
        // the last of the text actually appeared.
        running: !root.busy && !root.hidden && root.bodyText !== "" && root.revealDone && !root.peeking
        onTriggered: root.hidden = true
    }

    Timer {                                   // dictation's "Pasted ✓" beat, then the island hides
        id: pasteDwell
        objectName: "pasteDwell"              // reached by name from the self-check
        interval: Theme.durationPasteDwell
        running: root.pasted && !root.hidden
        onTriggered: { root.pasted = false; root.hidden = true }
    }

    // Esc, handled in __main__.py, which owns the key because it owns the window. Hiding is
    // local and immediate — the daemon is told separately and never waited on.
    Connections {
        target: overlay
        // Esc always dismisses the island outright — even from a peek. Just `hidden`, NOT
        // `peeking = false`: resetting peeking here animates a shrink back to compact WHILE the
        // island is also fading out, so you see it shrink THEN vanish (both, visibly). Instead it
        // fades out at the peek size, and peeking resets only once fully hidden (onVisibleChanged),
        // where the size-snap is invisible. (Esc still aborts an in-flight turn, as ever.)
        function onDismissed() { root.hidden = true }
    }
    // Two sizes, nothing else: LISTENING is the minimised pill with the wave; every other
    // visible state — thinking, prompt, reply, fault — is the standard width, with the status
    // word or the text sitting in the SAME left-aligned slot, so the handoff from
    // "Transcribing…" to your prompt has nothing to animate. Idle hides the window outright.
    // Listed positively rather than as "not listening": the orchestrator publishes the fault
    // MESSAGE before it publishes state:error, so text can arrive while the state still says
    // listening — `bodyText` has to be able to open the pill on its own or that text is lost.
    // It also keeps `idle` closed, which "not listening" would not.
    readonly property bool open: bodyText !== "" || st === "thinking"
                                 || st === "speaking" || st === "error"
                                 || dictWord !== ""

    // The WINDOW never moves or resizes: a fixed, fully transparent, click-through frame at the
    // island's largest possible size, with the island animating INSIDE it. Animating the window
    // means native move/resize operations that land a frame apart from the scene graph — newly
    // exposed area paints late, and the silhouette can be clipped mid-growth. Keep it fixed.
    //
    // The mostly-empty frame is click-through via IslandHitTest (per-region WM_NCHITTEST): it
    // returns HTTRANSPARENT everywhere but the island silhouette, so the frame never swallows a
    // click meant for the app beneath. (It was previously a blanket WS_EX_TRANSPARENT.)
    // Widest / tallest the island can EVER be — now including the peek, which is both wider
    // (peekW) and much taller (up to peekMaxH) than a normal turn. The island animates INSIDE this
    // fixed frame; if the frame is smaller than the peek, the peek is clipped — the black slab can't
    // grow and the panel is cut off. The frame stays fixed (no per-turn window resize; that tore).
    width: Math.max(openW, peekW) + 2 * flare
    height: Math.max(baseH + (maxLines - 1) * lineBox, peekMaxH)
    readonly property int islandW: (peeking ? peekW
                                    : bootShown ? bootW
                                    : (open ? openW : compactW)) + 2 * flare
    // A single line is ALWAYS exactly baseH, and each extra line adds exactly one whole line
    // box, so the bottom gap stays padBottom however many lines show. Growth stops at
    // maxLines; past that the text scrolls instead.
    readonly property int shownLines: Math.max(1, Math.min(measure.lineCount, maxLines))
    readonly property int scrolled: Math.max(0, measure.lineCount - maxLines)
    // Where the next word ends (space included). Drives the measurer, so growth runs one word
    // ahead of what is on screen.
    function wordEnd(from) {
        if (from >= bodyText.length)
            return bodyText.length;
        var i = bodyText.indexOf(" ", from);
        return i < 0 ? bodyText.length : i + 1;
    }
    readonly property int pendingEnd: wordEnd(reveal.shown)
    // The pill's TARGET size. `animW`/`animH` are the live, animating values every visual is
    // drawn from — one pair of numbers, so the silhouette, the text, the bars and the readout
    // cannot disagree about where the island is on any given frame.
    readonly property int islandH: peeking
        ? Math.max(peekMinH, Math.min(peekMaxH, peekPanel.naturalHeight))
        : ((open ? baseH + (shownLines - 1) * lineBox : baseH) + (hovering && peekable ? hintNudge : 0))
    property real animW: islandW
    property real animH: islandH
    // enabled only once fully shown (`appeared`) — see that property. Appearing/disappearing
    // snaps the size so a re-opened pill never animates down from a stale width.
    Behavior on animW { enabled: root.appeared; NumberAnimation { duration: root.moveMs; easing.type: Easing.InOutCubic } }
    Behavior on animH { enabled: root.appeared; NumberAnimation { duration: root.moveMs; easing.type: Easing.InOutCubic } }
    // Centred in the fixed frame. Both edges therefore move by the same amount in the same
    // frame — the asymmetry came from this being a native window move racing a native resize.
    readonly property real islandX: (width - animW) / 2

    // Measures the text INCLUDING the word about to appear, so the island can finish growing
    // before that word is revealed rather than the word landing on a box still catching up.
    // Never drawn, and deliberately NOT inside the viewport — it is layout arithmetic, not
    // part of the clipped content.
    // Every layout property is taken FROM textItem, never restated: if the two ever wrapped
    // differently, lineCount would describe a layout that is not on screen and the gate below
    // would silently let words land early.
    Text {
        id: measure
        objectName: "measure"           // reached by name from the self-check
        visible: false
        width: textItem.width
        wrapMode: textItem.wrapMode
        font: textItem.font
        lineHeight: textItem.lineHeight
        lineHeightMode: textItem.lineHeightMode
        text: root.bodyText.substring(0, root.pendingEnd)
    }

    // How far the fade may reach before it would dim a real glyph. The first line's BOX starts
    // at padTop, but its ink starts lower: FixedHeight centres the natural line in the box, and
    // there is blank space above capitals. Derived from live metrics, so changing the Theme's
    // fontSize or lineHeight re-derives it instead of silently dimming text.
    FontMetrics { id: fm; font: textItem.font }
    readonly property real inkTop: padTop + (lineBox - fm.height) / 2 + (fm.ascent - fm.capitalHeight)
    readonly property real fadeH: Math.max(4, inkTop - 0.5)   // ~16px at 18/1.3

    // Gone = nothing to say and nothing left to read (no longer simply `st === "idle"`).
    // The tray, not the island, says "alive".
    visible: showing || entrance > 0.01
    opacity: entrance
    color: "transparent"
    flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
           | Qt.WindowDoesNotAcceptFocus
    // virtualX/Y, not 0 — Screen.width is this screen's width but x is in VIRTUAL-DESKTOP
    // coordinates, so on a multi-monitor desktop (or the Mac with an external display)
    // omitting the origin puts the island on the wrong screen. Correct on a single display too.
    x: Screen.virtualX + Math.round((Screen.width - width) / 2)
    y: Screen.virtualY

    // Fades the whole window rather than wrapping the contents in a transformed Item: the
    // visuals are interleaved with the declarations they depend on, so wrapping reparents those
    // too and every `root.<prop>` breaks. `visible` lingers so the fade-out can finish.
    property real entrance: showing ? 1 : 0
    Behavior on entrance {
        NumberAnimation { duration: root.fadeMs; easing.type: Easing.OutCubic }
    }
    // The entrance fade has fully settled. The island's SIZE animates only while this is true
    // (see the animW/animH Behaviors): a resize that coincides with the pill appearing or
    // disappearing SNAPS instead of animating, so a re-opened pill is already the right size the
    // instant it shows — rather than fading in at the last turn's width and shrinking, because
    // `idle` no longer clears the turn so the width lingers while hidden. Keys off the
    // pill's own visibility, so it holds for ANY re-open path (new turn · wake word · a future
    // expanded-view hotkey), not just the one that produced the bug. `entrance` LAGS `showing`
    // (it fades over fadeMs), which is exactly why gating on it works where gating on `showing`
    // would not — the target-change and the appearance no longer coincide.
    readonly property bool appeared: entrance > 0.99

    // ---- the silhouette: a plain box, with the two flares stuck on the sides ----
    // The moving part is a plain Rectangle — cheap, and antialiased without help. The only real
    // curves are the two flares, and those NEVER change size: the island grows and shrinks around
    // them, so they only move. Nothing re-tessellates during an animation.
    Rectangle {
        id: slab
        x: root.islandX + root.flare        // the flares live outside the body, one on each side
        width: root.animW - 2 * root.flare
        height: root.animH
        color: Theme.surface
        // Fused to the top screen edge, so only the bottom corners are round. Per-corner radius
        // is a Rectangle feature (Qt 6.7+) — no path needed for the convex half of the shape.
        bottomLeftRadius: root.botR
        bottomRightRadius: root.botR
        topLeftRadius: 0
        topRightRadius: 0
    }

    // The flares: concave fillets that flow outward from the body into the screen edge. These
    // DO need a real path — Rectangle (like CSS border-radius) can only round inward.
    // A Repeater rather than two hand-written Shapes: mirrored geometry written twice is
    // geometry that can be edited once, and the two sides would silently stop matching.
    Repeater {
        model: 2                                       // 0 = left flare, 1 = right
        Shape {
            id: flare
            required property int index
            readonly property bool isLeft: index === 0
            // Local coords, an 18x18 box: `ox` is the edge against the screen corner, `bx` the
            // edge against the body. Naming them makes one path serve both mirrorings.
            readonly property real ox: isLeft ? 0 : root.flare
            readonly property real bx: isLeft ? root.flare : 0

            x: isLeft ? slab.x - root.flare : slab.x + slab.width
            y: 0
            width: root.flare
            height: root.flare
            antialiasing: true
            // CurveRenderer, not the default GeometryRenderer: the latter antialiases by
            // multisampling the window surface, and this window is frameless/translucent with
            // no MSAA, so the curve came out hard-edged and pixellated.
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                fillColor: Theme.surface
                strokeWidth: 0
                strokeColor: "transparent"
                startX: flare.ox                                    // the screen corner
                startY: 0
                PathLine { x: flare.bx; y: 0 }                      // along the top edge
                PathLine { x: flare.bx; y: root.flare }             // down the body's side
                PathQuad {                                          // and back out, concave
                    controlX: flare.bx; controlY: 0
                    x: flare.ox;        y: 0
                }
            }
        }
    }

    // ---- thinking: a morphing status word ----
    // A word rests, then the next one wipes over it left-to-right, one column per tick. Marking
    // the sweep with a block caret reads as monospace, so here the letters just flip — the
    // wipe carries itself. Words describe TRANSCRIBING, because that is the phase this covers:
    // it shows from end-of-speech until the transcript lands, then the prompt takes the slot.
    readonly property bool loaderOn: st === "thinking" && bodyText === ""

    // Dictation's own status words: steady, not the assistant's morphing loader. `pasted`
    // shows a check — the ONLY on-screen signal the text reached the caret, since dictation
    // pastes into another app and never shows a reply. "Tidying" matches the settings "Tidy" label.
    readonly property string dictWord:
          st === "transcribing" ? "Transcribing…"
        : st === "transforming" ? "Tidying…"
        : root.pasted           ? "Pasted"
        : ""

    // The wipe's state lives ON the timer that drives it, like the typewriter's `reveal.shown`
    // below, rather than as loose mutable properties on the Window. Only `shown` is read outside.
    Timer {                                   // the wipe: one column per tick
        id: sweep
        objectName: "sweep"                   // reached by name from the self-check
        property var words: [
            "transcribing", "deciphering", "decoding", "parsing",
            "untangling", "interpreting", "unpicking", "resolving",
        ]
        property string shown: ""             // the settled-or-mid-wipe word, bare
        property string wordFrom: ""
        property string wordTo: ""
        property int at: 0
        property string last: ""
        property bool active: false

        // The bare word only — the ellipsis is static punctuation appended at render. If it
        // took part in the wipe, a longer outgoing word would leave its own "…" trailing for a
        // tick and you'd see "Interpreting……".
        function labelFor(w) { return w.charAt(0).toUpperCase() + w.slice(1) }

        function next() {
            var w = last;
            while (w === last && words.length > 1)
                w = words[Math.floor(Math.random() * words.length)];
            last = w;
            wordFrom = shown;
            wordTo = labelFor(w);
            at = 0;
            active = true;
        }

        interval: 28
        repeat: true
        running: root.loaderOn && active
        onTriggered: {
            var span = Math.max(wordFrom.length, wordTo.length);
            if (at < span) {
                shown = wordTo.slice(0, at) + wordFrom.slice(at);
                at++;
            } else {
                shown = wordTo;               // settled: rests until the next word
                active = false;
                hold.restart();
            }
        }
    }

    Timer {                                   // dwell on a settled word
        id: hold
        interval: 1500
        onTriggered: if (root.loaderOn) sweep.next()
    }

    onLoaderOnChanged: {
        hold.stop();
        sweep.shown = "";
        if (loaderOn) {
            sweep.last = "";
            sweep.next();
        } else {
            sweep.active = false;
        }
    }

    // ---- listening: a scrolling level history ----
    // A stream that flows right->left, so time shows even in silence (as dots); when the mic catches
    // something the slots swell into bars, then keep scrolling out the left edge. Present ONLY while
    // 'mic' messages arrive (feed.py drops the level to 0 when they stop) — a truthful
    // indicator, never inferred from state alone. Dimensions live in the geometry block above.
    property var waveLevels: []               // rolling buffer: [0] leftmost/oldest, last newest/right
    function waveReset() { var a = []; for (var i = 0; i < root.waveCount; i++) a.push(0); waveLevels = a; }
    Component.onCompleted: waveReset()
    Timer {
        id: waveTimer
        interval: root.waveTickMs; repeat: true
        running: root.st === "listening"
        onRunningChanged: if (running) root.waveReset()      // start each listen from silence
        onTriggered: { var a = root.waveLevels.slice(1); a.push(Math.min(1, overlay.mic * root.waveGain)); root.waveLevels = a; }
    }
    Item {
        id: bars
        objectName: "bars"
        opacity: 1 - root.peekFade
        visible: root.st === "listening"
        width: root.waveWidth
        height: root.waveMaxH
        // Centred in the pill when Al is out — but centring it with her IN put its left fade
        // straight over her cell (they overlapped by 30px), which is why there was no gap. With
        // her in, it starts after her column and the pill hugs it, leaving `padSide` on the right.
        x: root.alOn ? (root.islandX + root.flare + root.alCol)
                      : (root.islandX + (root.animW - width) / 2)
        y: (root.animH - height) / 2
        Row {
            anchors.fill: parent
            spacing: root.waveGap
            Repeater {
                model: root.waveCount
                Rectangle {
                    width: root.waveBarW
                    radius: root.waveBarW / 2                 // rounded ends: a dot when short, a bar when tall
                    color: Theme.textPrimary
                    anchors.verticalCenter: parent.verticalCenter
                    height: root.waveBarW + (root.waveLevels[index] || 0) * (root.waveMaxH - root.waveBarW)
                    Behavior on height { NumberAnimation { duration: root.waveTickMs; easing.type: Easing.Linear } }
                }
            }
        }
        // fade the ends into the black pill: samples flow IN on the right, OUT at the left
        Rectangle {
            anchors { left: parent.left; top: parent.top; bottom: parent.bottom }
            width: root.waveFade
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: Theme.surface }
                GradientStop { position: 1.0; color: "transparent" }
            }
        }
        Rectangle {
            anchors { right: parent.right; top: parent.top; bottom: parent.bottom }
            width: root.waveFade
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: "transparent" }
                GradientStop { position: 1.0; color: Theme.surface }
            }
        }
    }

    // ---- transcript / reply / fault ----
    // Viewport: clips to the island's inner area, so as the island animates open (or grows a
    // line) the text is REVEALED rather than spilling outside the black. Its width follows the
    // animating island, but the Text inside is laid out at the final width — so the line count
    // never changes mid-animation and the height animates once, straight to its target.
    // Al. Deliberately a SIBLING of the viewport, not a child: the viewport clips (it has to,
    // for the scroll), and clipping her would defeat the whole point of letting the cell overhang.
    // `y` is pinned to the first line box rather than centred on the animating height, so she
    // stays put as the island grows downward for more lines — same reason the text is top-anchored.
    // max(0, …) keeps the cell inside the WINDOW: overhanging the pill is intended, being clipped
    // away by the window edge is not.
    Image {
        objectName: "al"                      // reached by name from the checks / sandbox builder
        // ROUNDED, and that is not cosmetic: `islandX` is (width - animW) / 2, so any odd pill
        // width lands her on a half pixel — and half a pixel of offset is exactly what destroys
        // pixel art, since every cell edge then straddles two device pixels and Qt has to blend
        // them. The island's own antialiased shapes do not care; a 26-cell sprite does.
        x: Math.round(root.islandX + root.flare + root.alLeft)
        y: Math.round(Math.max(0, (root.baseH - root.alPx) / 2))
        width: root.alPx; height: root.alPx
        sourceSize: Qt.size(root.alPx, root.alPx)
        smooth: false                          // nearest-neighbour: keep the cells crisp
        opacity: root.entrance                 // fades with the island, never on its own
        // Hidden during boot AND through the boot pill's fade-out (bootShown): startup shows the
        // circular loader instead of Al, and she must not flash in as the loader fades out.
        visible: root.alOn && !root.peeking && !root.bootShown
        source: alPlayer.source
        z: 5
    }

    // The boot loader — the shared circular Spinner (Spinner.qml, the settings Test button's own
    // mark), centred in the island. Shown only while the daemon is warming up; no word beside it.
    Spinner {
        objectName: "bootSpinner"              // reached by name from overlay_check
        anchors.horizontalCenter: parent.horizontalCenter
        y: Math.round(root.baseH / 2 - height / 2)
        // bootShown = booting || latch: visible LIVE while booting (a value binding, so it shows on
        // the first frame) and held through the fade-out (fading with the pill via `opacity`).
        running: root.bootShown
        visible: root.bootShown
        tint: Theme.textPrimary
        opacity: root.entrance
        z: 5
    }

    Item {
        id: viewport
        // Hidden the INSTANT a peek opens, not cross-faded over the 200ms grow — otherwise the
        // compact reply lingers behind the (transparent) peek panel as a ghost during the transition.
        opacity: root.peeking ? 0 : 1
        x: root.islandX + root.flare + root.leftInset
        y: 0
        width: Math.max(0, root.animW - 2 * root.flare - root.leftInset - root.padSide)
        height: Math.max(0, root.animH - root.padBottom)
        clip: true
        visible: root.open

        Text {
            id: textItem
            objectName: "body"              // the reveal/scroll self-check reaches it by name
            width: root.textW               // FINAL width, never the animating one
            // Top-anchored at a fixed offset and scrolled by whole lines. Deliberately does NOT
            // read the island height: that dependency is what made the text jump while the
            // height animated. The island grows downward around it instead.
            y: root.padTop - root.scrolled * root.lineBox
            Behavior on y { NumberAnimation { duration: root.scrollMs; easing.type: Easing.OutCubic } }
            wrapMode: Text.WordWrap
            color: root.isError ? Theme.textMuted : Theme.textPrimary
            font.family: fontFamily             // resolved in __main__.py, not guessed here
            font.pixelSize: Theme.fontSize
            font.weight: Theme.fontWeight
            lineHeight: root.lineBox
            lineHeightMode: Text.FixedHeight    // pixels, not a multiple of natural height
            // EVERYTHING reveals here, prompt and reply alike — never raw. Model deltas arrive as
            // a few fat chunks, so leaning on them to pace the text renders it as a block.
            text: root.bodyText.substring(0, reveal.shown)
        }

        // The morphing status word occupies the SAME slot the prompt will land in — same left
        // edge, same baseline — so when the transcript arrives it simply replaces the word.
        Text {
            id: statusWord
            objectName: "statusWord"            // reached by name from the self-check
            visible: root.loaderOn || root.dictWord !== ""
            x: 0
            y: root.padTop
            text: root.dictWord !== "" ? root.dictWord
                                       : (sweep.shown ? sweep.shown + "…" : "")
            color: Theme.textMuted                  // a status word, not content
            font.family: fontFamily
            font.pixelSize: Theme.fontSize
            font.weight: Theme.fontWeight
            lineHeight: root.lineBox
            lineHeightMode: Text.FixedHeight
        }

        // Dictation's paste confirmation earns a real ✓ from the icon font — the island's body
        // face has no U+2713 (it renders as tofu). Set just after the "Pasted" word, shown only
        // for the `pasted` beat.
        Text {
            id: pasteMark
            objectName: "pasteMark"
            visible: root.pasted
            text: Theme.ico.check
            x: statusWord.x + statusWord.contentWidth + 7
            y: root.padTop
            color: Theme.textMuted
            font.family: Theme.fontIcon
            font.pixelSize: Math.round(Theme.fontSize * Theme.iconInk)
            lineHeight: root.lineBox
            lineHeightMode: Text.FixedHeight
        }

        // "there is more above": the tail of the scrolled-off line shows through and dissolves
        // upward. Deliberately light and gradual — it reaches transparency by `fadeH`, which is
        // derived to stop just short of the first line's ink, so nothing legible is dimmed.
        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            height: root.fadeH
            visible: root.scrolled > 0
            gradient: Gradient {                    // curve is local; colour + peak are tokens
                GradientStop { position: 0.00; color: Theme.scrim(1.00) }
                GradientStop { position: 0.45; color: Theme.scrim(0.59) }
                GradientStop { position: 0.75; color: Theme.scrim(0.25) }
                GradientStop { position: 1.00; color: Theme.scrim(0.00) }
            }
        }
    }

    // ---- latency readout: the first-live-run instrument ----
    // status.json calls this "not user-facing chrome by default", so it is off unless asked
    // for (--latency, or the tray toggle). Targets come from shared/schemas/targets.json via the
    // `targets` context property — no longer hardcoded here, so a number cannot be quoted
    // two ways. A reading past a GATE renders at full strength so a miss is obvious at a glance.
    readonly property int fbTarget: targets.feedback.ms
    readonly property int fwTarget: targets.first_word.ms
    // first_word is 'measured', not a gate: under generate-then-play it is a reply-length
    // proxy, so the readout must show it neutrally and NEVER flag it red. If targets.json ever
    // reclassifies it to a gate, this expression starts colouring it — the reclassification is
    // data, not something baked into the renderer.
    readonly property bool fwIsGate: targets.first_word.kind !== "measured"

    FontMetrics { id: latencyFm; font: latencyText.font }

    Text {
        id: latencyText
        objectName: "latency"           // reached by name from the self-check
        visible: overlay.showLatency && (overlay.feedbackMs > 0 || overlay.firstWordMs > 0) && !root.peeking
        x: root.islandX + root.animW - root.flare - root.padSide - width
        y: root.padTop
        color: (overlay.feedbackMs > root.fbTarget
                || (root.fwIsGate && overlay.firstWordMs > root.fwTarget))
               ? Theme.textPrimary : Theme.textMuted
        font.family: fontFamily
        font.pixelSize: 11
        text: (overlay.feedbackMs > 0 ? "fb " + Math.round(overlay.feedbackMs) + "ms" : "")
              + (overlay.feedbackMs > 0 && overlay.firstWordMs > 0 ? "   " : "")
              + (overlay.firstWordMs > 0 ? "word " + Math.round(overlay.firstWordMs) + "ms" : "")
    }

    // The typewriter, for prompt AND reply. Two cases have to be told apart:
    //   GROWS  — a reply delta appends to what is already there: keep typing from where we are.
    //   CHANGES — a new prompt, or the reply replacing the prompt: start over from zero.
    // A prefix test distinguishes them without the model having to say which happened.
    property string revealedFrom: ""
    onBodyTextChanged: {
        var grew = bodyText.length >= revealedFrom.length
                   && bodyText.substring(0, revealedFrom.length) === revealedFrom;
        if (!grew)
            reveal.shown = 0;
        revealedFrom = bodyText;
    }

    Timer {
        id: reveal
        property int shown: 0
        interval: Theme.durationWord
        repeat: true
        running: shown < root.bodyText.length
        onTriggered: {
            // Hold the word back until the island has FINISHED moving — BOTH the height and the
            // scroll. `measure` already counts the pending word, so islandH and the scroll offset
            // are the targets. Gating only growth let words land mid-scroll past three lines.
            var targetY = root.padTop - root.scrolled * root.lineBox;
            if (Math.abs(root.animH - root.islandH) > 0.5
                    || Math.abs(textItem.y - targetY) > 0.5)
                return;
            reveal.shown = root.pendingEnd;
        }
    }

    // ---- expanded view ----
    // The island's one input surface: hover a showing answer for the hint, click to peek. Enabled
    // only when NOT peeking; once peeked, PeekPanel (below, so on top) owns all interaction. The
    // native filter in __main__.py is what lets these events reach the window at all — and only
    // over the silhouette (the frame stays click-through), gated on `peekable`.
    MouseArea {
        id: islandMouse
        x: root.islandX; y: 0; width: root.animW; height: root.animH
        enabled: !root.peeking
        hoverEnabled: root.peekable && !root.peeking
        cursorShape: (root.peekable && !root.peeking) ? Qt.PointingHandCursor : Qt.ArrowCursor
        onEntered: root.hovering = true
        onExited: root.hovering = false
        onClicked: if (root.peekable && !root.peeking) root.peeking = true
    }

    PeekPanel {
        id: peekPanel
        objectName: "peekPanel"
        x: root.islandX + root.flare
        y: 0
        width: Math.max(0, root.animW - 2 * root.flare)
        height: root.animH
        bodyHeight: root.islandH                     // final peek height; content lays out to this
        textWidth: root.peekW - 2 * 24               // fixed reading width (peekW minus side padding)
        faceFamily: fontFamily                       // context prop -> panel (non-shadowing name)
        prompt: root.prompt
        reply: overlay.reply
        generating: overlay.reply !== "" && !overlay.done   // mid-stream peek: more still coming
        model: overlay.model                                 // the model + tokens for the footer
        tokens: overlay.tokens
        visible: root.peeking || root.peekFade > 0.01
        opacity: root.peekFade
        onCopyRequested: root.copyRequested(overlay.reply)
        onSaveRequested: root.saveRequested(overlay.reply)
    }

}
