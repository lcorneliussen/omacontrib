import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from test_helper import ScriptedAPI, h, pr, search_page

SOURCE = "https://github.com/upstream/library/pull/178"
BUMP = "https://github.com/downstream/app/pull/9"


def resource(url, state="OPEN", kind="PullRequest", **extra):
    owner, name, _, number = url.split("/")[3:]
    return dict({"__typename": kind, "id": "node-" + number, "number": int(number), "title": "Allow dup3",
                 "url": url, "state": state, "updatedAt": "2026-10-03T19:03:26Z",
                 "author": {"login": "alice"}, "repository": {"nameWithOwner": f"{owner}/{name}"}}, **extra)


def track(**changes):
    return h.Settings.validate_tracks([dict({"url": SOURCE}, **changes)])


def versions(installed):
    return (patch.object(h, "installed_version", side_effect=installed.get),
            patch.object(h, "compare_versions", side_effect=lambda left, right: (left > right) - (left < right)))



def agent_run(result, code=0, session="5f0c-11", prompts=None):
    """A fake `claude -p --output-format stream-json` run."""
    def run(command, on_line):
        if prompts is not None:
            prompts.append(command)
        on_line(json.dumps({"type": "system", "subtype": "init", "session_id": session}) + "\n")
        on_line("not json\n")
        on_line(json.dumps({"type": "result", "result": result, "is_error": bool(code), "session_id": session}) + "\n")
        return code
    return run

