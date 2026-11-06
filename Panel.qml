import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui as Ui

Ui.Panel {
    id: root
    moduleName: "community.omacontrib"
    manageIpc: false
    property var anchorItem: null
    property var hostWidget: null

    function open() { root.controller.show() }
    function close() { root.controller.hide() }
    function openPr(url) {
        if (/^https:\/\/github\.com\/[^/]+\/[^/]+\/(pull|issues|discussions)\/\d+$/.test(url))
            Qt.openUrlExternally(url)
    }

    // Box UI tests (tests/check_box.py) set this to find controls by name instead of by pixel.
    readonly property bool reportLayout: Quickshell.env("OMACONTRIB_REPORT_LAYOUT") === "1"
    property string reportedLayout: ""
    function controlsIn(item, found) {
        if (!item || !item.visible) return found
        if (item.objectName && item.width > 0) {
            let at = item.mapToItem(null, 0, 0)
            let row = item.prData ? "@" + item.prData.number : ""
            found.push(item.objectName + row + "=" + [at.x, at.y, item.width, item.height].map(Math.round).join(","))
        }
        for (let child of item.children) controlsIn(child, found)
        return found
    }
    Timer {
        interval: 200; repeat: true; running: root.reportLayout && root.opened
        onTriggered: {
            let layout = root.controlsIn(content.Window.contentItem, []).join(" ")
            if (layout !== root.reportedLayout) console.info("omacontrib-layout " + (root.reportedLayout = layout))
        }
    }
    Process {
        id: copier
        property bool compact: false
        // Defer the fallback so normal exits can report their result first.
        onRunningChanged: if (!running) Qt.callLater(function() {
            if (!copier.running) content.copyFinished(false, copier.compact)
        })
        // qmllint disable signal-handler-parameters
        onExited: function(exitCode, exitStatus) {
            content.copyFinished(exitCode === 0 && exitStatus === 0, compact)
        }
        // qmllint enable signal-handler-parameters
    }
    Process {
        id: configurer
        property string action: ""
        stderr: StdioCollector { id: configErrors }
        // qmllint disable signal-handler-parameters
        onExited: function(exitCode) {
            content.configFinished(action, exitCode === 0, configErrors.text.trim())
            if (exitCode === 0 && root.hostWidget) root.hostWidget.refresh(true)
        }
        // qmllint enable signal-handler-parameters
    }
    Process { id: accountLogin }

    function configure(action, values) {
        if (configurer.running) { content.configFinished(action, false, "Another group change is still being applied."); return }
        let path = decodeURIComponent(Qt.resolvedUrl("bin/omacontrib.py").toString().replace(/^file:\/\//, ""))
        configurer.action = action
        configurer.command = ["python3", path, action].concat(Array.isArray(values) ? values : [values])
        configurer.running = true
    }

    Ui.KeyboardPanel {
        id: panel
        anchorItem: root.anchorItem
        owner: root.hostWidget || root
        bar: root.bar
        open: root.opened
        focusTarget: keys
        contentWidth: panel.fittedContentWidth(Style.space(580))
        contentHeight: panel.fittedContentHeight(Style.space(580))

        Ui.PanelKeyCatcher {
            id: keys
            blocked: content.editing
            Keys.forwardTo: [content]
            anchors.fill: parent
            onCloseRequested: root.close()
            onMoveRequested: function(dx, dy) { content.move(dx, dy) }
            onActivateRequested: content.activate()
            onTabRequested: function(direction) { content.move(0, direction) }
            onTextKey: function(text) { content.textKey(text) }
            PrContent {
                id: content
                objectName: "prContent"
                anchors.fill: parent
                snapshot: root.hostWidget ? root.hostWidget.snapshot : ({ prs: [], excluded: [], groups: [], accounts: [], availableAccounts: [] })
                loaded: root.hostWidget ? root.hostWidget.loaded : false
                busy: root.hostWidget ? root.hostWidget.busy : false
                onOpenRequested: function(url) { root.openPr(url) }
                onRefreshRequested: if (root.hostWidget) root.hostWidget.refresh(true)
                onConfigRequested: function(action, value) { root.configure(action, value) }
                onEditingFinished: keys.forceActiveFocus()
                onLoginRequested: {
                    accountLogin.command = ["omarchy", "launch", "terminal", "gh", "auth", "login", "--hostname", "github.com"]
                    accountLogin.running = true
                }
                onCopyRequested: function(text, compact) {
                    copier.compact = compact
                    copier.command = ["wl-copy", "--type", "text/plain", "--", text]
                    copier.running = true
                    Qt.callLater(function() {
                        if (!copier.running) content.copyFinished(false, compact)
                    })
                }
            }
        }
    }
}
