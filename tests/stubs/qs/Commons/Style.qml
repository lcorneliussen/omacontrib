pragma Singleton
import QtQuick
QtObject {
    function space(value) { return value }
    function hoverFillFor(foreground, accent) { return Qt.rgba(foreground.r, foreground.g, foreground.b, 0.08) }
    property int cornerRadius: 0
    property QtObject font: QtObject {
        property string family: "monospace"
        property int title: 20
        property int body: 14
        property int bodySmall: 12
    }
}
