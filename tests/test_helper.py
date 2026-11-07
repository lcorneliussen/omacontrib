import copy
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("helper", Path(__file__).resolve().parents[1] / "bin/omacontrib.py")
h = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(h)


def connection(nodes, cursor=None):
    return {"nodes": nodes, "pageInfo": {"hasNextPage": cursor is not None, "endCursor": cursor}}


def check(name="unit", state="SUCCESS", kind="CheckRun"):
    if kind == "StatusContext":
        return {"id": name, "__typename": kind, "context": name, "state": state}
    return {"id": name, "__typename": kind, "name": name, "status": "COMPLETED", "conclusion": state}


def pr(identifier="p1", state="OPEN", author="alice", draft=False):
    return {
        "id": identifier, "number": 1, "title": "A <plain> title", "url": "https://github.com/example/project/pull/1",
        "repository": {"nameWithOwner": "example/project"}, "author": {"login": author},
        "state": state, "isDraft": draft, "updatedAt": "2026-09-06T00:00:00Z",
        "reviewDecision": "APPROVED", "comments": {"totalCount": 3},
        "reviews": connection([{"id": "review", "state": "APPROVED", "comments": {"totalCount": 4}}]),
        "commits": {"nodes": [{"commit": {"oid": "abc123", "statusCheckRollup": {"contexts": connection([check()])}}}]},
    }


class ScriptedAPI:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        # Issue searches are answered from their own script so existing sequences stay as they were.
        self.issues = []

    def graphql(self, query, timeout=35, **variables):
        self.calls.append((query, variables))
        if query == h.ISSUE_SEARCH:
            response = self.issues.pop(0) if self.issues else search_page([])
            if isinstance(response, Exception):
                raise response
            return copy.deepcopy(response)
        if query in {h.SUBMISSION_SEARCH, h.MERGED_SEARCH, h.CLOSED_SEARCH} and not self.responses:
            return search_page([])
        if not self.responses:
            raise AssertionError("Unexpected API request: " + query)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return copy.deepcopy(response)


def search_page(items, cursor=None, total=None):
    return {"search": dict(connection(items, cursor), issueCount=len(items) if total is None else total)}


def concurrent_refresh(path, calls):
    class API:
        def graphql(self, query, timeout=35, **variables):
            with calls.get_lock():
                calls.value += 1
            time.sleep(0.05)
            return {"viewer": {"login": "alice"}} if "viewer { login }" in query else search_page([])
    h.run(API(), h.Cache(Path(path)), force=True)


