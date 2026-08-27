// PeekPanel.qml: the expanded-view "peek" content. The current turn read in
// full: the prompt pinned and collapsible past two lines, the reply scrolling under a top/bottom
// fade, and Copy / Save actions. Overlay.qml grows the island to peek size and fades this in.
//
// Deliberately dumb: it renders `prompt`/`reply` and emits intent. The clipboard write and the
// save dialog belong to the host process (__main__.py) — a QML file has no business touching
// either, and keeping them out keeps this headless-testable. Mirrors sandbox/teleprompter-
// expanded-mockup.html.
import QtQuick
import QtQuick.Controls.Basic                    // ScrollBar (attached) for the shared ThemedScrollBar
import frontend                              // Theme — the design tokens

Item {
    id: panel
    clip: true                               // the island grows into the peek by REVEALING this
                                             // (content laid out to bodyHeight), never reflowing it

    property string prompt: ""
    property string reply: ""
    property bool generating: false          // the reply is still streaming (mid-stream peek)
    property string model: ""                // the model that produced the reply (footer)
    property int tokens: 0                    // the turn's total input+output tokens (footer)
    property string faceFamily: ""           // handed in by Overlay. DIFFERENT name from the
                                             // `fontFamily` context property on purpose: a property
                                             // named fontFamily makes `fontFamily: fontFamily`
                                             // self-bind to this empty local and load no font.
    // Reading width: the fixed peek content width minus side padding, handed in by Overlay. Fixed
    // (not derived from the animating height), so naturalHeight below has no circular dependency.
    property int textWidth: 512
    // The FINAL peek height (the island's target). The vertical layout pins to THIS, not the panel's
    // animating height, so nothing reflows during the grow — the clip above just reveals it.
    property int bodyHeight: 0

    signal copyRequested()
    signal saveRequested()

    // --- local geometry (this shape, not the design system — cf. Theme.qml's note) ---
    readonly property int padTop: 20
    readonly property int padSide: 24
    readonly property int padBottom: 16
    readonly property int promptGap: 2
    // Split top/bottom so the gap right after the prompt (top fade + its first-line clearance) can
    // be tight without shrinking the bottom fade. Each == the reply's matching top/bottom padding,
    // so the first/last line always clears its fade.
    readonly property int fadeTop: 10
    readonly property int fadeBottom: 18
    readonly property int actionsTop: 8
    readonly property int actionH: 36
    readonly property int cornerInset: 16         // icons equidistant from the right & bottom edges
    readonly property int scrollbarGutter: 8
    // The reply is a LITERAL replica of the non-peeked island reply (Overlay.qml's textItem): same
    // Theme.fontSize/fontWeight, same fixed line box. Don't invent values here — peeking must not
    // change the reply's type at all, only give it room.
    readonly property int replyLineBox: Math.round(Theme.fontSize * Theme.lineHeight)

    // A third, fainter ink for quiet controls (the more/less toggle, the scroll pill). Kept local
    // rather than a Theme token — Theme deliberately holds only two ink levels.
    readonly property color faintInk: Qt.rgba(Theme.inkBase.r, Theme.inkBase.g, Theme.inkBase.b, 0.26)

    // Overflow is the ACTUAL wrapped line count, not a pixelSize×lineHeight estimate: lineHeight
    // multiplies the font's NATURAL height, not the pixelSize, so the estimate under-counted and a
    // mere 2-line prompt tripped the "more" toggle. lineCount is exact.
    readonly property bool promptOverflows: promptMeasure.lineCount > 2
    property bool promptExpanded: false
    readonly property real promptPerLine: promptMeasure.lineCount > 0
        ? promptMeasure.contentHeight / promptMeasure.lineCount : promptMeasure.contentHeight
    readonly property int promptShownH: promptExpanded
        ? promptMeasure.contentHeight
        : Math.round(Math.min(promptMeasure.contentHeight, 2 * promptPerLine))
    readonly property int toggleH: promptOverflows ? 20 : 0

    // The height the panel WANTS; Overlay clamps it between a floor and a ceiling and animates to
    // it. Uses the clamped prompt height, so expanding the prompt shrinks the reply, never the pill.
    readonly property int naturalHeight:
        padTop + promptShownH + toggleH + promptGap
        + (replyMeasure.contentHeight + fadeTop + fadeBottom) + actionsTop + actionH + padBottom

    // A new turn re-collapses the prompt.
    onPromptChanged: promptExpanded = false

    // hidden measurers — never drawn; must lay out identically to the visible Text
    Text {
        id: promptMeasure
        visible: false; width: panel.textWidth; wrapMode: Text.WordWrap
        font: promptText.font; lineHeight: Theme.lineHeightTight; lineHeightMode: Text.ProportionalHeight
        text: panel.prompt
    }
    Text {
        id: replyMeasure
        visible: false; width: panel.textWidth - panel.scrollbarGutter; wrapMode: Text.WordWrap
        font: replyText.font; lineHeight: panel.replyLineBox; lineHeightMode: Text.FixedHeight
        text: panel.reply
    }

    // ---- prompt (pinned, muted context) ----
    Text {
        id: promptText
        objectName: "ppPrompt"
        x: panel.padSide; y: panel.padTop; width: panel.textWidth
        text: panel.prompt
        wrapMode: Text.WordWrap
        maximumLineCount: panel.promptExpanded ? 999 : 2
        elide: Text.ElideRight
        color: Theme.textMuted
        font.family: panel.faceFamily; font.pixelSize: Theme.fontSizePrompt; font.weight: Theme.fontWeight
        lineHeight: Theme.lineHeightTight; lineHeightMode: Text.ProportionalHeight
    }
    Text {
        id: promptToggle
        visible: panel.promptOverflows
        x: panel.padSide; y: promptText.y + promptText.height + 3
        text: panel.promptExpanded ? "less" : "more"
        color: hoverToggle.containsMouse ? Theme.textMuted : panel.faintInk
        font.family: panel.faceFamily; font.pixelSize: Theme.fontSizeSmall; font.weight: Font.Medium
        MouseArea {
            id: hoverToggle
            anchors.fill: parent; anchors.margins: -4
            hoverEnabled: true; cursorShape: Qt.PointingHandCursor
            onClicked: panel.promptExpanded = !panel.promptExpanded
        }
    }

    // ---- reply (scrolls; the whole turn's answer) ----
    Flickable {
        id: flick
        objectName: "peekReply"
        x: panel.padSide
        y: (panel.promptOverflows ? promptToggle.y + promptToggle.height : promptText.y + promptText.height) + panel.promptGap
        width: panel.textWidth
        // fills down to just above the actions row
        height: Math.max(0, actions.y - y - panel.actionsTop)
        clip: true
        contentHeight: replyText.height
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ThemedScrollBar {}        // the same scrollbar the settings window uses
        Text {
            id: replyText
            objectName: "ppReply"
            width: panel.textWidth - panel.scrollbarGutter
            topPadding: panel.fadeTop; bottomPadding: panel.fadeBottom   // clearances for the fades
            text: panel.reply
            wrapMode: Text.WordWrap
            color: Theme.textPrimary
            font.family: panel.faceFamily; font.pixelSize: Theme.fontSize; font.weight: Theme.fontWeight
            lineHeight: panel.replyLineBox; lineHeightMode: Text.FixedHeight
        }
    }
    // top/bottom fades — always on; the reply's top/bottom padding keeps the first/last line clear,
    // so only mid-scroll content ever dims (a truthful "more above/below", never popping).
    Rectangle {
        x: flick.x; y: flick.y; width: flick.width; height: panel.fadeTop
        gradient: Gradient {
            GradientStop { position: 0.0; color: Theme.surface }
            GradientStop { position: 1.0; color: "transparent" }
        }
    }
    Rectangle {
        x: flick.x; y: flick.y + flick.height - panel.fadeBottom; width: flick.width; height: panel.fadeBottom
        gradient: Gradient {
            GradientStop { position: 0.0; color: "transparent" }
            GradientStop { position: 1.0; color: Theme.surface }
        }
    }
    // ---- actions (Copy live, Save opens a save dialog — both host-handled) ----
    Row {
        id: actions
        spacing: 4
        anchors.right: parent.right; anchors.rightMargin: panel.cornerInset
        y: panel.bodyHeight - panel.cornerInset - height   // pinned to the FINAL bottom, not the clip

        property bool copied: false
        Timer { id: copiedReset; interval: 1200; onTriggered: actions.copied = false }

        // Copy
        Rectangle {
            width: panel.actionH; height: panel.actionH; radius: 9
            color: hoverCopy.containsMouse ? Qt.rgba(1, 1, 1, 0.08) : "transparent"
            Glyph {
                anchors.centerIn: parent
                px: 21
                d: actions.copied ? Theme.ico.check : Theme.ico.copy
                tint: actions.copied ? Theme.inkOk : Theme.inkBase
                opacity: actions.copied ? 1.0 : (hoverCopy.containsMouse ? 1.0 : 0.42)
            }
            MouseArea {
                id: hoverCopy
                anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                onClicked: { panel.copyRequested(); actions.copied = true; copiedReset.restart(); }
            }
        }
        // Save (opens a save dialog in the host)
        Rectangle {
            width: panel.actionH; height: panel.actionH; radius: 9
            color: hoverSave.containsMouse ? Qt.rgba(1, 1, 1, 0.08) : "transparent"
            Glyph {
                anchors.centerIn: parent
                px: 21
                d: Theme.ico.save
                tint: Theme.inkBase
                opacity: hoverSave.containsMouse ? 1.0 : 0.42
            }
            MouseArea {
                id: hoverSave
                anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                onClicked: panel.saveRequested()
            }
        }
    }

    // "still generating" cue — mid-stream peek is allowed, so tell the reader more is coming
    // (a copy right now would be partial). Sits bottom-left beside the actions, pulsing softly.
    Text {
        id: generatingCue
        visible: panel.generating
        anchors.left: parent.left; anchors.leftMargin: panel.padSide
        anchors.verticalCenter: actions.verticalCenter
        text: "generating…"
        color: Theme.textMuted
        font.family: panel.faceFamily; font.pixelSize: Theme.fontSizeSmall
        SequentialAnimation on opacity {
            running: panel.generating
            loops: Animation.Infinite
            NumberAnimation { from: 0.45; to: 0.95; duration: 700; easing.type: Easing.InOutSine }
            NumberAnimation { from: 0.95; to: 0.45; duration: 700; easing.type: Easing.InOutSine }
        }
    }

    // The model that produced the reply + the turn's token count: a quiet mono line
    // bottom-left, opposite Copy/Save, once the reply has settled (not while it is still streaming).
    Text {
        id: modelLine
        objectName: "modelLine"
        visible: !panel.generating && panel.model !== ""
        anchors.left: parent.left; anchors.leftMargin: panel.padSide
        anchors.verticalCenter: actions.verticalCenter
        text: panel.model + (panel.tokens > 0 ? "  •  " + panel.tokens + " tokens" : "")
        color: Theme.textMuted
        font.family: Theme.fontMono; font.pixelSize: Theme.fontSizeSmall
    }
}
