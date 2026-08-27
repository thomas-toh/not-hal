// A general circular loader — a three-quarter ring, turning. Tokenised out of SettingsWindow's
// inline component (2026-08-02) so the settings sheets (the Test button) and the island's
// boot indicator are the SAME mark by construction rather than two that happen to match.
//
// Reserves its 16×16 box whether or not it is running, so starting a probe never shifts the row it
// sits in. `reducedMotion` (a context property, set by the host) stops the rotation dead.
import QtQuick
import QtQuick.Shapes
import frontend

Item {
    id: sp
    property bool running: false
    property color tint: Theme.uiTextDim
    implicitWidth: 16
    implicitHeight: 16
    opacity: running ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: reducedMotion ? 0 : Theme.durationControl } }
    Shape {
        id: ring
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer
        transformOrigin: Item.Center
        ShapePath {
            strokeColor: sp.tint
            strokeWidth: 2
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            PathAngleArc {
                centerX: 8; centerY: 8; radiusX: 6; radiusY: 6
                startAngle: -90; sweepAngle: 270
            }
        }
        NumberAnimation on rotation {
            running: sp.running && !reducedMotion
            from: 0; to: 360
            duration: 900
            loops: Animation.Infinite
        }
    }
}