class HelperTests(unittest.TestCase):
    def test_settings_add_remove_and_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = h.Settings(Path(directory) / "config")
            settings.move_rule("owner", "excluded", "Example")
            settings.move_rule("repository", "excluded", "Community/Project")
            settings.move_rule("item", "excluded", "https://github.com/community/project/pull/4")
            later = settings.create_group_and_move("item", "https://github.com/community/project/issues/5", "Review later")
            settings.add("account", "alice")
            value = settings.read()
            self.assertEqual(value["schemaVersion"], 2)
            self.assertEqual(value["accounts"], ["alice"])
            self.assertEqual(value["groups"][1]["owners"], ["example"])
            self.assertEqual(value["groups"][1]["repositories"], ["community/project"])
            self.assertEqual(value["groups"][1]["items"], ["https://github.com/community/project/pull/4"])
            self.assertEqual(value["groups"][2]["id"], later)
            self.assertEqual(value["groups"][2]["items"], ["https://github.com/community/project/issues/5"])
            self.assertEqual(settings.path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(settings.path.parent.stat().st_mode & 0o777, 0o700)
            settings.move_rule("owner", "inbox", "EXAMPLE")
            self.assertEqual(settings.read()["groups"][1]["owners"], [])

    def test_exclusions_partition_rows_without_hiding_discovery(self):
        source = pr()
        api = ScriptedAPI(search_page([source]), {"nodes": [source]})
        result = h.snapshot(api, "alice", {}, {"repositories": [], "owners": ["example"]})
        self.assertEqual(result["prs"], [])
        self.assertEqual([row["id"] for row in result["excluded"]], ["p1"])
        self.assertEqual(result["excluded"][0]["group"], "excluded")

    def test_individual_item_group_takes_precedence(self):
        groups = h.contribution_groups({})
        groups.append({"id": "later", "name": "Later", "count": False, "items": ["https://github.com/example/project/pull/1"], "repositories": [], "owners": []})
        groups[1]["owners"] = ["example"]
        group, scope = h.group_for("example/project", "https://github.com/example/project/pull/1", groups)
        self.assertEqual((group["id"], scope), ("later", "item"))

    def test_auto_groups_injects_access_visibility_buckets(self):
        groups = h.auto_groups(h.contribution_groups({}))
        self.assertEqual([g["id"] for g in groups], ["inbox", "private", "maintained", "excluded"])
        self.assertEqual(groups[0]["name"], "Upstream")
        self.assertEqual([g["count"] for g in groups[:3]], [True, True, False])
        # Stays within the strict snapshot/settings schema, and is idempotent.
        h.Settings.validate({"schemaVersion": 2, "accounts": [], "groups": groups})
        self.assertEqual([g["id"] for g in h.auto_groups(groups)], [g["id"] for g in groups])

    def test_auto_groups_defers_to_user_owned_ids_or_names(self):
        owned_id = [dict(h.contribution_groups({})[0]),
                    {"id": "maintained", "name": "Mine", "count": True, "items": [], "repositories": [], "owners": []}]
        self.assertEqual(h.auto_groups(owned_id), owned_id)
        owned_name = [{"id": "inbox", "name": "Upstream", "count": True, "items": [], "repositories": [], "owners": []}]
        self.assertEqual(h.auto_groups(owned_name), owned_name)

    def test_group_for_routes_by_access_then_visibility(self):
        groups = h.auto_groups(h.contribution_groups({}))

        def route(access, visibility):
            row = {"access": access, "visibility": visibility}
            return h.group_for("o/r", "https://github.com/o/r/pull/1", groups, row)[0]["id"]
        self.assertEqual(route("contributor", "public"), "inbox")      # upstream fallback
        self.assertEqual(route("contributor", "private"), "private")
        self.assertEqual(route("maintainer", "public"), "maintained")
        self.assertEqual(route("maintainer", "private"), "maintained")  # access wins over visibility
        # row=None keeps the old fallback (no auto-routing); a manual rule overrides the auto bucket.
        self.assertEqual(h.group_for("o/r", "https://github.com/o/r/pull/1", groups)[0]["id"], "inbox")
        groups[-1]["repositories"] = ["o/r"]
        row = {"access": "contributor", "visibility": "private"}
        self.assertEqual(h.group_for("o/r", "https://github.com/o/r/pull/1", groups, row)[1], "repository")

    def test_authored_pull_requests_include_personal_repositories(self):
        personal = pr("personal")
        personal["repository"]["nameWithOwner"] = "alice/project"
        external = pr("external")
        external["repository"]["nameWithOwner"] = "community/project"
        found, partial = h.discover(ScriptedAPI(search_page([personal, external])), "alice")
        self.assertEqual([item["id"] for item in found], ["personal", "external"])
        self.assertEqual(found[0]["kind"], "pull_request")
        self.assertEqual(partial, "")

    def test_marketplace_submission_status_and_comments(self):
        issue = {
            "id": "submission-1", "number": 42, "title": "[Plugin]: OmaContrib",
            "url": "https://github.com/omacom/omarchy-plugin-marketplace/issues/42",
            "state": "OPEN", "updatedAt": "2026-10-02T00:00:00Z",
            "author": {"login": "alice"},
            "repository": {"nameWithOwner": h.MARKETPLACE_REPOSITORY},
            "labels": {"nodes": [{"name": "validated"}, {"name": "needs-fixes"}]},
            "comments": {"totalCount": 3},
        }
        rows = h.discover_submissions(ScriptedAPI(search_page([issue])), "alice")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "submission")
        self.assertEqual(rows[0]["status"], "Needs fixes")
        self.assertEqual(rows[0]["discussion"], 3)

    def test_merged_omarchy_contribution_is_marked_shipped_only_when_tag_contains_it(self):
        merged = {
            "id": "merged-1", "number": 7, "title": "Ship it",
            "url": "https://github.com/omacom/omarchy/pull/7", "state": "MERGED",
            "updatedAt": "2026-10-01T00:00:00Z", "mergedAt": "2026-10-01T00:00:00Z",
            "author": {"login": "alice"}, "mergeCommit": {"oid": "abc123"},
            "repository": {"nameWithOwner": "omacom/omarchy", "latestRelease": {
                "tagName": "v5.0.0", "publishedAt": "2026-10-02T00:00:00Z",
                "url": "https://github.com/omacom/omarchy/releases/tag/v5.0.0"}},
        }

        class API(ScriptedAPI):
            def rest(self, endpoint, timeout=35):
                self.endpoint = endpoint
                return {"status": "ahead"}

        api = API({"search": {"nodes": [merged]}})
        rows = h.discover_merged(api, "alice")
        self.assertEqual(rows[0]["status"], "Shipped in Omarchy v5.0.0")
        self.assertIn("compare/abc123...v5.0.0", api.endpoint)

    def test_discovery_pagination_dedup_scope_and_drafts(self):
        first, draft = pr(), pr("p2", draft=True)
        draft["updatedAt"] = "2026-09-06T02:00:00Z"
        api = ScriptedAPI(search_page([first, pr("closed", state="CLOSED"), pr("other", author="bob"), None], "next"), search_page([first, draft]))
        result, partial = h.discover(api, "alice")
        self.assertEqual([p["id"] for p in result], ["p2", "p1"])
        self.assertTrue(result[0]["isDraft"])
        self.assertFalse(partial)
        self.assertEqual(api.calls[1][1]["cursor"], "next")
        self.assertEqual(api.calls[0][1]["q"], "is:pr is:open author:alice sort:updated-desc")

    def test_search_cap_is_explicit(self):
        result, partial = h.discover(ScriptedAPI(search_page([pr()], total=1001)), "alice")
        self.assertTrue(partial)
        self.assertEqual(len(result), 1)

    def test_nonadvancing_cursor_is_error(self):
        api = ScriptedAPI(search_page([pr()], "same"), search_page([pr()], "same"))
        with self.assertRaises(h.FetchError):
            h.discover(api, "alice")

    def test_every_check_category_and_original_state(self):
        for status, expected in {"SUCCESS": "success", "SKIPPED": "skipped", "CANCELLED": "skipped", "NEUTRAL": "skipped", "FAILURE": "failed", "TIMED_OUT": "failed", "ACTION_REQUIRED": "failed", "STARTUP_FAILURE": "failed", "NEW_STATE": "unknown", None: "unknown"}.items():
            with self.subTest(status=status):
                self.assertEqual(h.bucket(check(state=status)), expected)
        for state in ("QUEUED", "WAITING", "PENDING", "IN_PROGRESS", "REQUESTED"):
            self.assertEqual(h.bucket(dict(check(), status=state, conclusion=None)), "running")
        for state, expected in {"ERROR": "failed", "FAILURE": "failed", "PENDING": "running", "SUCCESS": "success"}.items():
            self.assertEqual(h.bucket(check(state=state, kind="StatusContext")), expected)

    def test_checks_follow_pages_pinned_to_original_head(self):
        source = pr()
        source["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"] = connection([check()], "next")
        api = ScriptedAPI({"repository": {"object": {"statusCheckRollup": {"contexts": connection([check(), check("lint", "CANCELLED"), check("external", "ERROR", "StatusContext")])}}}})
        row = h.normalize(api, source)
        self.assertEqual(len(row["checks"]), 3)
        self.assertEqual(row["counts"], {"running": 0, "success": 1, "skipped": 1, "failed": 1, "unknown": 0})
        self.assertEqual(row["checks"][1]["status"], "CANCELLED")
        self.assertEqual(api.calls[0][1]["oid"], "abc123")

    def test_comment_totals_separate_submitted_from_pending(self):
        source = pr()
        # Review totals include replies and resolved/outdated comments; review bodies are not requested.
        source["reviews"] = connection([
            {"id": "resolved", "state": "DISMISSED", "comments": {"totalCount": 4}},
            {"id": "pending", "state": "PENDING", "comments": {"totalCount": 50}},
        ], "next")
        api = ScriptedAPI({"node": {"reviews": connection([
            {"id": "resolved", "state": "DISMISSED", "comments": {"totalCount": 4}},
            {"id": "reply", "state": "COMMENTED", "comments": {"totalCount": 2}},
            {"id": "approval-only", "state": "APPROVED", "comments": {"totalCount": 0}},
        ])}})
        row = h.normalize(api, source)
        self.assertEqual(row["discussion"], 3)
        self.assertEqual(row["inline"], 6)
        self.assertIn("after:$cursor", api.calls[0][0])

    def test_check_retries_replace_old_results_across_pages_in_any_order(self):
        def attempt(run_id, state):
            return dict(check("unit-test-result", state), id=str(run_id), databaseId=run_id,
                        checkSuite={"id": "suite-" + str(run_id), "app": {"id": "actions"},
                                    "workflowRun": {"workflow": {"id": "ci"}}})

        for state, expected in (("SUCCESS", "success"), ("FAILURE", "failed"),
                                ("QUEUED", "running"), ("SKIPPED", "skipped")):
            for reverse in (False, True):
                with self.subTest(state=state, reverse=reverse):
                    old = attempt(10, "FAILURE" if state == "SUCCESS" else "SUCCESS")
                    new = attempt(20, state)
                    if state == "QUEUED":
                        new.update(status="QUEUED", conclusion=None)
                    first, second = (new, old) if reverse else (old, new)
                    source = pr()
                    source["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"] = connection([first], "next")
                    api = ScriptedAPI({"repository": {"object": {"statusCheckRollup": {
                        "contexts": connection([second])}}}})
                    row = h.normalize(api, source)
                    self.assertEqual(row["checks"], [{"name": "unit-test-result", "status": state, "bucket": expected}])
                    self.assertEqual(sum(row["counts"].values()), 1)
                    self.assertEqual(row["counts"][expected], 1)

    def test_same_named_checks_keep_distinct_workflows_apps_and_unknown_origins(self):
        original = dict(check(state="FAILURE"), databaseId=10,
                        checkSuite={"id": "suite", "app": {"id": "actions"},
                                    "workflowRun": {"workflow": {"id": "ci"}}})
        for difference in ("workflow", "app", "missing", "status"):
            with self.subTest(difference=difference):
                other = copy.deepcopy(original)
                other.update(id="other", databaseId=20, conclusion="SUCCESS")
                if difference == "workflow":
                    other["checkSuite"]["workflowRun"]["workflow"]["id"] = "deploy"
                elif difference == "app":
                    other["checkSuite"]["app"]["id"] = "another-app"
                elif difference == "missing":
                    other.pop("checkSuite")
                else:
                    other = check(kind="StatusContext")
                source = pr()
                source["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"] = connection([original, other])
                row = h.normalize(ScriptedAPI(), source)
                self.assertEqual(len(row["checks"]), 2)
                self.assertEqual(row["counts"]["failed"], 1)

    def test_successful_refresh_replaces_previous_check_list(self):
        source = pr()
        old = h.normalize(ScriptedAPI(), source)
        old["checks"] = [{"name": "removed", "status": "FAILURE", "bucket": "failed"}]
        old["counts"].update(success=0, failed=1)
        api = ScriptedAPI(search_page([source]), {"nodes": [source]})
        result = h.snapshot(api, "alice", {"prs": [old], "lastSuccessAt": 12})
        self.assertEqual(result["prs"][0]["counts"]["failed"], 0)
        self.assertEqual(result["prs"][0]["checks"][0]["name"], "unit")

    def test_review_states(self):
        for decision, expected in {"APPROVED": "Approved", "CHANGES_REQUESTED": "Changes requested", "REVIEW_REQUIRED": "Review required", None: "No review decision", "NEW": "Unknown"}.items():
            source = pr()
            source["reviewDecision"] = decision
            self.assertEqual(h.normalize(ScriptedAPI(), source)["review"], expected)

    def test_no_checks_is_distinct_from_missing_head(self):
        source = pr()
        source["commits"]["nodes"][0]["commit"]["statusCheckRollup"] = None
        self.assertEqual(h.normalize(ScriptedAPI(), source)["checks"], [])
        source["commits"]["nodes"] = []
        with self.assertRaises(h.FetchError):
            h.normalize(ScriptedAPI(), source)

    def test_failed_detail_retains_old_counts_and_timestamp(self):
        old = h.normalize(ScriptedAPI(), pr())
        api = ScriptedAPI(search_page([pr(), pr("new")]), h.FetchError("Offline", "network"))
        result = h.snapshot(api, "alice", {"prs": [old], "lastSuccessAt": 12})
        self.assertTrue(result["partial"])
        self.assertEqual(result["prs"][0]["discussion"], 3)
        self.assertEqual(result["prs"][0]["fetchedAt"], old["fetchedAt"])
        self.assertIsNone(result["prs"][1]["counts"])
        self.assertIsNone(result["prs"][1]["inline"])
        self.assertEqual(result["lastSuccessAt"], 12)

    def test_nested_failure_does_not_turn_into_success(self):
        source = pr()
        source["reviews"]["pageInfo"] = {"hasNextPage": True, "endCursor": "r2"}
        result = h.snapshot(ScriptedAPI(search_page([source]), {"nodes": [source]}, h.FetchError("offline")), "alice", {})
        self.assertTrue(result["partial"])
        self.assertIsNone(result["prs"][0]["checks"])

    def test_closed_during_fetch_and_closed_since_last_refresh_are_removed(self):
        result = h.snapshot(ScriptedAPI(search_page([pr()]), {"nodes": [pr(state="MERGED")]}), "alice", {"prs": [h.basic_row(pr("old"))]})
        self.assertEqual(result["prs"], [])
        self.assertFalse(result["partial"])

    def test_details_are_batched(self):
        items = [pr(str(i)) for i in range(21)]
        api = ScriptedAPI(search_page(items), {"nodes": items[:20]}, {"nodes": items[20:]})
        result = h.snapshot(api, "alice", {})
        self.assertEqual(len(result["prs"]), 21)
        self.assertEqual([len(call[1]["ids"]) for call in api.calls if "ids" in call[1]], [20, 1])

    def test_rate_limit_stops_remaining_batches(self):
        items = [pr(str(i)) for i in range(21)]
        api = ScriptedAPI(search_page(items), h.FetchError("rate limited", "rate_limit", 900))
        result = h.snapshot(api, "alice", {})
        self.assertEqual(len(api.calls), 6)
        self.assertEqual(len(result["prs"]), 21)
        self.assertTrue(result["partial"])
        self.assertGreater(result["retryAt"], time.time() + 890)

    def test_gh_uses_stdin_and_never_a_shell(self):
        with patch.object(h, "bounded_process", return_value=subprocess.CompletedProcess([], 0, '{"data":{"viewer":{"login":"alice"}}}', "")) as run:
            h.GitHub().graphql("query($q:String!) { search(query:$q) { issueCount } }", q='$(touch /tmp/unsafe)')
            args, kwargs = run.call_args
            self.assertNotIn("shell", kwargs)
            self.assertEqual(args[0][-2:], ["--input", "-"])
            self.assertNotIn("touch", " ".join(args[0]))
            self.assertEqual(json.loads(args[1])["variables"]["q"], '$(touch /tmp/unsafe)')

    def test_cli_failure_classification_and_graphql_partial_errors(self):
        for stdout, stderr, kind in [('', 'gh auth login', 'auth'), ('', 'rate limit exceeded', 'rate_limit'), ('', "API rate limit exceeded for 192.0.2.1. (But here's the good news: Authenticated requests get a higher rate limit.)", 'auth'), ('', 'connection refused', 'network'), ('{"data":{"nodes":[]},"errors":[{"message":"forbidden"}]}', '', 'api')]:
            with patch.object(h, "bounded_process", return_value=subprocess.CompletedProcess([], 1, stdout, stderr)):
                with self.assertRaises(h.FetchError) as raised:
                    h.GitHub().graphql("query {}")
                self.assertEqual(raised.exception.kind, kind)


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "cache"
        self.addCleanup(self.temp.cleanup)

    def initial(self):
        return h.run(ScriptedAPI({"viewer": {"login": "alice"}}, search_page([pr()]), {"nodes": [pr()]}), h.Cache(self.path))

    def test_cache_reuses_fetch_and_has_private_permissions(self):
        first = self.initial()
        second = h.run(ScriptedAPI(), h.Cache(self.path))
        self.assertEqual(first, second)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.path / "snapshot.json").stat().st_mode & 0o777, 0o600)

    def test_network_failure_retains_snapshot_and_backs_off_even_when_forced(self):
        original = self.initial()
        later = original["attemptedAt"] + 70
        result = h.run(ScriptedAPI(h.FetchError("offline", "network")), h.Cache(self.path), now=lambda: later)
        self.assertEqual(result["prs"], original["prs"])
        self.assertTrue(result["stale"])
        self.assertGreater(result["retryAt"], later)
        cached = h.run(ScriptedAPI(), h.Cache(self.path), force=True, now=lambda: later + 10)
        self.assertEqual(cached, result)

    def test_auth_failure_clears_private_data(self):
        original = self.initial()
        result = h.run(ScriptedAPI(h.FetchError("login", "auth")), h.Cache(self.path), now=lambda: original["attemptedAt"] + 70)
        self.assertEqual(result["prs"], [])
        self.assertNotIn("account", result)

    def test_account_switch_cannot_retain_other_accounts_prs(self):
        original = self.initial()
        result = h.run(ScriptedAPI({"viewer": {"login": "bob"}}, h.FetchError("offline")), h.Cache(self.path), now=lambda: original["attemptedAt"] + 70)
        self.assertEqual(result["prs"], [])

    def test_credential_change_invalidates_cached_response(self):
        self.initial()
        with patch.object(h, "auth_fingerprint", return_value="new-account"):
            result = h.run(ScriptedAPI(h.FetchError("offline")), h.Cache(self.path))
        self.assertEqual(result["prs"], [])

    def test_corrupt_cache_and_symlink_directory(self):
        cache = h.Cache(self.path)
        (self.path / "snapshot.json").write_text("bad-json")
        self.assertEqual(cache.read(), {})
        alias = self.path.parent / "alias"
        alias.symlink_to(self.path, target_is_directory=True)
        with self.assertRaises(h.FetchError):
            h.Cache(alias)

    def test_multiple_monitors_coalesce_manual_refresh(self):
        context = multiprocessing.get_context("fork")
        calls = context.Value("i", 0)
        processes = [context.Process(target=concurrent_refresh, args=(str(self.path), calls)) for _ in range(3)]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=5)
            self.assertEqual(process.exitcode, 0)
        self.assertEqual(calls.value, 6)
        self.assertEqual(h.Cache(self.path).read()["prs"], [])