class LandingTests(unittest.TestCase):
    def landing(self, tracks, resources, installed, rows=None, previous=(), refresh=True):
        rows = [] if rows is None else rows
        api = ScriptedAPI({f"r{index}": item for index, item in enumerate(resources)})
        first, second = versions(installed)
        with first, second:
            failure = h.apply_tracks(api, rows, tracks, list(previous), refresh)
        return rows, failure, api

    def test_closed_pull_request_waits_for_the_installed_package(self):
        tracks = track(packages=[{"name": "file", "operator": ">", "version": "5.48"}], workaround="the -S wrapper")
        rows, failure, _ = self.landing(tracks, [resource(SOURCE, "CLOSED")], {"file": "5.48-2"})
        self.assertIsNone(failure)
        self.assertEqual(rows[0]["kind"], "tracked")
        self.assertEqual(rows[0]["status"], "Awaiting file > 5.48")
        self.assertEqual([step["done"] for step in rows[0]["landing"]["steps"]], [True, False])
        self.assertEqual(rows[0]["landing"]["steps"][1]["label"], "file > 5.48 · installed 5.48-2")
        h.validate_snapshot({"schemaVersion": 1, "prs": rows})

        landed, _, api = self.landing(tracks, [], {"file": "5.49-1"}, previous=rows, refresh=False)
        self.assertEqual(api.calls, [])
        self.assertEqual(landed[0]["status"], "Landed")
        self.assertTrue(landed[0]["landing"]["landed"])
        self.assertEqual(h.landed_reminder(landed[0]), "\nYou can now drop: the -S wrapper")
        events = h.lifecycle_notifications({"prs": landed}, {"attemptedAt": 1, "prs": rows}, True)
        self.assertEqual(events[0][0], "Landed")

    def test_downstream_link_blocks_until_it_closes(self):
        tracks = track(after=[BUMP])
        rows, _, api = self.landing(tracks, [resource(SOURCE, "MERGED"), resource(BUMP)], {})
        self.assertEqual(api.calls[0][1], {"u0": SOURCE, "u1": BUMP})
        self.assertEqual(rows[0]["status"], "Awaiting downstream/app#9")
        self.assertEqual(rows[0]["landing"]["steps"][1]["label"], "downstream/app#9 · open")
        rows, _, _ = self.landing(tracks, [resource(SOURCE, "MERGED"), resource(BUMP, "MERGED")], {})
        self.assertEqual(rows[0]["status"], "Landed")

    def test_open_issue_is_not_awaiting_anything_yet(self):
        issue = SOURCE.replace("pull", "issues")
        rows, _, _ = self.landing(track(url=issue, packages=[{"name": "file", "operator": ">=", "version": "5.49"}]),
                                  [resource(issue, kind="Issue", stateReason=None)], {"file": "5.49-1"})
        self.assertEqual(rows[0]["status"], "Open")
        self.assertFalse(rows[0]["landing"]["landed"])

    def test_discussion_is_followed_until_answered_or_closed(self):
        discussion = SOURCE.replace("pull", "discussions")
        item = resource(discussion, kind="Discussion", closed=False, stateReason=None, isAnswered=False)
        del item["state"]
        rows, failure, api = self.landing(track(url=discussion, after=[BUMP]), [{"discussion": item}, resource(BUMP)], {})
        self.assertIsNone(failure)
        self.assertEqual(api.calls[0][1], {"o0": "upstream", "n0": "library", "d0": 178, "u1": BUMP})
        self.assertIn("r0: repository(owner:$o0, name:$n0) { discussion(number:$d0)", api.calls[0][0])
        self.assertEqual(rows[0]["status"], "Open")
        h.validate_snapshot({"schemaVersion": 1, "prs": rows})
        for change, label in (({"isAnswered": True}, "Answered"), ({"closed": True, "stateReason": "RESOLVED"}, "Resolved"),
                              ({"closed": True, "stateReason": "DUPLICATE"}, "Closed as duplicate")):
            rows, _, _ = self.landing(track(url=discussion, after=[BUMP]), [{"discussion": dict(item, **change)}, resource(BUMP)], {})
            self.assertEqual(rows[0]["landing"]["steps"][0], {"kind": "github", "label": label, "done": True})
            self.assertEqual(rows[0]["status"], "Awaiting downstream/app#9")

    def test_missing_discussion_is_reported(self):
        discussion = SOURCE.replace("pull", "discussions")
        rows, failure, _ = self.landing(track(url=discussion), [{"discussion": None}], {})
        self.assertEqual(rows, [])
        self.assertIn("pull request or discussion", failure)

    def test_requirements_attach_to_rows_found_by_discovery(self):
        open_row = h.basic_row(dict(pr(), url=SOURCE))
        merged = h.lifecycle_row("merged", resource(SOURCE, "MERGED"), "Released in v2")
        for row, status in ((open_row, None), (merged, "Awaiting file > 5.48")):
            rows, _, _ = self.landing(track(packages=[{"name": "file", "operator": ">", "version": "5.48"}]),
                                      [resource(SOURCE, "MERGED")], {"file": "5.48-1"}, rows=[row])
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].get("status"), status)
            self.assertFalse(rows[0]["landing"]["landed"])
        self.assertEqual(merged["landing"]["steps"][0]["label"], "Released in v2")

    def test_unknown_versions_and_failed_lookups_never_claim_a_landing(self):
        tracks = track(packages=[{"name": "file", "operator": ">", "version": "5.48"}])
        rows, _, _ = self.landing(tracks, [resource(SOURCE, "CLOSED")], {})
        self.assertEqual(rows[0]["landing"]["steps"][1]["label"], "file > 5.48 · not installed")
        with patch.object(h, "installed_version", return_value="5.49-1"), patch.object(h, "compare_versions", return_value=None):
            step, _ = h.package_step(tracks[0]["packages"][0])
        self.assertFalse(step["done"])
        rows = []
        failure = h.apply_tracks(ScriptedAPI(h.FetchError("offline")), rows, tracks, [], True)
        self.assertEqual((rows, failure), ([], "Landing tracking is unavailable. offline"))
        rows, failure, _ = self.landing(tracks, [None], {})
        self.assertEqual(rows, [])
        self.assertIn("not an accessible", failure)

    def test_closed_pull_requests_stay_visible(self):
        closed = dict(pr("gone", "CLOSED"), url=SOURCE, closedAt=h.time.strftime("%Y-%m-%dT%H:%M:%SZ"))
        old = dict(pr("old", "CLOSED"), closedAt="2020-01-01T00:00:00Z")
        api = ScriptedAPI(search_page([]), search_page([]), search_page([]), {"search": {"nodes": [closed, old]}})
        result = h.snapshot(api, "alice", {})
        self.assertIn("is:unmerged author:alice updated:>=", api.calls[-1][1]["q"])
        self.assertEqual([(row["id"], row["status"]) for row in result["prs"]], [("closed:gone", "Closed · not merged")])
        before = {"attemptedAt": 1, "prs": [dict(result["prs"][0], id="gone", kind="pull_request")]}
        self.assertEqual(h.lifecycle_notifications(result, before, True)[0][0], "Contribution closed")
        cached = h.snapshot(ScriptedAPI(search_page([])), "alice", dict(result, lifecycleCheckedAt=h.time.time()))
        self.assertEqual(cached["prs"][0]["id"], "closed:gone")

    def test_closed_pull_requests_in_counted_groups_are_kept_first(self):
        noise = [dict(pr(f"x{index}", "CLOSED"), url=f"https://github.com/me/scratch/pull/{index}",
                      repository={"nameWithOwner": "me/scratch"}, closedAt="2999-01-01T00:00:00Z") for index in range(h.MAX_CLOSED + 5)]
        wanted = dict(pr("wanted", "CLOSED"), url=SOURCE, closedAt="2999-01-01T00:00:00Z")
        groups = h.contribution_groups({"owners": ["me"]})
        rows = h.discover_closed(ScriptedAPI({"search": {"nodes": noise + [wanted]}}), "alice", groups)
        self.assertEqual(len(rows), h.MAX_CLOSED)
        self.assertEqual(rows[0]["id"], "closed:wanted")

    def test_accounts_do_not_duplicate_a_tracked_link(self):
        tracked = h.lifecycle_row("tracked", resource(SOURCE, "CLOSED"), "Landed")
        results = [{"account": "alice", "prs": [copy.deepcopy(tracked)]},
                   {"account": "bob", "prs": [copy.deepcopy(tracked)]}]
        merged = h.merge_account_results(results, ["alice", "bob"], {})
        self.assertEqual([row["id"] for row in merged["prs"]], ["alice:tracked:node-178"])


