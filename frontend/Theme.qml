pragma Singleton
import QtQuick

// Design tokens — the single source of colour, opacity and type for every Teleprompter
// surface (the island today; the expanded view later). A design-token sheet: values live
// HERE and are referenced BY ROLE, never restated as
// a literal at the point of use. A `pragma Singleton` is used rather than a plain component
// so there is exactly one instance and the values cannot drift between files.
//
// Island *geometry* deliberately stays in Overlay.qml — widths, the flare and the corner radii
// describe that one shape, not the design system.
QtObject {
    // ── palette ─────────────────────────────────────────────────────────────
    readonly property color surface: "#000000"       // the island body
    readonly property color inkBase: "#f4f6f8"       // white, before any opacity is applied
    readonly property color inkOk:   "#7ee0a0"       // an action that just succeeded (the peek's copied ✓)

    // ── opacity scale, named by ROLE not by value ──────────────────────────
    // Two levels, not three: the status word and fault text sit at the same recessive weight,
    // so they share one token rather than two that happen to match. Split them again only if
    // they ever need to differ.
    readonly property real opacityPrimary: 1.00      // content: the prompt, the reply
    readonly property real opacityMuted:   0.35      // status word, fault text, hints
    readonly property real opacityScrim:   0.88      // peak of the "more above" fade

    // ── derived text colours — use these, never an ad-hoc Qt.rgba() ────────
    readonly property color textPrimary: Qt.rgba(inkBase.r, inkBase.g, inkBase.b, opacityPrimary)
    readonly property color textMuted:   Qt.rgba(inkBase.r, inkBase.g, inkBase.b, opacityMuted)

    // ── type ────────────────────────────────────────────────────────────────
    // CSS semantics: the line box is lineHeight * fontSize (Overlay applies it via
    // Text.FixedHeight). Keep lineHeight above ~1.2 or descenders start to clip.
    readonly property int  fontSize: 18
    readonly property int  fontSizePrompt: 16        // peek prompt (context) — one step under the reply
    readonly property int  fontSizeSmall: 12         // quiet controls: peek more/less toggle, generating cue
    // 600, not the mockup's 500: a grotesque UI face at 500 reads too light against the black
    // island, and Qt renders a touch thinner than a browser. Inter (the app face again since
    // 2026-07-31; Inter → Hanken → Archivo → Inter) is variable, so 550 also works if 600 is a
    // shade heavy.
    readonly property int  fontWeight: Font.DemiBold // 600
    readonly property real lineHeight: 1.3
    readonly property real lineHeightTight: 1.15     // wrapped quiet context (peek prompt)

    // A point on the scrim ramp: f is the fraction of full strength (1.0 = opaquest). Lets a
    // gradient keep its own CURVE while the colour and peak strength stay tokenised.
    function scrim(f) { return Qt.rgba(surface.r, surface.g, surface.b, opacityScrim * f) }

    // ── motion ──────────────────────────────────────────────────────────────
    readonly property int durationResize: 340        // pill open/close
    readonly property int durationScroll: 200        // teleprompter glide
    readonly property int durationBars: 90           // mic bar smoothing
    readonly property int durationFade: 220          // the island's entrance / exit
    readonly property int durationPeek: 200          // expanded-view (peek) open/close — snappier than a turn resize
    readonly property int durationHint: 120          // hover-hint nudge
    // One WORD per tick, not one character: this is a teleprompter to be read, not a chat
    // stream to be skimmed. Matched to the scripted feed's cadence, which read well.
    readonly property int durationWord: 90

    // ── dwells: how long finished content stays put ─────────────────────────
    // Both start from the moment the text has FINISHED revealing, which is why they can be
    // flat numbers. Their predecessors lived in the daemon and had to scale with word count,
    // because the daemon was estimating this side's typing speed — it guessed 0.45 s a word
    // and still blanked long answers mid-sentence. Measured from the right clock, a constant
    // is enough, and "N seconds after it finishes appearing" is a knob you can reason about.
    //
    // The ANSWER dwell is two numbers, not one, and both are the user's to set
    // (General > Preferences). What is here are the FALLBACKS — used if the setting cannot be
    // read, and they are the shipped defaults, so a normal run and a fallback run look the same.
    // The daemon says which of the two a turn wants ("quick" once it has acted, "slow" once it
    // has answered); it never names a duration, because the seconds belong to the user.
    readonly property int durationPromptHold: 700     // prompt sits before the reply takes over
    readonly property int durationAnswerDwell: 20000  // an ANSWER sits before the island hides
    readonly property int durationActionDwell: 2500   // ...a CONFIRMATION, which has nothing to read
    readonly property int durationPasteDwell: 2500    // dictation's "Pasted ✓" beat before hiding

    // ── settings window ─────────────────────────────────────────────────────
    // A second surface, not a second design system: the face and motion above are shared. Every
    // fill refers to a ROLE, so a palette change is this block alone.
    //
    // The field is WARM, reversing the cool-neutral set of 2026-07-26. That decision was
    // taken against an olive that read as warm espresso; the brief has since changed to "read as
    // part of the system, not as a custom-designed app", and the greys it is measured against are
    // warm (R ≥ G ≥ B on every step). Cool-neutral is what made the old window read as designed.
    readonly property color surfaceShell: "#262624"   // window body
    readonly property color surfaceRail:  "#1f1e1d"   // the sidebar — a step below the body
    readonly property color surfaceCard:  "#2e2d2b"
    readonly property color surfaceLift:  "#32312e"   // hover, inset fields, segmented track
    readonly property color surfaceSunk:  "#1f1e1d"   // inset fields, a step below the shell
    readonly property color surfaceDeep:  "#141413"   // the page behind the window
    readonly property color surfacePop:   "#2e2d2b"   // dropdowns, sheets
    // Flat, not translucent: Qt reads an 8-digit hex as #AARRGGBB, so a stray alpha pair here is
    // a silently invisible line rather than an error. These are the composited values.
    readonly property color hairline:       "#393836"
    readonly property color hairlineStrong: "#4a4844"

    readonly property color bone: "#f5f4f2"           // the window's white (warmer than the island's)
    readonly property color uiInk:      bone                                    // strong fills
    readonly property color uiText:     bone
    readonly property color uiTextDim:  "#c6c7bb"                               // mist
    readonly property color uiTextFaint: "#7a7c6e"                             // ash
    readonly property color navText:       uiTextFaint
    readonly property color navTextActive: uiText

    readonly property color uiHover:       Qt.rgba(bone.r, bone.g, bone.b, 0.06)
    readonly property color uiHoverStrong: Qt.rgba(bone.r, bone.g, bone.b, 0.09)
    readonly property color uiNavHover:    Qt.rgba(bone.r, bone.g, bone.b, 0.04)
    readonly property color uiSelected:    Qt.rgba(bone.r, bone.g, bone.b, 0.075)
    // ── OPAQUE on purpose ────────────────────────────────────────────────────
    // These two are ANIMATED against opaque colours — the recorder's border goes hairline →
    // hover, the toggle's track goes track → accent. A ColorAnimation interpolates the alpha
    // channel too, so a translucent endpoint makes the control briefly SEE-THROUGH on the way,
    // which reads as a flash rather than a highlight. Same fault the Add-model button had.
    // These are the composited values (bone at 24% over surfaceSunk, at 10% over surfaceShell),
    // so they look identical at rest and no longer dip on the transition.
    // The rule, for anything added later: **both ends of an animated colour must be opaque.**
    // The rgba tokens above are only safe where they animate against `transparent` ON A DARKER
    // GROUND — because Qt's `transparent` is transparent BLACK, so fading it up to a fill dips
    // through dark grey. Over the island that is invisible; over a lifted track (the segmented
    // pickers) it is a visible dark flash, which is why those animate track → selected instead.
    readonly property color uiEdgeHover:   "#525150"
    readonly property color uiTrackOff:    "#3b3b39"

    // The UI accent is now WHITE (2026-07-26 — the lime read as sporty): toggles, the
    // primary model, focus, text selection all use `accent`. The secondary colours are firmed
    // down from the neon sandbox set into muted, editorial tones — coral for the mark + on-air,
    // berry for faults (so red never means "the mic can hear you"); teal is reserved, unused.
    readonly property color accent: bone              // white
    readonly property color flare:  "#cf6142"         // muted coral — mark, on-air
    readonly property color vapor:  "#40988c"         // muted teal (reserved)
    readonly property color pulse:  "#c2506f"         // muted berry — faults
    // `pulse` and `danger` are separate colours. `pulse` stays the FAULT colour, keeping the rule that red never
    // means "the mic can hear you"; `danger` becomes a real red for the two destructive
    // ACTIONS — the close button's hover and Remove — because a muted berry on a solid button
    // reads as disabled, which is the opposite of the signal those two need.
    readonly property color danger: "#d93b33"
    readonly property color lamp: flare               // the on-air lamp (name kept for callers)
    readonly property color lampSoft: Qt.rgba(0.81, 0.38, 0.26, 0.18)

    readonly property real opacityDim: 0.55          // a decided setting whose consumer is unbuilt
    readonly property int radiusCard: 12
    readonly property int radiusControl: 8
    // One height for every text button AND the three window controls. A token rather than
    // a consequence of padding, because "the same size" must not depend on two paddings agreeing
    // — which is exactly how they drifted apart when the button font changed.
    readonly property int controlHeight: 34
    readonly property int durationControl: 180       // switch travel, hover, menu open

    // Type scale for the settings window — THREE sizes, named by role (standardised 2026-07-25).
    // The island's sizes above are its own. Fewer steps than before on purpose: a window earns
    // hierarchy from weight and spacing, not from a size for every occasion, and the old
    // 28/17/16/15/14/13/12 ladder read as "a lot of small fonts". Now:
    //   heading 18 — every title and section heading (pane title, sheet title, group heading)
    //   base    16 — everything readable: labels, help text, controls, free text
    //   small   14 — the floor: chips, machine values, the effort scale, captions
    // Bold + sentence case carry the heading; there is no uppercase-eyebrow tier. QML wants
    // ints here — a fractional pixelSize is rejected outright.
    // Four sizes, a step smaller throughout, because the window is now measured
    // against the OS rather than against itself: 17 page title · 15 section · 14 body · 12 the
    // floor. A description takes the size of the label it belongs to — weight and
    // colour do the separating, not scale, which is why there is no size between 14 and 12.
    // These tokens are the settings window's ALONE; the island's sizes are its own, above.
    // Bumped a step on 2026-08-01: 14/12 measured correct against the HTML mockup on
    // paper — Qt's pixelSize and CSS's font-size agree on Inter — but side by side on the real
    // display the window read visibly smaller than the page. The screen is the authority.
    readonly property int fontTitle:   18
    readonly property int fontHeading: 16
    readonly property int fontBase:    16
    readonly property int fontSmall:   14
    // Coded labels and machine values (model ids, SEC.01, keycaps) — Martian Mono, bundled in
    // frontend/fonts and registered at startup (falls back silently if the file is missing).
    readonly property string fontMono: "Martian Mono"
    // Instrument Serif is bundled and registered too, but DEPLOYED NOWHERE yet — reserved for a
    // serif accent, pending an explicit decision. Do not reference this token until then.
    readonly property string fontSerif: "Instrument Serif"

    // Model editor card — a denser scale than the window's 18/16/14 (a card packs many controls
    // into one tile). Named by role so the sizes live here, not scattered as literals.
    readonly property int fontCardName:  22   // the provider name
    readonly property int fontCardLabel: 13   // Model / Effort / Extended thinking / Notes
    readonly property int fontCardMeta:  12   // mono model ids, the key-status footer

    // Icons — Lucide (bundled in frontend/fonts, the full family). Drawn as font text, never as
    // SVG paths, so an icon is one codepoint here and nothing else. Lucide is a STATIC font: its
    // stroke is fixed at 2px on a 24 grid, so there are no wght/opsz axes to feed and no
    // per-call-site weight — the size tokens below are the only knob.
    readonly property string fontIcon: "lucide"
    readonly property int iconSm: 16
    readonly property int iconMd: 19
    readonly property int iconLg: 24
    // Lucide draws to the edge of its 24 grid where Material Symbols sat inside a padded optical
    // box, so the same pixelSize came out about 18% bigger. The size tokens above stay the LAYOUT
    // box and the Glyph draws its text at this fraction of it, which keeps every existing
    // alignment while matching the old ink. Measured across the 26 icons that were swapped.
    readonly property real iconInk: 0.85

    // Every icon the app draws, by role rather than by Lucide's name, so a call site reads as
    // intent and swapping the picture is one edit here. Codepoints are Lucide's own mapping
    // (font/codepoints.json in lucide-static); the trailing comment is the Lucide icon name.
    readonly property QtObject ico: QtObject {
        readonly property string cloud:     "\uE088"   // cloud             — a provider-hosted model
        readonly property string chip:      "\uE0A9"   // cpu               — a local model
        readonly property string sparkle:   "\uE412"   // sparkles          — the Ask model
        readonly property string kebab:     "\uE0B7"   // ellipsis-vertical — a row's ⋯
        readonly property string check:     "\uE06C"   // check             — selected option, stored key
        readonly property string chevron:   "\uE06D"   // chevron-down      — a dropdown's indicator
        readonly property string plus:      "\uE13D"   // plus              — add a model
        readonly property string trash:     "\uE18E"   // trash-2           — remove a model
        readonly property string close:     "\uE1B2"   // x                 — dismiss a sheet / the window
        readonly property string minimize:  "\uE11C"   // minus             — window minimise
        readonly property string maximize:  "\uE167"   // square            — window maximise
        readonly property string restore:   "\uE09E"   // copy              — window restore
        readonly property string back:      "\uE06E"   // chevron-left      — step 2 → step 1
        readonly property string forward:   "\uE06F"   // chevron-right     — a catalogue row
        readonly property string edit:      "\uE172"   // square-pen        — configure a model
        readonly property string search:    "\uE151"   // search            — the sidebar field
        readonly property string mic:       "\uE118"   // mic               — the mic status mark
        readonly property string settings:  "\uE154"   // settings          — nav: General
        readonly property string plug:      "\uE37F"   // plug              — nav: Connectors
        readonly property string box:       "\uE061"   // box               — nav: Model selection
        readonly property string keyboard:  "\uE284"   // keyboard          — nav: Triggers
        readonly property string lines:     "\uE185"   // align-left        — nav: Dictation
        readonly property string info:      "\uE0F9"   // info              — nav: About (unbuilt)
        readonly property string sun:       "\uE178"   // sun               — theme: light
        readonly property string moon:      "\uE11E"   // moon              — theme: dark
        readonly property string monitor:   "\uE11D"   // monitor           — theme: system
        readonly property string folder:    "\uE247"   // folder-open       — connector: Files
        readonly property string mail:      "\uE10F"   // mail              — connector: Email
        readonly property string paste:     "\uE085"   // clipboard         — connector: Clipboard
        readonly property string globe:     "\uE0E8"   // globe             — connector: Web
        readonly property string apps:      "\uE0FF"   // layout-grid       — connector: Apps & media
        readonly property string hub:       "\uE0AD"   // database          — connector: MCP servers
        readonly property string copy:      "\uE09E"   // copy              — the peek's Copy action
        readonly property string save:      "\uE14D"   // save              — the peek's Save action
    }
    // Dropdown popup: show at most this many rows, then scroll (a fetched model list can be 100+).
    readonly property int dropdownRows: 8
    // Scrollbar thumb thickness — the one width every scrollbar shares (settings + the island peek),
    // via the ThemedScrollBar component (2026-07-27).
    readonly property int scrollThickness: 5

    // ── vertical rhythm ─────────────────────────────────────────────────────
    // ONE ratio for every wrapped string in the window, applied as a FIXED line box (the mode
    // Overlay.qml uses). Qt's proportional `lineHeight` multiplies the font's natural height
    // and inflates a SINGLE line too, which is what made a one-line description sit taller
    // than the gap above it — the label/description pair looked wrong at some widths and
    // right at others. A fixed box makes the same string occupy the same height everywhere.
    readonly property real lineHeightUi: 1.35
    readonly property int rowGap: 4                  // a label to its description
    // Group rhythm. ONE rule for the space above every heading and below it, so "Profile" and
    // "Preferences" cannot drift apart. The first heading in a pane takes no top gap — the
    // scroll area's own top clearance already gives it room.
    readonly property int groupGapTop: 34
    readonly property int groupGapBottom: 6
    function lineBox(px) { return Math.round(px * lineHeightUi) }
}