class ProvenanceAndPrimingTests(unittest.TestCase):
    def test_repo_access_and_visibility_classification(self):
        for permission, access in (("ADMIN", "maintainer"), ("MAINTAIN", "maintainer"),
                                   ("WRITE", "contributor"), ("TRIAGE", "contributor"),
                                   ("READ", "contributor"), ("NONE", "contributor"), (None, "contributor")):
            repository = {"nameWithOwner": "example/project"}
            if permission is not None:
                repository["viewerPermission"] = permission
            self.assertEqual(h.repo_access(repository), access)
        self.assertEqual(h.repo_visibility({"isPrivate": True}), "private")
        self.assertEqual(h.repo_visibility({"isPrivate": False}), "public")
        self.assertEqual(h.repo_visibility({}), "public")

    def test_normalized_row_carries_access_and_visibility(self):
        source = pr()
        source["repository"].update(viewerPermission="MAINTAIN", isPrivate=True)
        row = h.normalize(ScriptedAPI(), source)
        self.assertEqual(row["access"], "maintainer")
        self.assertEqual(row["visibility"], "private")

    def test_fork_provenance_only_for_cross_repo_pull_requests(self):
        fork = h.normalize(ScriptedAPI(), dict(pr(), isCrossRepository=True, headRefName="fix-bug",
                                               headRepository={"nameWithOwner": "alice/project"}, baseRefName="main"))
        self.assertEqual(fork["fork"], {"repo": "alice/project", "branch": "fix-bug", "base": "example/project:main"})
        # A same-repo PR (push access to the base) carries no fork provenance.
        self.assertNotIn("fork", h.normalize(ScriptedAPI(), dict(pr(), isCrossRepository=False)))

    def test_validate_snapshot_accepts_provenance_and_rejects_malformed(self):
        base = h.normalize(ScriptedAPI(), dict(pr(), isCrossRepository=True, headRefName="b",
                                               headRepository={"nameWithOwner": "alice/project"}, baseRefName="main"))
        h.validate_snapshot({"schemaVersion": 1, "prs": [copy.deepcopy(base)], "excluded": []})
        for bad in (dict(base, access="owner"), dict(base, visibility="secret"),
                    dict(base, fork={"repo": "a/b"}), dict(base, fork="alice/project"),
                    dict(base, fork={"repo": "a/b", "branch": "x", "base": "y", "extra": "z"})):
            with self.assertRaises(ValueError):
                h.validate_snapshot({"schemaVersion": 1, "prs": [bad], "excluded": []})

    def test_session_prompt_primes_fork_followup_but_stays_read_only_for_checks(self):
        track = {"url": "https://github.com/example/project/pull/7",
                 "after": [], "packages": [], "note": "", "workaround": ""}
        prompt = h.session_prompt(track)
        self.assertIn("pushing to that fork's branch", prompt)
        self.assertIn("Never push to the base repository", prompt)
        # A summary-only follow-up delegates to the read-only investigation prompt, no push guidance.
        summary = dict(track, summaryOnly=True)
        self.assertEqual(h.session_prompt(summary), h.summary_prompt(summary))
        self.assertNotIn("fork", h.session_prompt(summary))
        # The background status check is unchanged and strictly read-only.
        self.assertIn("Do not comment, edit, or change anything", h.status_prompt(track))


if __name__ == "__main__":
    unittest.main()
