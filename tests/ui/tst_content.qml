import QtQuick
import QtTest
import qs.Commons
import qs.Ui as Ui
import "../.." as Plugin

Item {
    width: 640
    height: 680
    Rectangle {
        id: surface
        width: 640
        height: 680
        color: Color.popups.background
        Ui.PanelKeyCatcher {
            id: keys
            blocked: content.editing
            Keys.forwardTo: [content]
            anchors.fill: parent
            anchors.margins: 24
            onMoveRequested: function(dx, dy) { content.move(dx, dy) }
            onActivateRequested: content.activate()
            onTabRequested: function(direction) { content.move(0, direction) }
            onTextKey: function(text) { content.textKey(text) }
            Plugin.PrContent {
                id: content
                anchors.fill: parent
                loaded: true
            }
        }
    }
    SignalSpy { id: opened; target: content; signalName: "openRequested" }
    SignalSpy { id: closed; target: keys; signalName: "closeRequested" }
    SignalSpy { id: refreshed; target: content; signalName: "refreshRequested" }
    SignalSpy { id: copied; target: content; signalName: "copyRequested" }
    SignalSpy { id: configured; target: content; signalName: "configRequested" }

    TestCase {
        name: "PrContent"
        when: windowShown

        function row(id, title, draft, review, checks, discussion, inline) {
            let counts = { running: 0, success: 0, skipped: 0, failed: 0, unknown: 0 }
            for (let check of checks) counts[check.bucket]++
            return { id: id, number: Number(id), repository: "example/omarchy-tools", title: title,
                url: "https://github.com/example/omarchy-tools/pull/" + id, draft: draft,
                review: review, discussion: discussion, inline: inline,
                checks: checks, counts: counts, error: null, fetchedAt: 1788662400 }
        }
        function example() {
            return { schemaVersion: 1, account: "octocat", stale: false, partial: false, error: "", lastSuccessAt: 1788662400,
                prs: [
                    row("42", "Add pull request status to the Omarchy bar", false, "Approved", [
                        { name: "Unit tests", status: "SUCCESS", bucket: "success" },
                        { name: "Lint", status: "SUCCESS", bucket: "success" },
                        { name: "Integration tests", status: "IN_PROGRESS", bucket: "running" },
                        { name: "Deploy preview", status: "SKIPPED", bucket: "skipped" }
                    ], 3, 8),
                    row("41", "Improve keyboard navigation in the panel", false, "Changes requested", [
                        { name: "Unit tests", status: "SUCCESS", bucket: "success" },
                        { name: "Lint", status: "FAILURE", bucket: "failed" }
                    ], 2, 5),
                    row("39", "Explore a compact layout for vertical bars", true, "No review decision", [], 0, 0)
                ] }
        }
        function stackedExample() {
            let data = example()
            data.prs[0].stack = { number: 7, position: 1, size: 3 }
            data.prs[1].stack = { number: 7, position: 3, size: 3 }
            return data
        }
        function test_stack_labels_and_navigation() {
            content.snapshot = stackedExample()
            wait(100)
            let list = findChild(content, "prList")
            verify(list.itemAtIndex(0).stackStart)
            verify(!list.itemAtIndex(0).stackEnd)
            let firstCard = findChild(list.itemAtIndex(0), "prCard")
            let firstHeading = findChild(list.itemAtIndex(0), "stackHeading")
            verify(firstCard.x > 0)
            verify(firstCard.y >= firstHeading.height)
            verify(findChild(list.itemAtIndex(0), "stackRail").visible)
            verify(visibleText(list.itemAtIndex(0), "Stack #7"))
            verify(visibleText(list.itemAtIndex(0), "1/3"))
            content.move(0, 1)
            wait(100)
            verify(!list.itemAtIndex(1).stackStart)
            verify(list.itemAtIndex(1).stackEnd)
            compare(findChild(list.itemAtIndex(1), "prCard").x, firstCard.x)
            compare(findChild(list.itemAtIndex(1), "prCard").y, 0)
            verify(visibleText(list.itemAtIndex(1), "3/3"))
            content.move(1, 0)
            compare(content.expandedId, "41")
            content.activate()
            compare(opened.signalArguments[0][0], content.snapshot.prs[1].url)
            let updated = stackedExample()
            updated.prs.unshift(updated.prs.pop())
            content.snapshot = updated
            wait(100)
            compare(list.currentIndex, 2)
            content.activate()
            compare(opened.signalArguments[1][0], updated.prs[2].url)
        }
        function readyExample() {
            let data = stackedExample()
            data.prs[0].mergeable = "MERGEABLE"
            data.prs[0].mergeStateStatus = "CLEAN"
            data.prs[0].checks[2] = { name: "Integration tests", status: "SUCCESS", bucket: "success" }
            data.prs[0].counts.running = 0
            data.prs[0].counts.success = 3
            return data
        }
        function test_merge_ready_badge_requires_fresh_passing_state() {
            content.snapshot = readyExample()
            wait(100)
            let list = findChild(content, "prList")
            verify(list.itemAtIndex(0).mergeReady)
            compare(findChild(list.itemAtIndex(0), "prStatusText").text, "✓ Ready to merge")
            verify(findChild(list.itemAtIndex(0), "prStatusText").font.bold)
            verify(!list.itemAtIndex(1).mergeReady)
            let blockers = [ { draft: true }, { error: "Offline" }, { mergeable: "UNKNOWN" },
                { mergeStateStatus: "BLOCKED" }, { review: "Review required" }, { checks: [] , counts: { success: 0 } } ]
            for (let blocker of blockers) {
                let data = readyExample()
                Object.assign(data.prs[0], blocker)
                content.snapshot = data
                wait(30)
                verify(!list.itemAtIndex(0).mergeReady)
                compare(findChild(list.itemAtIndex(0), "prStatusText").text, blocker.draft ? "Draft" : "Open")
            }
            for (let flag of ["stale", "partial"]) {
                let data = readyExample()
                data[flag] = true
                content.snapshot = data
                wait(30)
                verify(!list.itemAtIndex(0).mergeReady)
                compare(findChild(list.itemAtIndex(0), "prStatusText").text, "Open")
            }
            for (let status of ["FAILURE", "IN_PROGRESS", "UNKNOWN"]) {
                let data = readyExample()
                data.prs[0].checks[2] = { name: "Integration tests", status: status, bucket: "unknown" }
                content.snapshot = data
                wait(30)
                verify(!list.itemAtIndex(0).mergeReady)
                compare(findChild(list.itemAtIndex(0), "prStatusText").text, "Open")
            }
        }
        function test_cancelled_check_allows_badge_only_with_clean_merge_state() {
            let data = readyExample()
            data.prs[0].checks[2] = { name: "Optional preview", status: "CANCELLED", bucket: "skipped" }
            data.prs[0].counts.success--
            data.prs[0].counts.skipped++
            content.snapshot = data
            wait(100)
            let list = findChild(content, "prList")
            verify(list.itemAtIndex(0).mergeReady)
            compare(findChild(list.itemAtIndex(0), "prStatusText").text, "✓ Ready to merge")
            for (let state of ["BLOCKED", "UNKNOWN"]) {
                let blocked = JSON.parse(JSON.stringify(data))
                blocked.prs[0].mergeStateStatus = state
                content.snapshot = blocked
                wait(30)
                verify(!list.itemAtIndex(0).mergeReady)
                compare(findChild(list.itemAtIndex(0), "prStatusText").text, "Open")
            }
        }
        function init() {
            opened.clear(); closed.clear(); refreshed.clear(); copied.clear(); configured.clear()
            content.copyBusy = false; content.copyFeedback = ""
            content.expandedId = ""; content.selectedId = ""
            content.cancelGroupEditor()
            content.rowEditingId = ""
            content.pane = "inbox"
            content.snapshot = example()
            keys.forceActiveFocus()
            wait(100)
        }

        function test_landing_steps_and_workaround() {
            let data = example()
            Object.assign(data.prs[0], { kind: "tracked", status: "Awaiting file > 5.48", review: "Awaiting file > 5.48",
                landing: { landed: false, workaround: "the -S wrapper", note: "Found while debugging a terminal crash",
                    steps: [ { kind: "github", label: "Closed · not merged", done: true },
                             { kind: "package", label: "file > 5.48 · installed 5.48-1", done: false } ] } })
            content.snapshot = data
            wait(100)
            let list = findChild(content, "prList")
            compare(findChild(list.itemAtIndex(0), "prStatusText").text, "Awaiting file > 5.48")
            compare(findChild(list.itemAtIndex(0), "landingSteps").text, "✓ #" + data.prs[0].number + " closed · not merged   →   ○ file > 5.48 · installed 5.48-1")
            compare(findChild(list.itemAtIndex(0), "landingWorkaround").text, "Workaround in place: the -S wrapper")
            verify(findChild(list.itemAtIndex(0), "landingNote").visible)
            verify(!findChild(list.itemAtIndex(1), "landingSteps").visible)
            compare(findChild(list.itemAtIndex(0), "followStatus").text, "No status yet")
            compare(findChild(list.itemAtIndex(0), "originSession").text, "o  start agent session")
            verify(!findChild(list.itemAtIndex(1), "originSession").visible)
            configured.clear()
            content.textKey("s")
            content.textKey("o")
            compare(configured.count, 2)
            compare(configured.signalArguments[0][0], "--ask-status")
            compare(configured.signalArguments[1][0], "--open-session")
            compare(configured.signalArguments[1][1], data.prs[0].url)
            data = JSON.parse(JSON.stringify(data))
            data.prs[0].landing.status = { text: "Committed upstream; waiting for 5.49", at: new Date().toISOString().slice(0, 19) + "Z", by: "claude" }
            data.prs[0].landing.sessions = [ { agent: "codex", id: "019a", host: "studio-mac", cwd: "/Users/me/src", at: "2026-10-01T10:00:00Z" } ]
            content.snapshot = data
            wait(100)
            compare(findChild(list.itemAtIndex(0), "followStatus").text, "Committed upstream; waiting for 5.49  ·  claude, 0m ago")
            compare(findChild(list.itemAtIndex(0), "originSession").text, "o  resume codex on studio-mac  ·  /Users/me/src")
            data.prs[0].landing.status.by = "checking"
            content.snapshot = JSON.parse(JSON.stringify(data))
            wait(100)
            configured.clear()
            content.textKey("s")
            compare(configured.count, 0)
            compare(findChild(list.itemAtIndex(0), "askStatus").text, "checking…")
            compare(findChild(list.itemAtIndex(0), "followStatus").text, "Agent is checking…  ·  was: Committed upstream; waiting for 5.49")
            compare(findChild(list.itemAtIndex(0), "watchAction").text, "w  watch")
            data.prs[0].landing.status.activity = "$ gh pr view 178"
            content.snapshot = JSON.parse(JSON.stringify(data))
            wait(100)
            compare(findChild(list.itemAtIndex(0), "followStatus").text, "Agent is checking…  ·  → $ gh pr view 178")
            content.textKey("w")
            compare(configured.count, 1)
            compare(configured.signalArguments[0][0], "--watch-status")
            delete data.prs[0].landing.status.activity
            data = JSON.parse(JSON.stringify(data))
            Object.assign(data.prs[0], { status: "Landed", review: "Landed" })
            data.prs[0].landing.landed = true
            content.snapshot = data
            wait(100)
            verify(list.itemAtIndex(0).completed)
            compare(findChild(list.itemAtIndex(0), "landingWorkaround").text, "You can now drop: the -S wrapper")
        }
        function test_named_groups_and_individual_assignment() {
            let data = example()
            data.groups = [
                { id: "inbox", name: "Active", count: true, items: [], repositories: [], owners: [] },
                { id: "later", name: "Later", count: false, items: [], repositories: [], owners: [] }
            ]
            let later = data.prs.splice(1, 1)[0]
            later.group = "later"
            data.excluded = [later]
            for (let item of data.prs) item.group = "inbox"
            content.snapshot = data
            content.textKey("2")
            wait(100)
            let list = findChild(content, "prList")
            compare(content.pane, "later")
            compare(list.count, 1)
            let moveAction = findChild(list.itemAtIndex(0), "moveAction")
            mouseClick(moveAction)
            compare(opened.count, 0)
            let assignment = findChild(list.itemAtIndex(0), "assignmentPopup")
            tryCompare(assignment, "opened", true)
            mouseClick(findChild(assignment.contentItem, "destination-inbox"))
            compare(configured.count, 1)
            compare(configured.signalArguments[0][0], "--move-item")
            compare(configured.signalArguments[0][1][0], "inbox")
            tryCompare(findChild(list.itemAtIndex(0), "assignmentPopup"), "opened", false)
        }

        function test_group_menu_holds_count_setting_and_delete_confirmation() {
            let data = example()
            data.groups = [
                { id: "inbox", name: "Active", count: true, items: [], repositories: [], owners: [] },
                { id: "later", name: "Later", count: false, items: [], repositories: [], owners: [] }
            ]
            content.snapshot = data
            wait(100)
            let laterTab = findChild(content, "groupTab-later")
            mouseClick(laterTab, laterTab.width / 2, laterTab.height / 2, Qt.RightButton)
            let menu = findChild(content, "groupMenu")
            tryCompare(menu, "opened", true)
            mouseClick(findChild(menu.contentItem, "countGroupAction"))
            compare(configured.count, 1)
            compare(configured.signalArguments[0][0], "--group-count")
            compare(configured.signalArguments[0][1][0], "later")
            compare(configured.signalArguments[0][1][1], "true")
            tryCompare(menu, "opened", false)
            content.menuGroup = data.groups[1]
            menu.open()
            tryCompare(menu, "opened", true)
            verify(findChild(menu.contentItem, "deleteGroupAction").visible)
            content.confirmingDelete = true
            verify(content.confirmingDelete)
            compare(configured.count, 1)
            menu.close()
        }

        function test_group_name_editor_blocks_panel_shortcuts() {
            let data = example()
            data.groups = [
                { id: "inbox", name: "Active", count: true, items: [], repositories: [], owners: [] },
                { id: "later", name: "Later", count: false, items: [], repositories: [], owners: [] }
            ]
            content.snapshot = data
            wait(100)
            let add = findChild(content, "addGroupTab")
            mouseClick(add)
            tryCompare(content, "addingGroup", true)
            let field = findChild(content, "groupName")
            tryVerify(function() { return field.activeFocus })
            keyClick(Qt.Key_R); keyClick(Qt.Key_K); keyClick(Qt.Key_X); keyClick(Qt.Key_1)
            keyClick(Qt.Key_Space)
            keyClick(Qt.Key_D); keyClick(Qt.Key_R); keyClick(Qt.Key_A); keyClick(Qt.Key_F); keyClick(Qt.Key_T)
            compare(field.text, "rkx1 draft")
            compare(refreshed.count, 0)
            compare(opened.count, 0)
            compare(configured.count, 0)
            compare(content.pane, "inbox")
            keyClick(Qt.Key_Return)
            compare(configured.count, 1)
            compare(configured.signalArguments[0][0], "--create-group")
            compare(configured.signalArguments[0][1], "rkx1 draft")
            content.configFinished("--create-group", true, "")
            verify(!content.addingGroup)
        }

        function test_new_group_editor_escape_cancels_without_closing_panel() {
            content.snapshot = Object.assign(example(), { groups: [{ id: "inbox", name: "Active", count: true }] })
            wait(100)
            mouseClick(findChild(content, "addGroupTab"))
            tryCompare(content, "addingGroup", true)
            keyClick(Qt.Key_L); keyClick(Qt.Key_A); keyClick(Qt.Key_T); keyClick(Qt.Key_E); keyClick(Qt.Key_R)
            keyClick(Qt.Key_Escape)
            tryCompare(content, "addingGroup", false)
            compare(findChild(content, "groupName").text, "")
            compare(configured.count, 0)
            compare(closed.count, 0)
        }

        function test_drag_onto_group_tab_and_new_group() {
            let data = example()
            data.groups = [{ id: "inbox", name: "Active", count: true }, { id: "excluded", name: "Excluded", count: false }]
            content.snapshot = data
            wait(100)
            let list = findChild(content, "prList")
            function dragTo(target) {
                let handle = findChild(list.itemAtIndex(1), "moveAction")
                let end = target.mapToItem(handle, target.width / 2, target.height / 2)
                mousePress(handle, handle.width / 2, handle.height / 2)
                for (let step = 1; step <= 10; step++)
                    mouseMove(handle, handle.width / 2 + (end.x - handle.width / 2) * step / 10, handle.height / 2 + (end.y - handle.height / 2) * step / 10)
                mouseRelease(handle, end.x, end.y)
            }
            dragTo(findChild(content, "groupTab-excluded"))
            compare(configured.count, 1)
            compare(configured.signalArguments[0][0], "--move-item")
            compare(configured.signalArguments[0][1], ["excluded", "https://github.com/example/omarchy-tools/pull/41"])
            dragTo(findChild(content, "groupTab-inbox"))
            compare(configured.count, 1)
            dragTo(findChild(content, "addGroupTab"))
            verify(content.addingGroup)
            compare(content.pendingDropUrl, "https://github.com/example/omarchy-tools/pull/41")
            compare(findChild(content, "groupName").placeholderText, "Name group for dropped item")
            content.cancelGroupEditor()
        }

        function test_exclude_item_button_and_keys() {
            let data = example()
            data.groups = [{ id: "inbox", name: "Active", count: true }, { id: "excluded", name: "Excluded", count: false }]
            content.snapshot = data
            wait(100)
            let list = findChild(content, "prList")
            mouseClick(findChild(list.itemAtIndex(1), "excludeAction"))
            compare(configured.signalArguments[0][0], "--move-item")
            compare(configured.signalArguments[0][1], ["excluded", "https://github.com/example/omarchy-tools/pull/41"])
            keyClick(Qt.Key_X)
            compare(configured.signalArguments[1][0], "--move-item")
            compare(configured.signalArguments[1][1], ["excluded", "https://github.com/example/omarchy-tools/pull/42"])
            keyClick(Qt.Key_X, Qt.ShiftModifier)
            compare(configured.signalArguments[2][0], "--move-repository")
            compare(configured.signalArguments[2][1], ["excluded", "example/omarchy-tools"])
            data = JSON.parse(JSON.stringify(data))
            for (let pr of data.prs) pr.group = "excluded"
            content.snapshot = data
            content.pane = "excluded"
            wait(100)
            compare(findChild(list.itemAtIndex(0), "excludeAction").text, "↩")
            keyClick(Qt.Key_X)
            compare(configured.signalArguments[3][1], ["inbox", "https://github.com/example/omarchy-tools/pull/42"])
        }

        function test_group_editor_validation_failure_and_drop_cancel() {
            content.snapshot = Object.assign(example(), { groups: [
                { id: "inbox", name: "Active", count: true },
                { id: "later", name: "Later", count: false }
            ] })
            content.addingGroup = true
            content.createNamedGroup(" later ")
            compare(configured.count, 0)
            compare(content.groupError, "That group already exists.")
            content.createNamedGroup("Needs review")
            compare(configured.count, 1)
            content.configFinished("--create-group", false, "Name rejected")
            verify(content.addingGroup)
            compare(content.groupError, "Name rejected")
            content.pendingDropUrl = "https://github.com/example/omarchy-tools/pull/42"
            content.cancelGroupEditor()
            compare(content.pendingDropUrl, "")
            compare(findChild(content, "groupName").text, "")
        }

        function test_move_and_new_group_shortcuts() {
            content.snapshot = Object.assign(example(), { groups: [{ id: "inbox", name: "Active", count: true }] })
            wait(100)
            content.textKey("m")
            let popup = findChild(findChild(content, "prList").itemAtIndex(0), "assignmentPopup")
            tryCompare(popup, "opened", true)
            compare(opened.count, 0)
            popup.close()
            content.textKey("n")
            tryCompare(content, "addingGroup", true)
            content.cancelGroupEditor()
        }

        function test_navigation_and_expand() {
            content.activate()
            compare(opened.signalArguments[0][0], "https://github.com/example/omarchy-tools/pull/42")
            content.move(0, 1)
            content.activate()
            compare(opened.signalArguments[1][0], "https://github.com/example/omarchy-tools/pull/41")
            content.move(1, 0)
            compare(content.expandedId, "41")
            content.move(-1, 0)
            compare(content.expandedId, "")
            content.move(0, -1)
            content.move(0, -1)
            content.activate()
            compare(opened.signalArguments[2][0], "https://github.com/example/omarchy-tools/pull/39")
        }
        function test_open_rows_can_be_summarized_and_discussed() {
            let data = example()
            content.snapshot = data
            wait(100)
            let list = findChild(content, "prList")
            verify(!findChild(list.itemAtIndex(0), "followActions").visible)
            content.textKey("s")
            content.textKey("o")
            compare(configured.count, 2)
            compare(configured.signalArguments[0][0], "--ask-status")
            compare(configured.signalArguments[1][0], "--open-session")
            data = JSON.parse(JSON.stringify(data))
            data.prs[0].followUp = { status: { text: "Waiting for review; checks green", at: "2026-10-22T08:00:00Z", by: "claude" }, sessions: [] }
            content.snapshot = data
            wait(100)
            verify(findChild(list.itemAtIndex(0), "followActions").visible)
            compare(findChild(list.itemAtIndex(0), "originSession").text, "o  discuss with an agent")
            verify(!findChild(list.itemAtIndex(1), "followActions").visible)
            data = JSON.parse(JSON.stringify(data))
            Object.assign(data.prs[0].followUp.status, { waiting: "me", outdated: true, at: new Date().toISOString().slice(0, 19) + "Z" })
            content.snapshot = data
            wait(100)
            compare(findChild(list.itemAtIndex(0), "followStatus").text, "Your move  ·  Waiting for review; checks green  ·  claude, 0m ago  ·  outdated")
        }
        function test_issue_rows_show_comments_and_close_like_pull_requests() {
            let data = example()
            let issue = row("181", "Let plugins extend the New session page", false, "Issue", [], 1, 0)
            Object.assign(issue, { kind: "issue", status: "Open issue", closed: false,
                url: "https://github.com/example/omarchy-tools/issues/181" })
            data.prs = [issue]
            content.snapshot = data
            wait(100)
            let list = findChild(content, "prList")
            compare(findChild(list.itemAtIndex(0), "prStatusText").text, "Open issue")
            verify(visibleText(list.itemAtIndex(0), "Issue  ·  Comments 1"))
            verify(!visibleText(list.itemAtIndex(0), "Inline"))
            verify(!findChild(list.itemAtIndex(0), "dismissAction").visible)
            data = JSON.parse(JSON.stringify(data))
            Object.assign(data.prs[0], { status: "Closed · not planned", closed: true })
            content.snapshot = data
            wait(100)
            compare(findChild(list.itemAtIndex(0), "prStatusText").text, "Closed · not planned")
            verify(findChild(list.itemAtIndex(0), "dismissAction").visible)
            content.textKey("d")
            compare(configured.signalArguments[configured.count - 1][0], "--dismiss")
            compare(configured.signalArguments[configured.count - 1][1], data.prs[0].url)
        }
        function test_selection_follows_the_pointer() {
            content.snapshot = example()
            wait(100)
            let list = findChild(content, "prList")
            compare(list.currentIndex, 0)
            let second = list.itemAtIndex(1)
            mouseMove(second, second.width / 2, second.height / 2)
            tryCompare(list, "currentIndex", 1)
            compare(content.selectedId, "41")
            // Keys move the selection; the resting pointer does not take it back.
            content.move(0, 1)
            compare(list.currentIndex, 2)
            mouseMove(second, second.width / 2, second.height / 2)
            wait(50)
            compare(list.currentIndex, 2)
            mouseMove(second, second.width / 2, second.height / 2 + 2)
            tryCompare(list, "currentIndex", 1)
            mouseMove(content, 1, 1)
        }
        function test_copy_shortcuts_and_feedback() {
            keyClick(Qt.Key_C, Qt.ControlModifier)
            keyClick(Qt.Key_C, Qt.AltModifier)
            keyClick(Qt.Key_C, Qt.MetaModifier)
            compare(copied.count, 0)
            keyClick(Qt.Key_C)
            compare(copied.count, 1)
            compare(copied.signalArguments[0][0], "https://github.com/example/omarchy-tools/pull/42")
            compare(copied.signalArguments[0][1], false)
            compare(content.copyFeedback, "")
            verify(content.copyBusy)
            keyClick(Qt.Key_C, Qt.ShiftModifier)
            compare(copied.count, 1)
            content.copyFinished(true, false)
            compare(content.copyFeedback, "URL copied")
            tryCompare(findChild(content, "copyToast"), "opacity", 1)
            verify(visibleText(content, "URL copied"))
            keyClick(Qt.Key_Down)
            keyClick(Qt.Key_C, Qt.ShiftModifier)
            compare(copied.signalArguments[1][0], "example/omarchy-tools#41")
            compare(copied.signalArguments[1][1], true)
            content.copyFinished(true, true)
            compare(content.copyFeedback, "Reference copied")
            compare(findChild(content, "prList").currentIndex, 1)
            compare(opened.count, 0)
            compare(closed.count, 0)
            tryCompare(content, "copyFeedback", "", 3000)
            tryCompare(findChild(content, "copyToast"), "visible", false)
        }
        function test_copy_after_refresh_and_failure() {
            keyClick(Qt.Key_Down)
            let updated = example()
            updated.prs.unshift(updated.prs.pop())
            content.snapshot = updated
            wait(100)
            keyClick(Qt.Key_C)
            compare(copied.signalArguments[0][0], "https://github.com/example/omarchy-tools/pull/41")
            content.copyFinished(false, false)
            compare(content.copyFeedback, "Could not copy")
            verify(!content.copyBusy)
            keyClick(Qt.Key_C)
            compare(copied.count, 2)
            content.copyFinished(true, false)
            compare(content.copyFeedback, "URL copied")
        }
        function test_copy_empty_list() {
            content.snapshot = { prs: [] }
            wait(100)
            keyClick(Qt.Key_C)
            keyClick(Qt.Key_C, Qt.ShiftModifier)
            compare(copied.count, 0)
            verify(!content.copyBusy)
            compare(content.copyFeedback, "")
        }
        function test_refresh_preserves_selection() {
            content.move(0, 1)
            let updated = example()
            updated.prs.reverse()
            content.snapshot = updated
            wait(100)
            content.activate()
            compare(opened.signalArguments[0][0], "https://github.com/example/omarchy-tools/pull/41")
        }
        function test_initial_and_refresh_layout_do_not_clip_first_pr() {
            let data = example()
            for (let i = 100; i < 115; i++) data.prs.push(row(String(i), "A long pull request title that wraps onto several lines to exercise asynchronous delegate heights and initial list positioning", false, "Review required", [], 1, 2))
            content.snapshot = data
            wait(100)
            let list = findChild(content, "prList")
            compare(list.currentIndex, 0)
            verify(Math.abs(list.contentY - list.originY) < 1)
            let compactHeight = list.itemAtIndex(0).height
            verify(compactHeight < 100)
            list.positionViewAtIndex(3, ListView.Contain)
            wait(100)
            verify(list.itemAtIndex(3).height > compactHeight)
            content.snapshot = example()
            wait(100)
            verify(Math.abs(list.contentY - list.originY) < 1)
        }

        function test_four_compact_rows_fit_without_scrolling() {
            let data = example()
            data.prs.push(row("38", "Fourth contribution", false, "Approved", [], 0, 0))
            content.snapshot = data
            wait(100)
            let list = findChild(content, "prList")
            let fourth = list.itemAtIndex(3)
            verify(fourth !== null)
            verify(fourth.y + fourth.height <= list.height)
            verify(Math.abs(list.contentY - list.originY) < 1)
        }
        function test_keyboard_and_title_click() {
            keyClick(Qt.Key_Down)
            keyClick(Qt.Key_Right)
            compare(content.expandedId, "41")
            keyClick(Qt.Key_Return)
            compare(opened.signalArguments[0][0], "https://github.com/example/omarchy-tools/pull/41")
            keyClick(Qt.Key_Left)
            compare(content.expandedId, "")
            keyClick(Qt.Key_R)
            compare(refreshed.count, 1)
            keyClick(Qt.Key_Escape)
            compare(closed.count, 1)
            content.move(0, -1)
            wait(100)
            let list = findChild(content, "prList")
            let title = findChild(list.itemAtIndex(0), "prTitle")
            mouseClick(title, 20, title.height / 2)
            compare(opened.signalArguments[1][0], "https://github.com/example/omarchy-tools/pull/42")
        }
        function test_empty_and_error() {
            content.snapshot = { prs: [], stale: true, error: "Sign in with gh auth login." }
            wait(100)
            content.move(0, 1)
            content.activate()
            compare(opened.count, 0)
            verify(surface.visible)
        }
        function visibleText(item, expected) {
            if (item.visible && item.text !== undefined && item.text.indexOf(expected) >= 0) return true
            for (let child of item.children) {
                if (visibleText(child, expected)) return true
            }
            return false
        }
        function test_capped_results_show_unknown_and_stale_details() {
            let data = example()
            data.partial = true
            data.stale = true
            data.error = "Some PR details are unavailable. Review pagination limit reached."
            data.prs = data.prs.slice(0, 2)
            data.prs[0].error = "Review pagination limit reached; previous details retained."
            data.prs[1].checks = null
            data.prs[1].counts = null
            data.prs[1].inline = null
            data.prs[1].review = "Unknown"
            data.prs[1].error = "Check pagination limit reached; checks are unavailable."
            data.prs[1].fetchedAt = null
            content.snapshot = data
            wait(100)
            let list = findChild(content, "prList")
            verify(visibleText(content, "Incomplete ·"))
            verify(visibleText(content, "Last complete update"))
            verify(visibleText(list.itemAtIndex(0), "Stale / unavailable"))
            verify(visibleText(list.itemAtIndex(0), "Last updated"))
            content.move(0, 1)
            wait(100)
            let unknown = list.itemAtIndex(1)
            compare(unknown.summary, "Unknown")
            verify(visibleText(unknown, "Inline —"))
            verify(visibleText(unknown, "Check pagination limit reached"))
            content.move(1, 0)
            compare(content.expandedId, data.prs[1].id)
            compare(unknown.summary, "Unknown")
        }
        function test_preview() {
            content.snapshot = readyExample()
            content.copySelected(false)
            content.copyFinished(true, false)
            wait(250)
            let image = grabImage(surface)
            verify(image.width > 0)
            image.save(Qt.resolvedUrl("../../preview.png").toString().replace("file://", ""))
        }
        function test_preview_light() {
            content.snapshot = readyExample()
            content.copySelected(true)
            content.copyFinished(true, true)
            Color.popups.background = "#f4f5f8"
            Color.popups.text = "#263044"
            Color.muted = "#768196"
            Color.accent = "#4d689e"
            Color.urgent = "#b1394b"
            wait(250)
            let image = grabImage(surface)
            image.save(Qt.resolvedUrl("../../preview-light.png").toString().replace("file://", ""))
            Color.popups.background = "#171b24"
            Color.popups.text = "#e0e4ec"
            Color.muted = "#929cae"
            Color.accent = "#98b8ed"
            Color.urgent = "#e47d85"
        }
    }
}
