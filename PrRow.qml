pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Ui as Ui

Item {
    id: root
    required property var pr
    property bool stackStart: false
    property bool stackEnd: false
    readonly property real indent: pr.stack ? Style.space(18) : 0
    readonly property real headingHeight: stackStart ? Style.space(32) : 0
    property bool selected: false
    property bool expanded: false
    property bool stale: false
    property var groups: []
    property string currentGroup: "inbox"
    property string moveScope: "item"
    property bool addingDestination: false
    readonly property bool editorActive: assignmentPopup.opened
    readonly property string kind: pr.kind || "pull_request"
    readonly property string lifecycleStatus: pr.status || ""
    readonly property bool lifecycleOnly: kind === "merged" || kind === "closed" || kind === "tracked"
    readonly property var landing: pr.landing || null
    readonly property bool closedRow: kind === "closed" || (kind === "issue" && !!pr.closed)
    // Followed items keep their status line and sessions on the landing; everything else on a follow-up.
    readonly property var context: landing || pr.followUp || null
    // Any contribution can be summarized or discussed; a marketplace submission has no GitHub thread.
    readonly property bool followsUp: kind !== "submission"
    // Plain open rows stay compact until there is something to show.
    readonly property bool followLines: followsUp && (!!landing || closedRow || !!followStatus || !!origin)
    readonly property var followStatus: context ? context.status || null : null
    readonly property var origin: context && context.sessions && context.sessions.length ? context.sessions[context.sessions.length - 1] : null
    readonly property bool checking: !!followStatus && followStatus.by === "checking"
    readonly property bool mergeReady: !stale && !pr.error && !pr.draft
        && kind === "pull_request"
        && (pr.review === "Approved" || pr.review === "No review decision")
        && pr.mergeable === "MERGEABLE" && pr.mergeStateStatus === "CLEAN"
        && !!pr.counts && pr.counts.success > 0
        && pr.counts.running === 0 && pr.counts.failed === 0 && pr.counts.unknown === 0
        && !!pr.checks && pr.checks.every(function(check) {
            return check.bucket === "success" || check.status === "SKIPPED"
                || check.status === "NEUTRAL" || check.status === "CANCELLED"
        })
    signal openRequested()
    signal expandRequested()
    signal configRequested(string action, var values)
    signal editingFinished()
    readonly property color foreground: Color.popups.text
    readonly property color muted: Util.alpha(Color.popups.text, 0.68)
    readonly property string family: Style.font.family
    readonly property bool lightSurface: (Color.popups.background.r * 0.299 + Color.popups.background.g * 0.587 + Color.popups.background.b * 0.114) > 0.6
    readonly property var colors: ({ running: lightSurface ? "#926000" : "#d5a44c", success: lightSurface ? "#337745" : "#78ad79", skipped: Color.muted, failed: Color.urgent, unknown: Color.muted })
    readonly property bool needsAction: lifecycleStatus === "Needs fixes" || pr.review === "Changes requested"
    readonly property bool completed: lifecycleStatus === "Listed" || lifecycleStatus === "Approved" || lifecycleStatus === "Landed" || lifecycleStatus === "Closed · completed"
        || lifecycleStatus.indexOf("Released in ") === 0 || lifecycleStatus.indexOf("Shipped in ") === 0
    readonly property int total: pr.checks ? pr.checks.length : 0
    readonly property string summary: !pr.counts ? "Unknown" : total === 0 ? "No checks" : ["running", "success", "skipped", "failed", "unknown"].filter(function(key) { return pr.counts[key] > 0 }).map(function(key) { return pr.counts[key] + " " + key }).join("  ·  ")

    function ago(iso) {
        let minutes = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 60000))
        return minutes < 60 ? minutes + "m ago" : minutes < 2880 ? Math.round(minutes / 60) + "h ago" : Math.round(minutes / 1440) + "d ago"
    }
    function askStatus() { if (followsUp && !checking) configRequested("--ask-status", pr.url) }
    function openSession() { if (followsUp) configRequested("--open-session", pr.url) }
    // A run in progress, or the last run's full report: the status line is only its last word.
    readonly property bool agentRan: checking || (!!followStatus && followStatus.by === "claude")
    function watchAgent() { if (followsUp && agentRan) configRequested("--watch-status", pr.url) }
    function dismiss() { if (closedRow) configRequested("--dismiss", pr.url) }
    // Excluding an already excluded item brings it back to Active.
    readonly property var excludedGroup: groups.find(function(group) { return group.id === "excluded" }) || groups[1] || null
    readonly property bool isExcluded: !!excludedGroup && currentGroup === excludedGroup.id
    function exclude() { if (excludedGroup) configRequested("--move-item", [isExcluded ? "inbox" : excludedGroup.id, pr.url]) }
    // A row action, styled like the shell's own panel actions: its hover fill, a little
    // brighter text on hover, and its themed tooltip.
    component RowAction: Rectangle {
        id: action
        property alias text: actionLabel.text
        property string tip: ""
        property color tint: Color.accent
        property bool active: true
        property real fontSize: Style.font.bodySmall
        property int elide: Text.ElideNone
        readonly property bool hovered: actionHover.hovered
        signal activated()
        implicitWidth: actionLabel.implicitWidth + Style.space(10)
        implicitHeight: actionLabel.implicitHeight + Style.space(4)
        radius: Math.max(Style.cornerRadius, Style.space(4))
        color: hovered && active ? Style.hoverFillFor(root.foreground, tint) : "transparent"
        Behavior on color { ColorAnimation { duration: 60 } }
        Text {
            id: actionLabel
            anchors.fill: parent; anchors.leftMargin: Style.space(5); anchors.rightMargin: Style.space(5)
            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
            textFormat: Text.PlainText; elide: action.elide
            font.family: root.family; font.pixelSize: action.fontSize
            color: action.hovered && action.active ? Qt.lighter(action.tint, 1.25) : action.tint
        }
        HoverHandler { id: actionHover; cursorShape: action.active ? Qt.PointingHandCursor : Qt.ArrowCursor }
        TapHandler { onTapped: if (action.active) action.activated() }
        Ui.PanelToolTip { visible: action.tip !== "" && actionHover.hovered; text: action.tip }
    }
    function openMove() { moveScope = "item"; addingDestination = false; assignmentPopup.open() }
    function scopeAction() { return moveScope === "repository" ? "--move-repository" : moveScope === "owner" ? "--move-owner" : "--move-item" }
    function scopeValue() { return moveScope === "repository" ? pr.repository : moveScope === "owner" ? pr.repository.split("/")[0] : pr.url }
    function moveTo(groupId) { configRequested(scopeAction(), [groupId, scopeValue()]); assignmentPopup.close() }
    function createAndMove(name) {
        let clean = name.trim()
        if (!clean) return
        configRequested("--create-group-and-move", [moveScope, scopeValue(), clean])
        destinationName.text = ""
        assignmentPopup.close()
    }

    height: body.implicitHeight + Style.space(16) + headingHeight

    Text {
        objectName: "stackHeading"; visible: root.stackStart; width: parent.width
        text: root.pr.stack ? "Stack #" + root.pr.stack.number + " · " + root.pr.repository : ""
        textFormat: Text.PlainText; elide: Text.ElideMiddle; color: Color.accent
        font.family: root.family; font.pixelSize: Style.font.bodySmall; font.bold: true
    }
    Rectangle {
        objectName: "stackRail"; visible: !!root.pr.stack; x: Style.space(4); y: root.headingHeight
        width: Style.space(2); height: parent.height - y + (root.stackEnd ? 0 : Style.space(10)); color: Util.alpha(Color.accent, 0.4)
    }
    Rectangle {
        objectName: "prCard"; x: root.indent; y: root.headingHeight; width: parent.width - x; height: parent.height - y
        radius: Style.space(8)
        color: root.mergeReady ? Util.alpha(root.colors.success, root.selected ? 0.09 : 0.045) : Util.alpha(Color.popups.text, root.selected ? 0.07 : 0.025)
        border.width: 1
        border.color: root.selected ? Util.alpha(Color.accent, 0.6) : Util.alpha(root.mergeReady ? root.colors.success : Color.muted, root.mergeReady ? 0.28 : 0.18)
    }
    Column {
        id: body
        x: root.indent + Style.space(8); y: root.headingHeight + Style.space(8)
        width: parent.width - root.indent - Style.space(16); spacing: Style.space(5)
        Row {
            width: parent.width; spacing: Style.space(8)
            Text {
                width: Math.max(Style.space(80), parent.width - excludeAction.width - moveAction.width - badge.width - Style.space(24))
                text: (root.pr.stack ? root.pr.stack.position + "/" + root.pr.stack.size + "  ·  " : "")
                    + (root.kind === "submission" ? "Omarchy Marketplace" : root.pr.repository) + "  #" + root.pr.number
                textFormat: Text.PlainText; elide: Text.ElideMiddle; color: root.muted
                font.family: root.family; font.pixelSize: Style.font.bodySmall
            }
            RowAction {
                id: excludeAction; objectName: "excludeAction"; visible: !!root.excludedGroup
                width: visible ? implicitWidth : 0; height: Math.max(implicitHeight, badge.height)
                text: root.isExcluded ? "↩" : "⊘"; fontSize: Style.font.body
                opacity: root.selected || hovered ? 1 : 0.45
                tip: root.isExcluded ? "Back to Active · x" : "Exclude this item · x   (shift+x: whole repository)"
                onActivated: root.exclude()
            }
            RowAction {
                id: moveAction; objectName: "moveAction"; readonly property var prData: root.pr
                height: Math.max(implicitHeight, badge.height)
                text: "⠿"; fontSize: Style.font.body
                opacity: root.selected || hovered || moveDrag.active || assignmentPopup.opened ? 1 : 0.45
                tip: moveDrag.active ? "" : "Move · m   (or drag onto a group)"
                onActivated: root.openMove()
                // An internal drag only reaches drop areas under the dragged item, so a
                // proxy follows the pointer; the row itself stays in the list.
                DragHandler {
                    id: moveDrag; target: null
                    onActiveChanged: if (active) dragProxy.Drag.start(); else dragProxy.Drag.drop()
                }
            }
            Rectangle {
                id: badge; objectName: "prStatusBadge"
                width: badgeText.implicitWidth + Style.space(14); height: badgeText.implicitHeight + Style.space(4); radius: height / 2
                color: root.needsAction ? Util.alpha(Color.urgent, 0.18) : root.mergeReady ? root.colors.success : Util.alpha(root.completed ? root.colors.success : Color.accent, 0.15)
                Text {
                    id: badgeText; objectName: "prStatusText"; anchors.centerIn: parent
                    text: root.kind !== "pull_request" ? root.lifecycleStatus : root.mergeReady ? "✓ Ready to merge" : root.pr.draft ? "Draft" : "Open"
                    textFormat: Text.PlainText
                    color: root.needsAction ? Color.urgent : root.mergeReady ? Color.popups.background : root.pr.draft ? root.muted : root.completed ? root.colors.success : Color.accent
                    font.family: root.family; font.pixelSize: Style.font.bodySmall; font.bold: root.mergeReady
                }
            }
        }
        Text {
            objectName: "prTitle"; width: parent.width; text: root.pr.title; textFormat: Text.PlainText
            color: root.foreground; font.family: root.family; font.pixelSize: Style.font.body; font.bold: true; wrapMode: Text.WordWrap
            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.openRequested() }
        }
        Text {
            // Read-only provenance: repo visibility and, for a PR opened from your fork, where its branch lives.
            objectName: "prProvenance"; width: parent.width; visible: text.length > 0
            text: [root.pr.visibility === "private" ? "Private" : "",
                   root.pr.access === "maintainer" ? "you maintain" : "",
                   root.pr.fork ? "from " + root.pr.fork.repo + (root.pr.fork.branch ? ":" + root.pr.fork.branch : "") : ""]
                  .filter(function(part) { return part.length > 0 }).join("   ·   ")
            textFormat: Text.PlainText; elide: Text.ElideMiddle
            color: root.muted; font.family: root.family; font.pixelSize: Style.font.bodySmall
        }
        Row {
            // On closed and followed-only rows the review line only repeated the badge.
            width: parent.width; spacing: Style.space(10); visible: !root.closedRow && root.kind !== "tracked"
            Text {
                width: Math.max(0, parent.width - activitySummary.width - Style.space(10))
                text: root.pr.review
                    + (!root.lifecycleOnly ? "  ·  " + (root.kind === "submission" || root.kind === "issue" ? "Comments " : "Discussion ") + (root.pr.discussion === null ? "—" : root.pr.discussion) : "")
                    + (root.kind === "pull_request" ? "  ·  Inline " + (root.pr.inline === null ? "—" : root.pr.inline) : "")
                textFormat: Text.PlainText; elide: Text.ElideRight
                font.family: root.family; font.pixelSize: Style.font.bodySmall
                color: root.pr.review === "Approved" ? root.colors.success : root.pr.review === "Changes requested" ? Color.urgent : root.muted
            }
            Text {
                id: activitySummary
                visible: root.kind === "pull_request"
                width: visible ? implicitWidth : 0
                text: root.summary + (root.total ? root.expanded ? "  ▴" : "  ▾" : "")
                textFormat: Text.PlainText
                font.family: root.family; font.pixelSize: Style.font.bodySmall; color: root.muted
                MouseArea { id: summaryHover; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.expandRequested() }
                Ui.PanelToolTip { visible: summaryHover.containsMouse; text: root.summary + (root.total ? " · click to show checks" : "") }
            }
        }
        Rectangle {
            visible: root.kind === "pull_request" && root.total > 0
            width: parent.width; height: Style.space(3); radius: height / 2; color: Util.alpha(Color.muted, 0.2)
            Row {
                anchors.fill: parent
                Repeater {
                    model: ["running", "success", "skipped", "failed", "unknown"]
                    Rectangle { required property string modelData; height: Style.space(3); width: root.total && root.pr.counts ? body.width * root.pr.counts[modelData] / root.total : 0; color: root.colors[modelData] }
                }
            }
            MouseArea { id: barHover; anchors.fill: parent; hoverEnabled: true; onClicked: root.expandRequested() }
            Ui.PanelToolTip { visible: barHover.containsMouse; text: root.summary }
        }
        Text {
            width: parent.width; visible: !!root.pr.error
            text: "Stale / unavailable · " + (root.pr.error || "") + (root.pr.fetchedAt ? " · Last updated " + Qt.formatDateTime(new Date(root.pr.fetchedAt * 1000), "HH:mm:ss") : "")
            textFormat: Text.PlainText; wrapMode: Text.WordWrap; font.family: root.family; font.pixelSize: Style.font.bodySmall; color: Color.urgent
        }
        Text {
            objectName: "landingSteps"; width: parent.width; visible: !!root.landing
            // The first step is this item itself, labelled only by its state.
            text: root.landing ? root.landing.steps.map(function(step, index) {
                return (step.done ? "✓ " : "○ ") + (index === 0 ? "#" + root.pr.number + " " + step.label[0].toLowerCase() + step.label.slice(1) : step.label)
            }).join("   →   ") : ""
            textFormat: Text.PlainText; wrapMode: Text.WordWrap; font.family: root.family; font.pixelSize: Style.font.bodySmall
            color: root.landing && root.landing.landed ? root.colors.success : root.muted
        }
        Text {
            objectName: "landingWorkaround"; width: parent.width; visible: !!root.landing && !!root.landing.workaround
            text: root.landing ? (root.landing.landed ? "You can now drop: " : "Workaround in place: ") + root.landing.workaround : ""
            textFormat: Text.PlainText; wrapMode: Text.WordWrap; font.family: root.family; font.pixelSize: Style.font.bodySmall
            font.bold: !!root.landing && root.landing.landed; color: root.landing && root.landing.landed ? root.colors.success : root.muted
        }
        Text {
            objectName: "landingNote"; width: parent.width; visible: !!root.landing && !!root.landing.note
            text: root.landing ? root.landing.note : ""
            textFormat: Text.PlainText; wrapMode: Text.WordWrap; font.family: root.family; font.pixelSize: Style.font.bodySmall; font.italic: true; color: root.muted
        }
        Text {
            objectName: "followStatus"; width: parent.width; visible: root.followLines
            readonly property bool yourMove: !!root.followStatus && !root.checking && root.followStatus.waiting === "me"
            text: root.checking ? "Agent is checking…  ·  " + (root.followStatus.activity ? "→ " + root.followStatus.activity : "was: " + root.followStatus.text)
                : root.followStatus ? (yourMove ? "Your move  ·  " : "") + root.followStatus.text + "  ·  " + root.followStatus.by + ", " + root.ago(root.followStatus.at)
                    + (root.followStatus.outdated ? "  ·  outdated" : "")
                : root.landing ? "No status yet" : "No summary yet"
            textFormat: Text.PlainText; wrapMode: Text.WordWrap; font.family: root.family; font.pixelSize: Style.font.bodySmall
            color: yourMove ? root.colors.running : root.followStatus && !root.checking && !root.followStatus.outdated ? root.foreground : root.muted
        }
        Row {
            // All follow-up actions share one line under the status; their hover padding hangs into the margin.
            objectName: "followActions"; x: -Style.space(5); width: parent.width + Style.space(5); spacing: Style.space(6); visible: root.followLines
            RowAction {
                id: openAction; objectName: "openAction"; text: "↗  open"; visible: root.closedRow
                tip: "Open on GitHub · enter"
                onActivated: root.openRequested()
            }
            RowAction {
                id: askAction; objectName: "askStatus"
                text: root.checking ? "checking…" : root.landing ? "s  ask agent" : "s  summarize"
                active: !root.checking; tint: root.checking ? root.muted : Color.accent
                tip: root.checking ? "" : root.landing ? "Refresh this status with a read-only agent in the background"
                    : "Find out where it stands with a read-only agent in the background"
                onActivated: root.askStatus()
            }
            RowAction {
                id: watchAction; objectName: "watchAction"; visible: root.agentRan
                text: root.checking ? "w  watch" : "w  report"
                tip: root.checking ? "Follow the agent live in a terminal" : "The last check's full report; continue that conversation from there"
                onActivated: root.watchAgent()
            }
            RowAction {
                objectName: "originSession"
                width: Math.max(0, Math.min(implicitWidth, parent.width - askAction.width - parent.spacing
                    - (watchAction.visible ? watchAction.width + parent.spacing : 0)
                    - (openAction.visible ? openAction.width + parent.spacing : 0)
                    - (dismissAction.visible ? dismissAction.width + parent.spacing : 0)))
                text: root.origin ? "o  resume " + root.origin.agent + " on " + root.origin.host + "  ·  " + root.origin.cwd
                    : root.landing ? "o  start agent session" : "o  discuss with an agent"
                elide: Text.ElideMiddle
                tip: root.origin ? "Resume the session this came from" : "Open an agent session about this item"
                onActivated: root.openSession()
            }
            RowAction {
                id: dismissAction; objectName: "dismissAction"; readonly property var prData: root.pr
                text: "d  dismiss"; visible: root.closedRow; tint: root.muted
                tip: "Hide it for good"
                onActivated: root.dismiss()
            }
        }
        Column {
            width: parent.width; spacing: Style.space(4); visible: root.expanded && root.kind === "pull_request"
            Repeater {
                model: root.expanded && root.pr.checks ? root.pr.checks : []
                Text { required property var modelData; width: body.width; text: "●  " + modelData.name + "  ·  " + modelData.status; textFormat: Text.PlainText; wrapMode: Text.WordWrap; font.family: root.family; font.pixelSize: Style.font.bodySmall; color: root.colors[modelData.bucket] }
            }
        }
    }

    Rectangle {
        id: dragProxy; objectName: "dragProxy"; property var prData: root.pr
        // Above the clipped list, so the proxy stays visible over the group tabs.
        parent: root.Window.contentItem || root
        visible: moveDrag.active; z: 100
        // Beside the pointer, not under it, so the group it is over stays readable; it drops at the pointer.
        x: moveDrag.centroid.scenePosition.x + Style.space(14); y: moveDrag.centroid.scenePosition.y + Style.space(10)
        width: proxyText.implicitWidth + Style.space(16); height: proxyText.implicitHeight + Style.space(8); radius: height / 2
        color: Color.popups.background; border.width: 1; border.color: Color.accent
        Drag.source: dragProxy; Drag.hotSpot.x: -Style.space(14); Drag.hotSpot.y: -Style.space(10)
        Text {
            id: proxyText; anchors.centerIn: parent
            text: "⠿  " + root.pr.repository + "#" + root.pr.number
            textFormat: Text.PlainText; color: Color.accent; font.family: root.family; font.pixelSize: Style.font.bodySmall
        }
    }

    Popup {
        id: assignmentPopup; objectName: "assignmentPopup"
        x: Math.max(root.indent, root.width - width - Style.space(12)); y: root.headingHeight + Style.space(34)
        width: Math.min(root.width - root.indent - Style.space(24), Style.space(330))
        height: assignmentMenu.implicitHeight + topPadding + bottomPadding; padding: Style.space(10)
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        onClosed: { root.addingDestination = false; destinationName.text = ""; root.editingFinished() }
        background: Rectangle { radius: Style.space(8); color: Color.popups.background; border.width: 1; border.color: Util.alpha(Color.accent, 0.55) }
        contentItem: Column {
            id: assignmentMenu; spacing: Style.space(8)
            Text { text: "Move…"; color: root.foreground; font.family: root.family; font.pixelSize: Style.font.body; font.bold: true }
            Row {
                spacing: Style.space(5)
                Repeater {
                    model: [{ id: "item", name: "This PR" }, { id: "repository", name: root.pr.repository }, { id: "owner", name: root.pr.repository.split("/")[0] }]
                    Rectangle {
                        id: scopeChoice; required property var modelData
                        width: Math.min(scopeText.implicitWidth + Style.space(12), assignmentPopup.availableWidth / 3 - Style.space(4)); height: Style.space(25); radius: Style.space(4)
                        color: root.moveScope === modelData.id ? Util.alpha(Color.accent, 0.18) : Util.alpha(root.foreground, 0.05)
                        Text { id: scopeText; anchors.centerIn: parent; width: parent.width - Style.space(8); text: scopeChoice.modelData.name; elide: Text.ElideMiddle; horizontalAlignment: Text.AlignHCenter; color: root.moveScope === scopeChoice.modelData.id ? Color.accent : root.muted; font.family: root.family; font.pixelSize: Style.font.bodySmall }
                        TapHandler { onTapped: root.moveScope = scopeChoice.modelData.id }
                    }
                }
            }
            Rectangle { width: parent.width; height: 1; color: root.muted; opacity: 0.22 }
            Repeater {
                // Ownership buckets (personal, maintained) fill automatically; not manual targets.
                model: root.groups.filter(function(group) { return ["personal", "maintained"].indexOf(group.id) < 0 })
                Rectangle {
                    id: destination; required property var modelData
                    objectName: "destination-" + modelData.id
                    width: assignmentPopup.availableWidth; height: Style.space(28); radius: Style.space(4)
                    color: destinationHover.hovered ? Util.alpha(Color.accent, 0.12) : "transparent"
                    Text { anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter; text: (destination.modelData.id === root.currentGroup ? "✓  " : "   ") + destination.modelData.name; color: destination.modelData.id === root.currentGroup ? Color.accent : root.foreground; font.family: root.family; font.pixelSize: Style.font.bodySmall }
                    HoverHandler { id: destinationHover }
                    TapHandler { onTapped: root.moveTo(destination.modelData.id) }
                }
            }
            Rectangle {
                width: assignmentPopup.availableWidth; height: root.addingDestination ? destinationName.implicitHeight : Style.space(28); radius: Style.space(4)
                color: newDestinationHover.hovered ? Util.alpha(Color.accent, 0.12) : "transparent"
                Text { anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter; visible: !root.addingDestination; text: "+  New group…"; color: Color.accent; font.family: root.family; font.pixelSize: Style.font.bodySmall }
                Ui.TextField {
                    id: destinationName; objectName: "destinationName"; anchors.fill: parent; visible: root.addingDestination
                    placeholderText: "Group name"; maximumLength: 40; verticalPadding: Style.space(3)
                    onAccepted: root.createAndMove(text)
                    Keys.onEscapePressed: { root.addingDestination = false; text = ""; assignmentPopup.forceActiveFocus() }
                }
                HoverHandler { id: newDestinationHover; enabled: !root.addingDestination }
                TapHandler { enabled: !root.addingDestination; onTapped: { root.addingDestination = true; Qt.callLater(function() { destinationName.forceActiveFocus() }) } }
            }
        }
    }
}
