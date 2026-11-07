pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Ui as Ui

Item {
    id: root
    property var snapshot: ({ prs: [], excluded: [], groups: [], accounts: [], availableAccounts: [] })
    property string pane: "inbox"
    property bool loaded: false
    property bool busy: false
    property string expandedId: ""
    property string selectedId: ""
    property point pointer: Qt.point(-1, -1)
    property bool copyBusy: false
    property string copyFeedback: ""
    property string pendingDropUrl: ""
    property bool addingGroup: false
    property bool creatingGroup: false
    property string groupError: ""
    property string pendingGroupName: ""
    property var menuGroup: null
    property bool confirmingDelete: false
    property string rowEditingId: ""
    readonly property bool editing: addingGroup || groupMenu.opened || !!rowEditingId
    readonly property color foreground: Color.popups.text
    readonly property color muted: Util.alpha(Color.popups.text, 0.68)
    readonly property string family: Style.font.family
    readonly property var allRows: (snapshot.prs || []).concat(snapshot.excluded || [])
    readonly property var visibleRows: allRows.filter(function(row) { return (row.group || "inbox") === pane })
    // Access/visibility buckets are filled automatically (see group_for in the helper); they are not
    // manual move/drop targets and cannot be renamed, deleted, or toggled. Keep in sync with AUTO_GROUP_SPECS.
    readonly property var autoGroupIds: ["private", "maintained"]
    function isAuto(id) { return root.autoGroupIds.indexOf(id) >= 0 }
    readonly property var currentGroup: (snapshot.groups || []).find(function(group) { return group.id === pane }) || ({ id: "inbox", name: "Active", count: true })
    signal openRequested(string url)
    signal refreshRequested()
    signal copyRequested(string text, bool compact)
    signal configRequested(string action, var values)
    signal loginRequested()
    signal editingFinished()

    function createNamedGroup(name) {
        let clean = name.trim()
        if (!clean) { groupError = "Enter a group name."; return }
        if (clean.length > 40) { groupError = "Group names can use at most 40 characters."; return }
        if ((snapshot.groups || []).some(function(group) { return group.name.toLowerCase() === clean.toLowerCase() })) {
            groupError = "That group already exists."
            return
        }
        groupError = ""
        creatingGroup = true
        pendingGroupName = clean
        if (pendingDropUrl) configRequested("--create-group-and-move", ["item", pendingDropUrl, clean])
        else configRequested("--create-group", clean)
    }

    function cancelGroupEditor() {
        addingGroup = false
        creatingGroup = false
        groupName.text = ""
        pendingDropUrl = ""
        pendingGroupName = ""
        groupError = ""
        editingFinished()
    }

    function configFinished(action, success, message) {
        if (action === "--ask-status" || action === "--open-session" || action === "--watch-status") {
            showToast(success ? (action === "--ask-status" ? "Agent is checking · w to watch" : action === "--watch-status" ? "Opened in a terminal" : "Session opened")
                : (message.split("\n").pop() || "Could not start the agent"))
            return
        }
        if (action === "--dismiss") {
            showToast(success ? "Dismissed" : (message.split("\n").pop() || "Could not dismiss it"))
            return
        }
        if (action !== "--create-group" && action !== "--create-group-and-move") {
            if (!success) showToast(message || "Could not update the group")
            return
        }
        creatingGroup = false
        if (!success) { groupError = message || "Could not create the group."; return }
        addingGroup = false
        groupName.text = ""
        pendingDropUrl = ""
        groupError = ""
        editingFinished()
    }

    function showToast(message) {
        copyFeedbackTimer.stop()
        copyFeedback = message
        copyFeedbackTimer.restart()
    }

    function textKey(text) {
        let key = text.toLowerCase()
        if (key === "r") refreshRequested()
        else if (key === "n" && (snapshot.groups || []).length < 20) {
            addingGroup = true
            groupError = ""
            Qt.callLater(function() { groupName.forceActiveFocus() })
        } else if (key === "m" && list.currentItem) list.currentItem.openMove()
        else if (key === "s" && list.currentItem) list.currentItem.askStatus()
        else if (key === "o" && list.currentItem) list.currentItem.openSession()
        else if (key === "w" && list.currentItem) list.currentItem.watchAgent()
        else if (key === "d" && list.currentItem) list.currentItem.dismiss()
        else if ((key === "[" || key === "]") && (snapshot.groups || []).length) {
            let groups = snapshot.groups
            let current = groups.findIndex(function(group) { return group.id === pane })
            pane = groups[(current + (key === "]" ? 1 : -1) + groups.length) % groups.length].id
        }
        else if (/^[1-9]$/.test(key)) {
            let index = Number(key) - 1
            if (snapshot.groups && snapshot.groups[index]) pane = snapshot.groups[index].id
        }
    }

    Keys.onPressed: function(event) {
        if (event.key === Qt.Key_C && !(event.modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier))) {
            if (!event.isAutoRepeat) copySelected(!!(event.modifiers & Qt.ShiftModifier))
            event.accepted = true
        } else if (event.key === Qt.Key_X && !(event.modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier))) {
            // x excludes the selected item (or restores it); shift+x its whole repository.
            if (!event.isAutoRepeat && visibleRows.length && list.currentItem) {
                let row = visibleRows[Math.max(0, list.currentIndex)]
                let target = list.currentItem.excludedGroup
                if (!(event.modifiers & Qt.ShiftModifier)) list.currentItem.exclude()
                else if (target) configRequested("--move-repository", [target.id, row.repository])
            }
            event.accepted = true
        }
    }
    function copySelected(compact) {
        if (copyBusy || !visibleRows.length) return
        let pr = visibleRows[Math.max(0, list.currentIndex)]
        if (!pr) return
        let text = compact ? pr.repository + "#" + pr.number : pr.url
        copyFeedbackTimer.stop()
        copyFeedback = ""
        copyBusy = true
        copyRequested(text, compact)
    }
    function copyFinished(success, compact) {
        if (!copyBusy) return
        copyBusy = false
        copyFeedback = success ? (compact ? "Reference copied" : "URL copied") : "Could not copy"
        copyFeedbackTimer.restart()
    }
    Timer {
        id: copyFeedbackTimer
        interval: 2500
        onTriggered: root.copyFeedback = ""
    }

    function move(dx, dy) {
        if (!list.count) return
        if (dy) {
            list.currentIndex = (Math.max(0, list.currentIndex) + dy + list.count) % list.count
            selectedId = visibleRows[list.currentIndex].id
            list.positionViewAtIndex(list.currentIndex, ListView.Contain)
        }
        if (dx) {
            let item = visibleRows[Math.max(0, list.currentIndex)]
            expandedId = dx > 0 && (item.kind || "pull_request") === "pull_request" ? item.id : ""
        }
    }
    function activate() {
        if (list.count) openRequested(visibleRows[Math.max(0, list.currentIndex)].url)
    }
    onPaneChanged: {
        if (addingGroup && !creatingGroup) cancelGroupEditor()
        selectedId = ""
        expandedId = ""
        Qt.callLater(function() { list.currentIndex = 0; list.positionViewAtBeginning() })
    }
    onSnapshotChanged: {
        let groups = snapshot.groups || []
        if (groups.length && !groups.some(function(group) { return group.id === pane })) pane = groups[0].id
        if (pendingGroupName) {
            let created = groups.find(function(group) { return group.name.toLowerCase() === pendingGroupName.toLowerCase() })
            if (created) { pane = created.id; pendingGroupName = "" }
        }
        let rows = (snapshot.prs || []).concat(snapshot.excluded || []).filter(function(row) { return (row.group || "inbox") === pane })
        let index = rows.findIndex(function(pr) { return pr.id === root.selectedId })
        Qt.callLater(function() {
            list.currentIndex = Math.max(0, index)
            list.forceLayout()
            list.positionViewAtIndex(list.currentIndex, list.currentIndex === 0 ? ListView.Beginning : ListView.Contain)
        })
    }

    Column {
        id: heading
        width: parent.width
        spacing: Style.space(5)
        Row {
            width: parent.width
            Text {
                width: parent.width - refresh.width
                text: "Contributions"
                textFormat: Text.PlainText
                font.family: root.family
                font.pixelSize: Style.font.title
                font.bold: true
                color: root.foreground
            }
            Text {
                id: refresh
                text: root.busy ? "Refreshing…" : "↻ Refresh"
                textFormat: Text.PlainText
                font.family: root.family
                font.pixelSize: Style.font.body
                color: Color.accent
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    enabled: !root.busy
                    onClicked: root.refreshRequested()
                }
            }
        }
        Text {
            width: parent.width
            text: root.snapshot.accounts && root.snapshot.accounts.length ? root.snapshot.accounts.map(function(account) { return "@" + account }).join(", ") : "Your PRs, issues and submissions · github.com"
            textFormat: Text.PlainText
            elide: Text.ElideRight
            font.family: root.family
            font.pixelSize: Style.font.body
            color: root.muted
        }
        Flow {
            width: parent.width
            spacing: Style.space(12)
            Repeater {
                model: root.snapshot.availableAccounts || []
                Text {
                    required property string modelData
                    readonly property bool enabledAccount: (root.snapshot.accounts || []).indexOf(modelData) >= 0
                    text: (enabledAccount ? "✓ " : "+ ") + "@" + modelData
                    textFormat: Text.PlainText
                    color: enabledAccount ? Color.accent : root.muted
                    font.family: root.family
                    font.pixelSize: Style.font.bodySmall
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: root.configRequested(parent.enabledAccount ? "--disable-account" : "--enable-account", parent.modelData)
                    }
                }
            }
            Text {
                text: "+ Add GitHub account"
                textFormat: Text.PlainText
                color: Color.accent
                font.family: root.family
                font.pixelSize: Style.font.bodySmall
                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.loginRequested() }
            }
        }
        Flow {
            width: parent.width
            spacing: Style.space(8)
            Repeater {
                model: root.snapshot.groups || []
                Rectangle {
                    id: groupTab
                    required property var modelData
                    objectName: "groupTab-" + modelData.id
                    width: Math.min(tabLabel.implicitWidth + countMark.implicitWidth + groupMenuButton.implicitWidth + Style.space(30), Style.space(165))
                    height: Style.space(28)
                    radius: Style.space(5)
                    color: groupDrop.containsDrag && groupTab.modelData.id !== root.pane ? Util.alpha(Color.accent, 0.18)
                        : root.pane === groupTab.modelData.id ? Util.alpha(Color.accent, 0.10) : "transparent"
                    DropArea {
                        id: groupDrop
                        anchors.fill: parent
                        onDropped: function(drop) {
                            if (drop.source && drop.source.prData) {
                                if (groupTab.modelData.id !== root.pane && !root.isAuto(groupTab.modelData.id))
                                    root.configRequested("--move-item", [groupTab.modelData.id, drop.source.prData.url])
                                drop.accept()
                            }
                        }
                    }
                    Row {
                        id: tabRow
                        anchors.centerIn: parent
                        width: Math.min(implicitWidth, groupTab.width - Style.space(10))
                        spacing: Style.space(6)
                        Text {
                            id: tabLabel
                            readonly property int itemCount: root.allRows.filter(function(row) { return (row.group || "inbox") === groupTab.modelData.id }).length
                            text: groupTab.modelData.name + "  " + itemCount
                            width: Math.min(implicitWidth, groupTab.width - countMark.width - groupMenuButton.width - Style.space(18))
                            elide: Text.ElideRight
                            color: root.pane === groupTab.modelData.id ? Color.accent : root.muted
                            font.family: root.family; font.pixelSize: Style.font.body; font.bold: root.pane === groupTab.modelData.id
                            MouseArea {
                                anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                acceptedButtons: Qt.LeftButton | Qt.RightButton
                                onClicked: function(mouse) {
                                    // Auto buckets have no rules to edit, so their context menu never opens.
                                    if (mouse.button === Qt.RightButton && !root.isAuto(groupTab.modelData.id)) {
                                        root.menuGroup = groupTab.modelData
                                        root.confirmingDelete = false
                                        groupMenu.open()
                                    } else root.pane = groupTab.modelData.id
                                }
                            }
                        }
                        Text {
                            id: countMark
                            visible: !groupTab.modelData.count
                            text: "⊘"
                            color: root.muted
                            font.family: root.family; font.pixelSize: Style.font.bodySmall
                            Ui.PanelToolTip { visible: countHover.hovered; text: "Not counted in the bar" }
                            HoverHandler { id: countHover }
                        }
                        Text {
                            id: groupMenuButton
                            visible: root.pane === groupTab.modelData.id && !root.isAuto(groupTab.modelData.id)
                            text: "⋯"
                            color: root.muted
                            font.family: root.family; font.pixelSize: Style.font.body
                            MouseArea {
                                anchors.fill: parent; cursorShape: Qt.PointingHandCursor; preventStealing: true
                                onClicked: {
                                    root.menuGroup = groupTab.modelData
                                    root.confirmingDelete = false
                                    groupMenu.open()
                                }
                            }
                        }
                    }
                }
            }
            Rectangle {
                id: addGroupTab
                objectName: "addGroupTab"
                width: root.addingGroup ? Style.space(155) : Style.space(28)
                height: Style.space(28)
                radius: Style.space(5)
                visible: (root.snapshot.groups || []).length < 20
                color: addGroupDrop.containsDrag ? Util.alpha(Color.accent, 0.18)
                    : addGroupHover.hovered || root.addingGroup ? Util.alpha(Color.popups.text, 0.05) : "transparent"
                DropArea {
                    id: addGroupDrop
                    anchors.fill: parent
                    onDropped: function(drop) {
                        if (drop.source && drop.source.prData) {
                            root.pendingDropUrl = drop.source.prData.url
                            root.addingGroup = true
                            root.groupError = ""
                            Qt.callLater(function() { groupName.forceActiveFocus() })
                            drop.accept()
                        }
                    }
                }
                Ui.TextField {
                    id: groupName
                    objectName: "groupName"
                    anchors.fill: parent
                    visible: root.addingGroup
                    placeholderText: root.pendingDropUrl ? "Name group for dropped item" : "New group"
                    maximumLength: 40
                    enabled: !root.creatingGroup
                    onAccepted: root.createNamedGroup(text)
                    Keys.onEscapePressed: root.cancelGroupEditor()
                }
                Text {
                    anchors.centerIn: parent
                    visible: !root.addingGroup
                    text: "+"
                    color: Color.accent
                    font.family: root.family; font.pixelSize: Style.font.title
                }
                HoverHandler { id: addGroupHover }
                TapHandler {
                    enabled: !root.addingGroup
                    onTapped: { root.addingGroup = true; root.groupError = ""; Qt.callLater(function() { groupName.forceActiveFocus() }) }
                }
            }
        }
        Text {
            width: parent.width
            visible: !!root.groupError
            text: root.groupError
            textFormat: Text.PlainText
            color: Color.urgent
            font.family: root.family
            font.pixelSize: Style.font.bodySmall
        }
        Text {
            width: parent.width
            visible: !!root.snapshot.error
            text: (root.snapshot.partial ? "Incomplete · " : "") + (root.snapshot.error || "")
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            font.family: root.family
            font.pixelSize: Style.font.bodySmall
            color: Color.urgent
        }
        Rectangle { width: parent.width; height: 1; color: root.muted; opacity: 0.25 }
    }

    ListView {
        id: list
        objectName: "prList"
        anchors.top: heading.bottom
        anchors.bottom: footerColumn.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.topMargin: Style.space(8)
        anchors.bottomMargin: Style.space(8)
        clip: true
        spacing: Style.space(6)
        model: root.visibleRows
        currentIndex: 0
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar {}
        WheelHandler {
            target: null
            acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
            onWheel: function(event) {
                // Boost smooth scrolling too: some mice send pixel deltas.
                // Use a larger wheel step while keeping small deltas proportional.
                let distance = event.pixelDelta.y !== 0
                    ? event.pixelDelta.y * (13 / 6)
                    : event.angleDelta.y / 120 * Style.space(260)
                let bottom = list.originY + Math.max(0, list.contentHeight - list.height)
                list.cancelFlick()
                list.contentY = Math.max(list.originY, Math.min(bottom, list.contentY - distance))
                event.accepted = true
            }
        }
        delegate: PrRow {
            required property var modelData
            required property int index
            width: list.width - Style.space(12)
            pr: modelData
            stale: !!root.snapshot.stale || !!root.snapshot.partial
            stackStart: !!modelData.stack && (index === 0
                || !root.visibleRows[index - 1].stack
                || root.visibleRows[index - 1].repository !== modelData.repository
                || root.visibleRows[index - 1].stack.number !== modelData.stack.number)
            stackEnd: !!modelData.stack && (index === root.visibleRows.length - 1
                || !root.visibleRows[index + 1].stack
                || root.visibleRows[index + 1].repository !== modelData.repository
                || root.visibleRows[index + 1].stack.number !== modelData.stack.number)
            selected: list.currentIndex === index
            expanded: root.expandedId === modelData.id
            groups: root.snapshot.groups || []
            currentGroup: root.pane
            onConfigRequested: function(action, values) { root.configRequested(action, values) }
            onEditorActiveChanged: root.rowEditingId = editorActive ? modelData.id : (root.rowEditingId === modelData.id ? "" : root.rowEditingId)
            onEditingFinished: root.editingFinished()
            onOpenRequested: { list.currentIndex = index; root.selectedId = modelData.id; root.openRequested(modelData.url) }
            onExpandRequested: {
                list.currentIndex = index
                root.selectedId = modelData.id
                root.expandedId = expanded ? "" : modelData.id
            }
            // Selection follows the pointer, but only when the pointer itself moves: rows
            // scrolling under a resting mouse during keyboard navigation keep the selection.
            HoverHandler {
                onPointChanged: {
                    let position = point.scenePosition
                    if (!hovered || (position.x === root.pointer.x && position.y === root.pointer.y)) return
                    root.pointer = position
                    if (list.currentIndex !== parent.index && !root.rowEditingId) {
                        list.currentIndex = parent.index
                        root.selectedId = parent.modelData.id
                    }
                }
            }
        }
        Text {
            anchors.centerIn: parent
            width: parent.width - Style.space(30)
            visible: list.count === 0
            text: !root.loaded ? "Loading your contributions…" : root.snapshot.error ? "Contributions are unavailable.\nUse Refresh to try again." : "No contributions in " + root.currentGroup.name + "."
            textFormat: Text.PlainText
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
            font.family: root.family
            font.pixelSize: Style.font.body
            color: root.muted
        }
    }
    Column {
        id: footerColumn
        anchors.bottom: parent.bottom
        width: parent.width
        spacing: Style.space(5)
        Text {
            width: parent.width
            text: root.snapshot.lastSuccessAt ? (root.snapshot.stale ? "Last complete update " : "Updated ") + Qt.formatDateTime(new Date(root.snapshot.lastSuccessAt * 1000), "MMM d, HH:mm:ss") : root.snapshot.fetchedAt ? "No complete update yet" : ""
            textFormat: Text.PlainText
            font.family: root.family
            font.pixelSize: Style.font.bodySmall
            color: root.muted
        }
        Text {
            width: parent.width
            text: "↑↓ navigate · enter open · s summarize · o discuss · m move · x exclude · n group · r refresh · esc close"
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            font.family: root.family
            font.pixelSize: Style.font.bodySmall
            color: root.muted
        }
    }
    Rectangle {
        id: copyToast
        objectName: "copyToast"
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: footerColumn.top
        anchors.bottomMargin: Style.space(12)
        width: Math.min(parent.width, toastText.implicitWidth + Style.space(28))
        height: toastText.implicitHeight + Style.space(18)
        radius: Style.space(8)
        color: Color.popups.background
        border.color: Color.accent
        opacity: root.copyFeedback ? 1 : 0
        visible: opacity > 0
        z: 1
        Behavior on opacity { NumberAnimation { duration: 150 } }
        Text {
            id: toastText
            anchors.centerIn: parent
            // Keep the last message while the toast fades out.
            property string message: ""
            Connections {
                target: root
                function onCopyFeedbackChanged() {
                    if (root.copyFeedback) toastText.message = root.copyFeedback
                }
            }
            text: message
            textFormat: Text.PlainText
            font.family: root.family
            font.pixelSize: Style.font.bodySmall
            color: root.foreground
        }
    }
    Popup {
        id: groupMenu
        objectName: "groupMenu"
        x: Math.max(0, root.width - width)
        y: Math.min(root.height - height, heading.height)
        width: Style.space(220)
        height: groupMenuContent.implicitHeight + topPadding + bottomPadding
        padding: Style.space(10)
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        onClosed: { root.confirmingDelete = false; root.editingFinished() }
        background: Rectangle {
            radius: Style.space(8)
            color: Color.popups.background
            border.width: 1
            border.color: Util.alpha(Color.accent, 0.5)
        }
        contentItem: Column {
            id: groupMenuContent
            spacing: Style.space(8)
            Text {
                width: parent.width
                text: root.menuGroup ? root.menuGroup.name : "Group"
                elide: Text.ElideRight
                color: root.foreground
                font.family: root.family; font.pixelSize: Style.font.body; font.bold: true
            }
            Rectangle { width: parent.width; height: 1; color: root.muted; opacity: 0.22 }
            Rectangle {
                objectName: "countGroupAction"
                width: parent.width; height: Style.space(30); radius: Style.space(4)
                color: countActionHover.hovered ? Util.alpha(Color.accent, 0.12) : "transparent"
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: (root.menuGroup && root.menuGroup.count ? "✓  " : "   ") + "Count in bar total"
                    color: root.foreground
                    font.family: root.family; font.pixelSize: Style.font.bodySmall
                }
                HoverHandler { id: countActionHover }
                TapHandler {
                    onTapped: {
                        if (!root.menuGroup) return
                        root.configRequested("--group-count", [root.menuGroup.id, root.menuGroup.count ? "false" : "true"])
                        groupMenu.close()
                    }
                }
            }
            Rectangle {
                objectName: "deleteGroupAction"
                visible: root.menuGroup && root.menuGroup.id !== "inbox" && !root.isAuto(root.menuGroup.id)
                width: parent.width; height: root.confirmingDelete ? Style.space(52) : Style.space(30); radius: Style.space(4)
                color: deleteHover.hovered ? Util.alpha(Color.urgent, 0.12) : "transparent"
                Text {
                    anchors.left: parent.left; anchors.top: parent.top
                    anchors.topMargin: root.confirmingDelete ? 0 : (parent.height - height) / 2
                    text: root.confirmingDelete ? "Move its items to Active and delete?" : "Delete group…"
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Color.urgent
                    font.family: root.family; font.pixelSize: Style.font.bodySmall
                }
                Row {
                    visible: root.confirmingDelete
                    anchors.right: parent.right; anchors.bottom: parent.bottom
                    spacing: Style.space(12)
                    Text {
                        text: "Cancel"; color: root.muted; font.family: root.family; font.pixelSize: Style.font.bodySmall
                        TapHandler { onTapped: root.confirmingDelete = false }
                    }
                    Text {
                        text: "Delete"; color: Color.urgent; font.family: root.family; font.pixelSize: Style.font.bodySmall; font.bold: true
                        TapHandler {
                            onTapped: {
                                root.configRequested("--delete-group", root.menuGroup.id)
                                groupMenu.close()
                            }
                        }
                    }
                }
                HoverHandler { id: deleteHover }
                MouseArea {
                    anchors.fill: parent
                    enabled: !root.confirmingDelete
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.confirmingDelete = true
                }
            }
        }
    }
}