class IssueTests(unittest.TestCase):
    """Issues the account opened show up without being tracked by hand."""
    ISSUE = SOURCE.replace("pull", "issues")

    def issue(self, number, state="OPEN", closed_days=None, reason=None, comments=0, author="alice"):
        url = self.ISSUE.replace("178", str(number))
        closed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - closed_days * 86400)) if closed_days is not None else None
        return dict(resource(url, state, kind="Issue", stateReason=reason, closedAt=closed_at,
                             comments={"totalCount": comments}), author={"login": author})

    def test_open_and_recently_closed_issues_become_rows(self):
        api = ScriptedAPI()
        api.issues = [search_page([
            self.issue(1, comments=1),
            self.issue(2, "CLOSED", closed_days=3, reason="NOT_PLANNED"),
            self.issue(3, "CLOSED", closed_days=90, reason="COMPLETED"),
            self.issue(4, author="bob"),
        ])]
        rows = h.discover_issues(api, "alice")
        self.assertEqual(api.calls[0][1]["q"], "is:issue author:alice -user:alice -repo:omacom/omarchy-plugin-marketplace sort:updated-desc")
        self.assertEqual([(row["number"], row["status"], row["discussion"], row["closed"]) for row in rows],
                         [(1, "Open issue", 1, False), (2, "Closed · not planned", 0, True)])
        h.validate_snapshot({"schemaVersion": 1, "prs": rows})

    def test_a_new_comment_changes_the_summary_revision(self):
        before = h.discover_issues(ScriptedAPI(), "alice")
        api = ScriptedAPI()
        api.issues = [search_page([self.issue(1)])]
        quiet = h.discover_issues(api, "alice")[0]
        api.issues = [search_page([self.issue(1, comments=1)])]
        commented = h.discover_issues(api, "alice")[0]
        self.assertEqual(before, [])
        self.assertNotEqual(h.revision(quiet), h.revision(commented))

    def test_snapshot_lists_issues_and_keeps_them_when_search_fails(self):
        api = ScriptedAPI(search_page([]))
        api.issues = [search_page([self.issue(1)])]
        result = h.snapshot(api, "alice", {})
        self.assertEqual([row["kind"] for row in result["prs"]], ["issue"])
        api = ScriptedAPI(search_page([]))
        api.issues = [h.FetchError("GitHub is unavailable.")]
        again = h.snapshot(api, "alice", result)
        self.assertEqual([row["kind"] for row in again["prs"]], ["issue"])
        self.assertIn("Issues are unavailable", again["error"])

    def test_tracked_issue_keeps_its_landing_on_the_discovered_row(self):
        rows = h.discover_issues(self._api([self.issue(178)]), "alice")
        api = ScriptedAPI({"r0": resource(self.ISSUE, kind="Issue", stateReason=None)})
        h.apply_tracks(api, rows, track(url=self.ISSUE), [], True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "issue")
        self.assertEqual(rows[0]["landing"]["steps"][0]["label"], "Open")

    def test_closing_an_issue_notifies(self):
        open_row = h.discover_issues(self._api([self.issue(1)]), "alice")[0]
        closed_row = h.discover_issues(self._api([self.issue(1, "CLOSED", closed_days=0, reason="COMPLETED")]), "alice")[0]
        events = h.lifecycle_notifications({"prs": [closed_row]}, {"attemptedAt": 1, "prs": [open_row]}, True)
        self.assertEqual(events, [("Closed · completed", closed_row)])

    @staticmethod
    def _api(issues):
        api = ScriptedAPI()
        api.issues = [search_page(issues)]
        return api


