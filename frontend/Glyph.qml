// Glyph.qml — one icon, drawn as one Lucide glyph. Shared by the settings window and the island's
// peek, which is why it is a file rather than an inline component: both surfaces draw from the same
// `Theme.ico` map, and an icon that differs between the two is a bug.
//
// `d` is the glyph char (a `Theme.ico.*`), `px` the box it is centred in. There is no weight knob:
// Lucide is a static font with a fixed 2px stroke, so the size tokens are the only dial.
import QtQuick
import frontend

Item {
    id: g
    property string d: ""
    property int px: Theme.iconMd
    property color tint: Theme.uiText
    implicitWidth: px
    implicitHeight: px
    Text {
        anchors.centerIn: parent
        text: g.d
        color: g.tint
        font.family: Theme.fontIcon
        // `px` is the LAYOUT box; the ink is drawn a notch smaller so Lucide's edge-to-edge grid
        // lands where Material Symbols' padded one used to (Theme.iconInk).
        font.pixelSize: Math.round(g.px * Theme.iconInk)
        renderType: Text.QtRendering
    }
}
