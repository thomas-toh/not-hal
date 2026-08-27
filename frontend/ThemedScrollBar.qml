// The app's scrollbar, in one place — a thin rounded thumb that fades to a light track, shared by
// the settings window and the island's peek so they scroll identically (Thomas, 2026-07-27). Was
// four near-identical inline copies; unified here, with a pointer cursor on hover added.
//
// Works vertical OR horizontal: attaching it as `ScrollBar.vertical`/`ScrollBar.horizontal` sets the
// orientation, and the thumb takes `Theme.scrollThickness` on whichever axis is the cross-axis
// (both implicit sizes are set; the control overrides the one along the scroll axis with the thumb
// length). `reducedMotion` is a context property both hosts set.
import QtQuick
import QtQuick.Controls.Basic
import frontend

ScrollBar {
    id: sb
    policy: ScrollBar.AsNeeded
    contentItem: Rectangle {
        implicitWidth: Theme.scrollThickness
        implicitHeight: Theme.scrollThickness
        radius: Theme.scrollThickness / 2
        color: sb.pressed ? Theme.uiTextDim
             : (sb.hovered ? Theme.uiTextFaint : Theme.uiTrackOff)
        opacity: sb.active || sb.hovered ? 1 : 0.5
        Behavior on color { enabled: !reducedMotion; ColorAnimation { duration: Theme.durationControl } }
        HoverHandler { cursorShape: Qt.PointingHandCursor }
    }
}