class TrackSettingsTests(unittest.TestCase):
    def test_track_replaces_and_untrack_removes(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = h.Settings(Path(directory) / "config")
            self.assertNotIn("tracks", settings.read())
            with patch.object(h, "installed_version", return_value="1:5.48-1"):
                settings.track(SOURCE, [BUMP], ["file", "ghostty>=1.4.0"], "  my fork\n of file ", None)
            self.assertEqual(settings.read()["tracks"], [{
                "url": SOURCE, "after": [BUMP], "workaround": "my fork of file", "note": "",
                "packages": [{"name": "file", "operator": ">", "version": "1:5.48"},
                             {"name": "ghostty", "operator": ">=", "version": "1.4.0"}]}])
            settings.track(SOURCE, [], [], None, "found while debugging ghostty")
            self.assertEqual(settings.read()["tracks"][0]["packages"], [])
            settings.untrack(SOURCE)
            self.assertNotIn("tracks", settings.read())

    def test_invalid_requirements_are_rejected(self):
        with patch.object(h, "installed_version", return_value=None):
            for text in ("file", "file<5", "file>=", "two words"):
                with self.subTest(text=text), self.assertRaises(ValueError):
                    h.parse_requirement(text)
        for bad in ({"url": "https://example.com/x"}, {"url": SOURCE, "after": [SOURCE]},
                    {"url": SOURCE, "packages": [{"name": "file; rm", "operator": ">", "version": "1"}]},
                    {"url": SOURCE, "after": [BUMP] * 6}, {"url": SOURCE, "surprise": 1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                h.Settings.validate_tracks([bad])


if __name__ == "__main__":
    unittest.main()


class FollowThroughTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings = h.Settings(Path(self.directory.name) / "config")

    def test_status_and_sessions_follow_an_item_without_invalidating_the_cache(self):
        before = h.Settings.fingerprint(self.settings.read())
        self.settings.track(SOURCE, [], [], None, None)
        tracked = h.Settings.fingerprint(self.settings.read())
        self.assertNotEqual(before, tracked)
        self.settings.set_status(SOURCE, "  Waiting for\nfile 5.49 ", "you")
        env = {"CLAUDE_CODE_SESSION_ID": "e358-afb5", "CODEX_THREAD_ID": ""}
        with patch.dict(h.os.environ, env), patch.object(h.socket, "gethostname", return_value="desktop.lan"):
            self.settings.add_session(SOURCE, h.current_session(cwd="/home/me/work"))
            self.settings.add_session(SOURCE, h.current_session(cwd="/home/me/other"))
        track = self.settings.read()["tracks"][0]
        self.assertEqual(h.Settings.fingerprint(self.settings.read()), tracked)
        self.assertEqual(track["status"]["text"], "Waiting for file 5.49")
        self.assertEqual([(s["agent"], s["id"], s["host"], s["cwd"]) for s in track["sessions"]],
                         [("claude", "e358-afb5", "desktop", "/home/me/other")])
        self.settings.track(SOURCE, [BUMP], [], None, None)
        self.assertIn("status", self.settings.read()["tracks"][0])

        row = h.lifecycle_row("tracked", resource(SOURCE, "CLOSED"), "Closed")
        row["landing"] = {"landed": True, "steps": [{"kind": "github", "label": "Closed", "done": True}], "workaround": "", "note": ""}
        result = {"schemaVersion": 1, "prs": [row]}
        h.annotate_tracks(result, self.settings.read())
        self.assertEqual(row["landing"]["sessions"][0]["host"], "desktop")
        h.validate_snapshot(result)

    def test_sessions_need_an_agent_and_safe_fields(self):
        with patch.dict(h.os.environ, {"CLAUDE_CODE_SESSION_ID": "", "CODEX_THREAD_ID": ""}):
            with self.assertRaises(ValueError):
                h.current_session()
            self.assertEqual(h.current_session("codex", "019a-77", "box", "/srv")["agent"], "codex")
        for bad in ({"id": "x; rm -rf /"}, {"host": "-oProxyCommand=x"}, {"cwd": "relative"}, {"agent": "vim"}):
            session = dict({"agent": "claude", "id": "abc", "host": "box", "cwd": "/srv", "at": h.utc_now()}, **bad)
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                h.Settings.validate_session(session)

    def test_status_agent_records_only_its_final_status_line(self):
        self.settings.track(SOURCE, [], [], "wrapper", "broke ghostty")
        prompts = []

        with patch.dict(h.os.environ, {"XDG_CACHE_HOME": self.directory.name}), patch("builtins.print"):
            h.run_status_agent(self.settings, SOURCE, agent_run("Looked around.\nSTATUS: Committed upstream; waiting for file 5.49\n",
                                                                prompts=prompts))
            track = self.settings.read()["tracks"][0]
            self.assertEqual(track["status"]["text"], "Committed upstream; waiting for file 5.49")
            self.assertIn("Previous status", h.status_prompt(track))
            self.assertIn("Do not comment", prompts[0][2])
            self.assertNotIn("Bash(gh api:*)", prompts[0])
            # A check with nothing to go back to becomes the conversation `o` resumes.
            self.assertEqual([(s["agent"], s["id"], s["cwd"]) for s in track["sessions"]], [("claude", "5f0c-11", str(h.Path.home()))])
            self.assertFalse(h.agent_log_path(SOURCE, ".activity").exists())
            h.run_status_agent(self.settings, SOURCE, agent_run("no verdict", 1, session="9e1d-22"))
            track = self.settings.read()["tracks"][0]
            self.assertTrue(track["status"]["text"].startswith("Status check failed"))
            self.assertEqual([s["id"] for s in track["sessions"]], ["5f0c-11"])

    def test_agent_judges_who_has_to_act_next(self):
        self.settings.follow_up(SOURCE)
        with patch.dict(h.os.environ, {"XDG_CACHE_HOME": self.directory.name}), patch("builtins.print"):
            self.settings.set_status(SOURCE, "No status yet", "checking", "0123456789ab")
            h.run_status_agent(self.settings, SOURCE, agent_run("Found it.\nWAITING: me (reply to the ping)\nSTATUS: Owner asked for a rebase\n"))
        status = self.settings.read()["tracks"][0]["status"]
        self.assertEqual((status["waiting"], status["revision"]), ("me", "0123456789ab"))
        self.assertIn("WAITING: me", h.summary_prompt(self.settings.read()["tracks"][0]))

    def test_summaries_refresh_automatically_when_the_contribution_changes(self):
        clock = [1_800_000_000.0]
        spawn = lambda store, url, revision: store.follow_up(url) and store.set_status(url, "x", "checking", revision)
        older = dict(h.lifecycle_row("closed", resource(BUMP, "CLOSED"), "Closed · not merged"), updatedAt="2026-10-01T00:00:00Z")
        newer = h.lifecycle_row("closed", resource(SOURCE, "CLOSED"), "Closed · not merged")
        result = {"schemaVersion": 1, "prs": [older, newer], "excluded": []}
        with patch.dict(h.os.environ, {"XDG_CACHE_HOME": self.directory.name}), patch.object(h, "utc_now", lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(clock[0]))):
            run = lambda: h.auto_summaries(result, self.settings, now=lambda: clock[0], spawn=spawn)
            # No summary yet: newest first, one agent at a time.
            self.assertEqual(run(), SOURCE)
            self.assertIsNone(run())
            self.settings.set_status(SOURCE, "Waiting for review", "claude", h.revision(newer))
            self.assertEqual(run(), BUMP)
            self.settings.set_status(BUMP, "Abandoned", "claude", h.revision(older))
            self.assertIsNone(run())
            # A change waits until it has settled, then shows as outdated and runs again.
            newer["discussion"] = 6
            self.assertIsNone(run())
            annotated = copy.deepcopy(result)
            h.annotate_tracks(annotated, self.settings.read())
            self.assertTrue(annotated["prs"][1]["followUp"]["status"]["outdated"])
            h.validate_snapshot(annotated)
            clock[0] += h.AUTO_SETTLE
            self.assertEqual(run(), SOURCE)
            # A hand-written status is taken as current.
            self.settings.set_status(SOURCE, "Rebased", "you")
            clock[0] += h.AGENT_SECONDS
            self.assertIsNone(run())
            self.assertEqual(h.find_track(self.settings.read(), SOURCE)["status"]["revision"], h.revision(newer))
            self.settings.write(dict(self.settings.read(), autoSummary=False))
            newer["discussion"] = 7
            clock[0] += h.AUTO_SETTLE
            self.assertIsNone(run())
            self.assertEqual(h.Settings.fingerprint(self.settings.read()), h.Settings.fingerprint({**self.settings.read(), "tracks": []}))

    def test_rows_waiting_on_me_come_first_then_the_newest(self):
        rows = [dict(h.lifecycle_row("closed", resource(f"https://github.com/a/b/pull/{n}", "CLOSED"), "Closed"),
                     updatedAt=f"2026-10-0{n}T00:00:00Z") for n in (1, 2, 3)]
        rows[0]["followUp"] = {"status": {"text": "Rebase asked", "at": "2026-10-03T00:00:00Z", "by": "claude", "waiting": "me"}, "sessions": []}
        result = {"prs": rows, "excluded": []}
        h.order_rows(result)
        self.assertEqual([row["number"] for row in result["prs"]], [1, 3, 2])

    def test_checking_status_shows_what_the_agent_is_doing(self):
        self.settings.track(SOURCE, [], [], None, None)
        with patch.dict(h.os.environ, {"XDG_CACHE_HOME": self.directory.name}), patch("builtins.print"):
            def run(command, on_line):
                on_line(json.dumps({"type": "assistant", "message": {"content": [
                    {"type": "tool_use", "name": "Bash", "input": {"command": "gh pr view 178\n  --repo file/file"}}]}}))
                # Mid-run: the snapshot carries the activity next to the kept status.
                self.settings.set_status(SOURCE, "No status yet", "checking")
                row = h.lifecycle_row("tracked", resource(SOURCE, "CLOSED"), "Closed · not merged")
                row["landing"] = {"landed": False, "steps": [{"kind": "github", "label": "Closed", "done": True}], "workaround": "", "note": ""}
                result = {"schemaVersion": 1, "prs": [row]}
                h.annotate_tracks(result, self.settings.read())
                seen.append(row["landing"]["status"]["activity"])
                h.validate_snapshot(result)
                return 1
            seen = []
            h.run_status_agent(self.settings, SOURCE, run)
            self.assertEqual(seen, ["$ gh pr view 178 --repo file/file"])
            events = []
            h.print_agent_event({"type": "assistant", "message": {"content": [{"type": "text", "text": "Found it."}]}}, events.append)
            h.print_agent_event({"type": "result", "duration_ms": 41000, "result": "STATUS: x"}, events.append)
            self.assertEqual(events, ["\nFound it.\n", "\n· done in 41s"])

    def test_open_session_prefers_the_origin_and_crosses_machines(self):
        calls = []
        launcher = lambda *args: calls.append(args) or "fake"
        self.settings.track(SOURCE, [], [], None, None)
        h.open_session(self.settings, SOURCE, launcher, local_host="desktop")
        self.assertEqual(calls[-1][:3], ("upstream/library#178", "claude", "claude"))
        self.assertIn(SOURCE, calls[-1][3][0])
        self.settings.add_session(SOURCE, {"agent": "codex", "id": "019a-77", "host": "studio-mac", "cwd": "/Users/me/src"})
        h.open_session(self.settings, SOURCE, launcher, local_host="desktop")
        self.assertEqual(calls[-1], ("upstream/library#178", "codex", "codex", ["resume", "019a-77"], "studio-mac", "/Users/me/src"))
        h.open_session(self.settings, SOURCE, launcher, local_host="STUDIO-MAC")
        self.assertIsNone(calls[-1][4])

    def test_closed_item_gets_a_summary_and_discussion_without_a_landing(self):
        calls = []
        h.open_session(self.settings, SOURCE, lambda *args: calls.append(args) or "fake", local_host="desktop")
        track = self.settings.read()["tracks"][0]
        self.assertTrue(track["summaryOnly"])
        self.assertIn("If it was closed", calls[-1][3][0])
        with patch.dict(h.os.environ, {"XDG_CACHE_HOME": self.directory.name}), patch("builtins.print"):
            h.run_status_agent(self.settings, SOURCE, agent_run("STATUS: Applied by hand in 3f2a1c; nothing left to do\n"))
        row = h.lifecycle_row("closed", resource(SOURCE, "CLOSED"), "Closed · not merged")
        result = {"schemaVersion": 1, "prs": [row]}
        h.annotate_tracks(result, self.settings.read())
        self.assertNotIn("landing", row)
        self.assertEqual(row["followUp"]["status"]["text"], "Applied by hand in 3f2a1c; nothing left to do")
        h.validate_snapshot(result)
        self.settings.track(SOURCE, [BUMP], [], None, None)
        self.assertNotIn("summaryOnly", self.settings.read()["tracks"][0])
        with self.assertRaises(ValueError):
            h.Settings.validate_tracks([{"url": SOURCE, "after": [BUMP], "summaryOnly": True}])

    def test_dismissed_items_leave_the_snapshot_and_stop_being_followed(self):
        self.settings.follow_up(SOURCE)
        self.settings.dismiss(SOURCE)
        self.assertEqual(self.settings.read()["dismissed"], [SOURCE])
        self.assertNotIn("tracks", self.settings.read())
        result = {"schemaVersion": 1, "prs": [h.lifecycle_row("closed", resource(SOURCE, "CLOSED"), "Closed")],
                  "excluded": [h.lifecycle_row("closed", resource(BUMP, "CLOSED"), "Closed")]}
        h.annotate_tracks(result, self.settings.read())
        self.assertEqual([row["url"] for row in result["prs"] + result["excluded"]], [BUMP])
        self.settings.dismiss(SOURCE, False)
        self.assertNotIn("dismissed", self.settings.read())
        with self.assertRaises(ValueError):
            self.settings.dismiss("https://example.com/x")
