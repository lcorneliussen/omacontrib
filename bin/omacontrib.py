#!/usr/bin/env python3
"""Read-only contribution lifecycle monitor. stdout is versioned JSON."""

import argparse
import calendar
import copy
import fcntl
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import selectors
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time
from urllib.parse import quote


REFRESH_SECONDS = 60
REQUEST_SECONDS = 35
MAX_PRS = 100
MAX_SUBMISSIONS = 25
MAX_MERGED = 20
MAX_CLOSED = 20
MAX_ISSUES = 30
CLOSED_STICKY_DAYS = 30
MAX_TRACKS = 20
MAX_REQUIREMENTS = 5
MAX_SESSIONS = 5
AGENT_SECONDS = 600
# Summary-only entries: everything asked about or summarized automatically, not declared for landing.
MAX_SUMMARIES = 60
# Automatic summaries: a change must hold this long (CI settles, comments come in bursts),
# one agent at a time, and at most this many runs a day.
AUTO_SETTLE = 600
AUTO_DAILY = 24
WAITING = ("me", "them", "nobody")
MAX_ROWS = MAX_PRS + MAX_SUBMISSIONS + MAX_MERGED + MAX_CLOSED + MAX_ISSUES + MAX_TRACKS
LIFECYCLE_REFRESH_SECONDS = 3600
MAX_EXCLUSIONS = 200
MAX_GROUPS = 20
MAX_SEARCH_PAGES = 10
MAX_DETAIL_PAGES = 5
MAX_ENTRIES = 500
MAX_STDOUT_BYTES = 8 * 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
MAX_TEXT = 512
MAX_STATUS = 64
MAX_NAME = 256
MAX_REFERENCE = 1024
BUCKETS = ("running", "success", "skipped", "failed", "unknown")
NOTIFICATION_ICON = Path(__file__).resolve().parent.parent / "assets/git-pull-request.svg"

PAGE = "pageInfo { hasNextPage endCursor }"
BASIC = """id number title url state isDraft updatedAt
author { login } repository { nameWithOwner }"""
CHECKS = """nodes { __typename
 ... on CheckRun { id name status conclusion databaseId
   checkSuite { id app { id } workflowRun { workflow { id } } }
 }
 ... on StatusContext { id context state }
} """ + PAGE
REVIEWS = "nodes { id state comments { totalCount } } " + PAGE
DETAIL = BASIC + """
reviewDecision mergeable mergeStateStatus comments { totalCount }
stackEntry { position stack { number size } }
reviews(first:100) { """ + REVIEWS + """ }
commits(last:1) { nodes { commit { oid statusCheckRollup {
 contexts(first:100) { """ + CHECKS + """ }
} } } }
"""
SEARCH = """query($q:String!, $cursor:String) {
 search(query:$q, type:ISSUE, first:100, after:$cursor) {
 issueCount nodes { ... on PullRequest { """ + BASIC + " } } " + PAGE + " } }"
DETAILS = "query($ids:[ID!]!) { nodes(ids:$ids) { ... on PullRequest { " + DETAIL + " } } }"
SUBMISSION_SEARCH = """query($q:String!, $cursor:String) {
 search(query:$q, type:ISSUE, first:25, after:$cursor) {
  issueCount nodes { ... on Issue {
   id number title url state updatedAt author { login }
   repository { nameWithOwner }
   labels(first:30) { nodes { name } }
   comments { totalCount }
  } } """ + PAGE + " } }"

MARKETPLACE_REPOSITORY = "omacom/omarchy-plugin-marketplace"
MERGED_SEARCH = """query($q:String!) {
 search(query:$q, type:ISSUE, first:20) {
  nodes { ... on PullRequest {
   id number title url state updatedAt mergedAt author { login }
   mergeCommit { oid }
   repository { nameWithOwner latestRelease { tagName publishedAt url } }
  } }
 } }"""
CLOSED_SEARCH = """query($q:String!) {
 search(query:$q, type:ISSUE, first:100) {
  nodes { ... on PullRequest { """ + BASIC + """ closedAt } }
 } }"""
ISSUE_SEARCH = """query($q:String!) {
 search(query:$q, type:ISSUE, first:100) {
  nodes { ... on Issue { """ + BASIC.replace(" isDraft", "") + """ closedAt stateReason comments { totalCount } } }
 } }"""
ISSUE_CLOSED = {"COMPLETED": "Closed · completed", "NOT_PLANNED": "Closed · not planned", "DUPLICATE": "Closed · duplicate"}
TRACKED = BASIC.replace(" isDraft", "")
TRACKED_RESOURCE = """resource(url:$u%d) { __typename
 ... on PullRequest { """ + TRACKED + """ }
 ... on Issue { """ + TRACKED + """ stateReason }
}"""
# Discussions are not UniformResourceLocatable, so resource(url:) cannot reach them.
TRACKED_DISCUSSION = """repository(owner:$o%d, name:$n%d) { discussion(number:$d%d) { __typename
 """ + TRACKED.replace(" state", "") + """ closed stateReason isAnswered } }"""
SUBMISSION_STATUS = (
    "needs-fixes", "security-review-required", "approved-and-verified",
    "listed", "validated", "plugin-update", "submission",
)
GITHUB_OWNER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
GITHUB_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
GITHUB_ITEM = re.compile(r"https://github\.com/([^/]+)/([^/]+)/(pull|issues|discussions)/([0-9]+)")
PACKAGE_NAME = re.compile(r"[a-z0-9@_+][a-z0-9@._+-]{0,99}")
PACKAGE_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+_~:-]{0,63}")
HOST_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,62}")
SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
REVISION = re.compile(r"[0-9a-f]{12}")
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
AGENTS = {"claude": ("claude", "--resume"), "codex": ("codex", "resume")}
# Read-only tools for the background status check: it reports, it never acts.
STATUS_TOOLS = ("Bash(gh pr view:*)", "Bash(gh issue view:*)", "Bash(gh release list:*)",
                "Bash(gh release view:*)", "Bash(gh search:*)", "Bash(pacman -Q:*)", "Bash(pacman -Si:*)",
                "Bash(git ls-remote:*)",
                "WebFetch(domain:github.com)")
PACKAGE_REQUIREMENT = re.compile(r"([^<>=\s]+)\s*(?:(>=|>)\s*([^<>=\s]+))?")


class FetchError(Exception):
    def __init__(self, message, kind="api", retry_after=0):
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after


class LimitError(FetchError):
    def __init__(self, message):
        super().__init__(message, "limit")


class Deadline:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.end = clock() + REFRESH_SECONDS

    def remaining(self):
        remaining = self.end - self.clock()
        if remaining <= 0:
            raise FetchError("Refresh time limit reached; results are incomplete.", "deadline")
        return remaining


class BudgetAPI:
    def __init__(self, api, deadline):
        self.api, self.deadline = api, deadline

    def graphql(self, query, **variables):
        try:
            result = self.api.graphql(query, timeout=min(REQUEST_SECONDS, self.deadline.remaining()), **variables)
        except FetchError as exc:
            if exc.kind not in {"auth", "rate_limit"}:
                self.deadline.remaining()
            raise
        self.deadline.remaining()
        return result

    def rest(self, endpoint):
        result = self.api.rest(endpoint, timeout=min(REQUEST_SECONDS, self.deadline.remaining()))
        self.deadline.remaining()
        return result


def budget_api(api):
    return api if isinstance(api, BudgetAPI) else BudgetAPI(api, Deadline())


def bounded_process(command, request, timeout, env):
    """Drain both pipes without ever buffering unlimited CLI output."""
    end = time.monotonic() + timeout
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, env=env) as proc:
        try:
            # Include stdin in the deadline: even a child that never reads must not block us.
            pending = memoryview(request.encode("utf-8"))
            output = {"stdout": bytearray(), "stderr": bytearray()}
            with selectors.DefaultSelector() as selector:
                for stream, name in ((proc.stdin, "stdin"), (proc.stdout, "stdout"), (proc.stderr, "stderr")):
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_WRITE if name == "stdin" else selectors.EVENT_READ, name)
                while selector.get_map():
                    remaining = end - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, timeout)
                    for key, _ in selector.select(remaining):
                        if key.data == "stdin":
                            try:
                                sent = os.write(key.fd, pending[:4096])
                                pending = pending[sent:]
                            except BrokenPipeError:
                                pending = pending[:0]
                            if not pending:
                                selector.unregister(key.fileobj)
                                key.fileobj.close()
                            continue
                        limit = MAX_STDOUT_BYTES if key.data == "stdout" else MAX_STDERR_BYTES
                        buffer = output[key.data]
                        chunk = os.read(key.fd, min(65536, limit + 1 - len(buffer)))
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        buffer.extend(chunk)
                        if len(buffer) > limit:
                            raise LimitError("GitHub response size limit reached; results are incomplete.")
            proc.wait(timeout=max(0, end - time.monotonic()))
            return subprocess.CompletedProcess(command, proc.returncode,
                                               output["stdout"].decode("utf-8", errors="replace"),
                                               output["stderr"].decode("utf-8", errors="replace"))
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()


class GitHub:
    def __init__(self, token=None):
        self.token = token

    def environment(self):
        env = dict(os.environ, GH_PROMPT_DISABLED="1", GH_HOST="github.com")
        if self.token:
            env["GH_TOKEN"] = self.token
            env.pop("GITHUB_TOKEN", None)
        return env

    def fingerprint(self):
        digest = hashlib.sha256(auth_fingerprint().encode())
        digest.update((self.token or "").encode())
        return digest.hexdigest()

    def graphql(self, query, timeout=REQUEST_SECONDS, **variables):
        env = self.environment()
        try:
            proc = bounded_process(
                ["gh", "api", "--hostname", "github.com", "graphql", "--input", "-"],
                json.dumps({"query": query, "variables": variables}), timeout, env,
            )
        except FileNotFoundError as exc:
            raise FetchError("Install GitHub CLI (gh), then run gh auth login.", "auth") from exc
        except subprocess.TimeoutExpired as exc:
            raise FetchError("GitHub request timed out. Retrying automatically.", "network") from exc
        try:
            payload = json.loads(proc.stdout) if proc.stdout else {}
        except (ValueError, RecursionError) as exc:
            raise FetchError("GitHub returned an invalid response.") from exc
        if not isinstance(payload, dict):
            raise FetchError("GitHub returned an invalid response.")
        if proc.returncode or payload.get("errors"):
            # Only classify stderr: do not copy arbitrary CLI output or credentials to the UI/cache.
            message = (proc.stderr + json.dumps(payload.get("errors", []))).lower()
            if any(word in message for word in ("rate limit", "rate_limit", "rate_limited", "abuse")):
                raise FetchError("GitHub rate limit reached. Retrying in 15 minutes.", "rate_limit", 900)
            if any(word in message for word in ("auth login", "bad credentials", "401", "authentication", "token has expired")):
                raise FetchError("Sign in with gh auth login --hostname github.com.", "auth")
            if any(word in message for word in ("resolve", "connection", "network", "timeout")):
                raise FetchError("Cannot reach GitHub. Check your connection.", "network")
            raise FetchError("GitHub could not return PR details. Check account access and retry.")
        if not isinstance(payload.get("data"), dict):
            raise FetchError("GitHub returned no data.")
        return payload["data"]

    def rest(self, endpoint, timeout=REQUEST_SECONDS):
        env = self.environment()
        try:
            proc = bounded_process(
                ["gh", "api", "--hostname", "github.com", "--method", "GET", endpoint],
                "", timeout, env,
            )
        except FileNotFoundError as exc:
            raise FetchError("Install GitHub CLI (gh), then run gh auth login.", "auth") from exc
        except subprocess.TimeoutExpired as exc:
            raise FetchError("GitHub request timed out. Retrying automatically.", "network") from exc
        try:
            payload = json.loads(proc.stdout) if proc.stdout else {}
        except (ValueError, RecursionError) as exc:
            raise FetchError("GitHub returned an invalid response.") from exc
        if proc.returncode or not isinstance(payload, dict):
            raise FetchError("GitHub could not return release details.")
        return payload


def next_cursor(connection, previous=None):
    info = connection["pageInfo"]
    if not info["hasNextPage"]:
        return None
    cursor = reference(info["endCursor"])
    if not cursor or cursor == previous:
        raise FetchError("GitHub pagination did not advance; results are incomplete.")
    return cursor


def reference(value, limit=MAX_REFERENCE):
    if not isinstance(value, str) or not value or len(value) > limit:
        raise LimitError("GitHub identifier or URL exceeds the supported limit.")
    return value


def display_text(value, limit=MAX_TEXT):
    if not isinstance(value, str):
        raise FetchError("Incomplete GitHub response.")
    return value if len(value) <= limit else value[:limit - 1] + "…"


def source_record(pr):
    """Keep only bounded fields needed for discovery and stale fallbacks."""
    return {
        "id": reference(pr["id"]), "number": integer(pr["number"]),
        "title": display_text(pr["title"]), "url": reference(pr["url"]),
        "repository": {"nameWithOwner": reference(pr["repository"]["nameWithOwner"], MAX_NAME)},
        "author": {"login": reference(pr["author"]["login"], MAX_NAME)},
        "updatedAt": reference(pr["updatedAt"], MAX_STATUS),
        "state": reference(pr["state"], MAX_STATUS), "isDraft": boolean(pr["isDraft"]),
    }


def contribution_groups(settings):
    if "groups" in settings:
        return settings["groups"]
    return [
        {"id": "inbox", "name": "Active", "count": True, "items": [], "repositories": [], "owners": []},
        {"id": "excluded", "name": "Excluded", "count": False,
         "items": [], "repositories": settings.get("repositories", []), "owners": settings.get("owners", [])},
    ]


def group_for(repository, url, groups):
    repository_key = repository.lower()
    owner = repository.split("/", 1)[0].lower()
    for group in groups:
        if url in group["items"]:
            return group, "item"
    for group in groups:
        if repository_key in group["repositories"]:
            return group, "repository"
    for group in groups:
        if owner in group["owners"]:
            return group, "owner"
    return groups[0], "default"


def excluded_by(repository, exclusions):
    owner = repository.split("/", 1)[0].lower()
    reasons = []
    if repository.lower() in exclusions["repositories"]:
        reasons.append("repository")
    if owner in exclusions["owners"]:
        reasons.append("owner")
    return reasons


def discover(api, login, exclusions=None):
    api = budget_api(api)
    exclusions = exclusions or {"repositories": [], "owners": []}
    reference(login, MAX_NAME)
    cursor, found, total, pages, reason = None, {}, 0, 0, ""
    while True:
        try:
            page = api.graphql(SEARCH, q=f"is:pr is:open author:{login} sort:updated-desc", cursor=cursor)["search"]
        except FetchError as exc:
            if not found or exc.kind != "deadline":
                raise
            reason = str(exc)
            break
        total = max(total, integer(page["issueCount"]))
        pages += 1
        nodes = page["nodes"]
        if not isinstance(nodes, list):
            raise FetchError("Incomplete GitHub response.")
        if len(nodes) > 100:
            reason = "GitHub search page entry limit reached; this list is incomplete."
        for pr in nodes[:100]:
            if pr and pr.get("state") == "OPEN" and (pr.get("author") or {}).get("login", "").lower() == login.lower():
                try:
                    source = source_record(pr)
                except (FetchError, KeyError, TypeError, ValueError):
                    reason = "Some PRs have invalid or oversized fields; this list is incomplete."
                    continue
                source["kind"] = "pull_request"
                found[source["id"]] = source
                if len(found) >= MAX_PRS:
                    break
        cursor = next_cursor(page, cursor)
        if cursor is None or pages >= MAX_SEARCH_PAGES or len(found) >= MAX_PRS:
            break
    if total > MAX_PRS or cursor is not None:
        reason = reason or "PR discovery limit reached (100 PRs / 10 pages); this list is incomplete."
    return sorted(found.values(), key=lambda pr: pr["updatedAt"], reverse=True), reason


def submission_status(labels, state):
    names = {label.lower() for label in labels}
    if "listed" in names:
        return "Listed"
    if "approved-and-verified" in names:
        return "Approved"
    if "needs-fixes" in names:
        return "Needs fixes"
    if "security-review-required" in names:
        return "Security review"
    if "validated" in names:
        return "Validated"
    if "plugin-update" in names:
        return "Update review"
    return "Open" if state == "OPEN" else "Closed"


def discover_submissions(api, login):
    """Return the account's own plugin/theme marketplace submissions."""
    api = budget_api(api)
    query = f"repo:{MARKETPLACE_REPOSITORY} is:issue author:{login} sort:updated-desc"
    page = api.graphql(SUBMISSION_SEARCH, q=query, cursor=None)["search"]
    rows = []
    for issue in page.get("nodes", [])[:MAX_SUBMISSIONS]:
        if not issue or (issue.get("author") or {}).get("login", "").lower() != login.lower():
            continue
        labels = [display_text(label["name"], MAX_STATUS) for label in issue["labels"]["nodes"]]
        status = submission_status(labels, issue["state"])
        rows.append({
            "id": "submission:" + reference(issue["id"]),
            "number": integer(issue["number"]),
            "title": display_text(issue["title"]),
            "url": reference(issue["url"]),
            "repository": MARKETPLACE_REPOSITORY,
            "author": reference(issue["author"]["login"], MAX_NAME),
            "updatedAt": reference(issue["updatedAt"], MAX_STATUS),
            "draft": False,
            "review": status,
            "discussion": integer(issue["comments"]["totalCount"]),
            "inline": 0,
            "checks": [],
            "counts": dict.fromkeys(BUCKETS, 0),
            "error": None,
            "fetchedAt": time.time(),
            "kind": "submission",
            "status": status,
            "labels": labels,
            "mergeable": "UNKNOWN",
            "mergeStateStatus": "UNKNOWN",
        })
    return rows


def discover_merged(api, login, exclusions=None):
    """Find recent external merges and prove inclusion in the latest release tag."""
    api = budget_api(api)
    exclusions = exclusions or {"repositories": [], "owners": []}
    since = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 365 * 86400))
    query = f"is:pr is:merged author:{login} merged:>={since} sort:updated-desc"
    nodes = api.graphql(MERGED_SEARCH, q=query)["search"]["nodes"]
    rows = []
    for pr in nodes[:MAX_MERGED]:
        if not pr or not pr.get("mergedAt") or not pr.get("mergeCommit"):
            continue
        repository = reference(pr["repository"]["nameWithOwner"], MAX_NAME)
        release = pr["repository"].get("latestRelease")
        status, release_url = "Merged · awaiting release", None
        if release and release.get("tagName") and release.get("publishedAt"):
            endpoint = "repos/{}/compare/{}...{}".format(
                repository,
                quote(reference(pr["mergeCommit"]["oid"]), safe=""),
                quote(reference(release["tagName"], MAX_NAME), safe=""),
            )
            comparison = api.rest(endpoint)
            if comparison.get("status") in {"ahead", "identical"}:
                prefix = "Shipped in Omarchy " if repository.lower() == "omacom/omarchy" else "Released in "
                status = prefix + display_text(release["tagName"], MAX_STATUS - len(prefix))
                release_url = reference(release["url"])
        rows.append({
            "id": "merged:" + reference(pr["id"]),
            "number": integer(pr["number"]), "title": display_text(pr["title"]),
            "url": reference(pr["url"]), "repository": repository,
            "author": reference(pr["author"]["login"], MAX_NAME),
            "updatedAt": reference((release or {}).get("publishedAt") or pr["mergedAt"], MAX_STATUS),
            "draft": False, "review": status, "discussion": 0, "inline": 0,
            "checks": [], "counts": dict.fromkeys(BUCKETS, 0), "error": None,
            "fetchedAt": time.time(), "kind": "merged", "status": status,
            "labels": [], "mergeable": "UNKNOWN", "mergeStateStatus": "UNKNOWN",
            "releaseUrl": release_url,
        })
    return rows


def lifecycle_row(kind, item, status):
    """A contribution with no live review or check state of its own."""
    return {
        "id": kind + ":" + reference(item["id"]),
        "number": integer(item["number"]), "title": display_text(item["title"]),
        "url": reference(item["url"]),
        "repository": reference(item["repository"]["nameWithOwner"], MAX_NAME),
        "author": reference((item.get("author") or {}).get("login") or "ghost", MAX_NAME),
        "updatedAt": reference(item["updatedAt"], MAX_STATUS),
        "draft": False, "review": status, "discussion": 0, "inline": 0,
        "checks": [], "counts": dict.fromkeys(BUCKETS, 0), "error": None,
        "fetchedAt": time.time(), "kind": kind, "status": status,
        "labels": [], "mergeable": "UNKNOWN", "mergeStateStatus": "UNKNOWN",
    }


def discover_closed(api, login, groups=None):
    """Keep PRs closed without a GitHub merge visible: maintainers often apply them by hand."""
    api = budget_api(api)
    groups = groups or contribution_groups({})
    since = time.strftime("%Y-%m-%d", time.gmtime(time.time() - CLOSED_STICKY_DAYS * 86400))
    # Closing updates a PR, and GitHub indexes closed: dates hours behind updated: dates.
    query = f"is:pr is:closed is:unmerged author:{login} updated:>={since} sort:updated-desc"
    nodes = api.graphql(CLOSED_SEARCH, q=query)["search"]["nodes"]
    rows = [lifecycle_row("closed", pr, "Closed · not merged") for pr in nodes[:100]
            if pr and pr.get("state") == "CLOSED" and (pr.get("closedAt") or "")[:10] >= since]
    # Closed experiments in uncounted groups must not crowd out the ones that matter.
    rows.sort(key=lambda row: not group_for(row["repository"], row["url"], groups)[0]["count"])
    return rows[:MAX_CLOSED]


def discover_issues(api, login, groups=None):
    """Issues the account opened elsewhere: open ones, and closed ones for as long as closed PRs stay.

    Issues in the account's own repositories are its to-do list, not contributions.
    """
    api = budget_api(api)
    groups = groups or contribution_groups({})
    since = time.strftime("%Y-%m-%d", time.gmtime(time.time() - CLOSED_STICKY_DAYS * 86400))
    query = f"is:issue author:{login} -user:{login} -repo:{MARKETPLACE_REPOSITORY} sort:updated-desc"
    nodes = api.graphql(ISSUE_SEARCH, q=query)["search"]["nodes"]
    rows = []
    for issue in nodes[:100]:
        if not issue or (issue.get("author") or {}).get("login", "").lower() != login.lower():
            continue
        closed = issue.get("state") == "CLOSED"
        if closed and (issue.get("closedAt") or "")[:10] < since:
            continue
        status = ISSUE_CLOSED.get(issue.get("stateReason"), "Closed") if closed else "Open issue"
        row = lifecycle_row("issue", issue, status)
        row.update(review="Issue", discussion=integer(issue["comments"]["totalCount"]), closed=closed)
        rows.append(row)
    # Issues in uncounted groups must not crowd out the ones that matter; then open before closed.
    rows.sort(key=lambda row: (not group_for(row["repository"], row["url"], groups)[0]["count"], row["closed"]))
    return rows[:MAX_ISSUES]


def installed_version(name, timeout=5):
    """The locally installed package version, or None when it cannot be established."""
    try:
        proc = bounded_process(["pacman", "-Q", "--", name], "", timeout, dict(os.environ, LC_ALL="C"))
    except (OSError, subprocess.TimeoutExpired, FetchError):
        return None
    fields = proc.stdout.split()
    if proc.returncode or len(fields) != 2 or fields[0] != name or not PACKAGE_VERSION.fullmatch(fields[1]):
        return None
    return fields[1]


def compare_versions(left, right, timeout=5):
    """pacman's own ordering; None when it is unavailable, so nothing is guessed."""
    try:
        proc = bounded_process(["vercmp", left, right], "", timeout, dict(os.environ, LC_ALL="C"))
        return int(proc.stdout.strip()) if not proc.returncode else None
    except (OSError, subprocess.TimeoutExpired, FetchError, ValueError):
        return None


def parse_requirement(text):
    """NAME, NAME>VERSION or NAME>=VERSION. A bare name means newer than what is installed now."""
    match = PACKAGE_REQUIREMENT.fullmatch(text.strip())
    if not match:
        raise ValueError("Package requirements look like NAME, NAME>VERSION or NAME>=VERSION")
    name, operator, version = match.groups()
    if not operator:
        installed = installed_version(name)
        if not installed:
            raise ValueError(f"{name} is not installed; give the version that will carry the fix")
        # A rebuild of the same upstream version must not count as the fix arriving.
        operator, version = ">", installed.rsplit("-", 1)[0]
    return {"name": name, "operator": operator, "version": version}


def package_step(requirement):
    name, operator, wanted = requirement["name"], requirement["operator"], requirement["version"]
    installed = installed_version(name)
    order = None
    if installed:
        order = compare_versions(installed if "-" in wanted else installed.rsplit("-", 1)[0], wanted)
    awaiting = f"{name} {operator} {wanted}"
    if order is not None and order >= (1 if operator == ">" else 0):
        return {"kind": "package", "label": display_text(f"{name} {installed} installed", MAX_NAME), "done": True}, awaiting
    found = "cannot compare versions" if installed and order is None else "installed " + installed if installed else "not installed"
    return {"kind": "package", "label": display_text(f"{awaiting} · {found}", MAX_NAME), "done": False}, awaiting


def resource_step(item, source=False):
    """Closed counts as done: the declared requirements after it decide whether it landed."""
    if item.get("__typename") not in {"PullRequest", "Issue", "Discussion"}:
        raise FetchError("A tracked link is not an accessible issue, pull request or discussion.")
    name = "{}#{}".format(reference(item["repository"]["nameWithOwner"], MAX_NAME), integer(item["number"]))
    if item["__typename"] == "Discussion":
        # An accepted answer settles a Q&A discussion even while it stays open.
        state = "CLOSED" if item["closed"] else "ANSWERED" if item.get("isAnswered") else "OPEN"
        reason = item.get("stateReason") if state == "CLOSED" else None
        label = "Closed as " + reason.lower() if reason in {"OUTDATED", "DUPLICATE"} else "Resolved" if reason else state.capitalize()
    else:
        state = reference(item["state"], MAX_STATUS)
        if state == "CLOSED" and item.get("stateReason") == "NOT_PLANNED":
            label = "Closed as not planned"
        elif state == "CLOSED" and item["__typename"] == "PullRequest":
            label = "Closed · not merged"
        else:
            label = state.capitalize()
    if not source:
        label = name + " · " + label[0].lower() + label[1:]
    return {"kind": "github", "label": display_text(label, MAX_NAME), "done": state != "OPEN"}, name


def tracked_resources(api, tracks):
    urls = list(dict.fromkeys(url for track in tracks for url in [track["url"]] + track["after"]))
    parameters, fields, variables = [], [], {}
    for index, url in enumerate(urls):
        owner, name, section, number = GITHUB_ITEM.fullmatch(url).groups()
        if section == "discussions":
            parameters += [f"$o{index}:String!", f"$n{index}:String!", f"$d{index}:Int!"]
            fields.append(f"r{index}: " + TRACKED_DISCUSSION % (index, index, index))
            variables.update({f"o{index}": owner, f"n{index}": name, f"d{index}": int(number)})
        else:
            parameters.append(f"$u{index}:URI!")
            fields.append(f"r{index}: " + TRACKED_RESOURCE % index)
            variables[f"u{index}"] = url
    data = api.graphql("query({}) {{ {} }}".format(", ".join(parameters), " ".join(fields)), **variables)
    return {url: (data[f"r{index}"] or {}).get("discussion") or {} if GITHUB_ITEM.fullmatch(url)[3] == "discussions"
            else data[f"r{index}"] or {} for index, url in enumerate(urls)}


def apply_tracks(api, rows, tracks, previous_rows, refresh):
    """Attach declared landing requirements; add rows for items no other discovery found.

    GitHub state is refreshed with the hourly lifecycle pass; installed package
    versions are local and are read on every run.
    """
    if not tracks:
        return None
    api = budget_api(api)
    by_url = {row["url"]: row for row in rows}
    cached = {row["url"]: row for row in previous_rows if "landing" in row}
    # A cached row of another kind is a stale copy of a contribution that has since moved on.
    reusable = all(track["url"] in cached and (track["url"] in by_url or cached[track["url"]]["kind"] == "tracked")
                   for track in tracks)
    resources, failure = None, None
    if refresh or not reusable:
        try:
            resources = tracked_resources(api, tracks)
        except (FetchError, KeyError, TypeError, ValueError, AttributeError) as exc:
            failure = "Landing tracking is unavailable. " + (str(exc) if isinstance(exc, FetchError) else "Incomplete GitHub response.")
    for track in tracks:
        url, row, before = track["url"], by_url.get(track["url"]), cached.get(track["url"])
        try:
            if resources is not None:
                source = resource_step(resources[url], source=True)[0]
                if row is None:
                    row = lifecycle_row("tracked", resources[url], source["label"])
                    rows.append(row)
                elif row["kind"] not in {"pull_request", "issue"}:
                    source["label"] = row["landing"]["steps"][0]["label"] if "landing" in row else row["status"]
                pending = [(source, "")] + [resource_step(resources[item]) for item in track["after"]]
            elif before is not None:
                if row is None:
                    row = copy.deepcopy(before)
                    rows.append(row)
                kept = [step for step in before["landing"]["steps"] if step["kind"] == "github"]
                pending = [(step, step["label"].split(" · ")[0]) for step in kept]
            else:
                continue
        except (FetchError, KeyError, TypeError, ValueError, AttributeError):
            failure = failure or "Landing tracking is unavailable. A tracked link is not an accessible issue, pull request or discussion."
            continue
        pending += [package_step(requirement) for requirement in track["packages"]]
        steps = [step for step, _ in pending]
        landed = all(step["done"] for step in steps)
        row["landing"] = {"landed": landed, "steps": steps, "workaround": track["workaround"], "note": track["note"]}
        if row["kind"] != "pull_request":
            awaiting = next((name for step, name in pending[1:] if not step["done"]), "")
            status = "Landed" if landed else "Awaiting " + awaiting if steps[0]["done"] else steps[0]["label"]
            row["status"] = row["review"] = display_text(status, MAX_STATUS)
    return failure


def bucket(check):
    status = check.get("status") if check.get("__typename") == "CheckRun" else check.get("state")
    if status in {"QUEUED", "IN_PROGRESS", "PENDING", "WAITING", "REQUESTED", "EXPECTED"}:
        return "running"
    result = check.get("conclusion") if status == "COMPLETED" else status
    if result == "SUCCESS":
        return "success"
    if result in {"SKIPPED", "NEUTRAL", "CANCELLED"}:
        return "skipped"
    if result in {"FAILURE", "ERROR", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "STALE"}:
        return "failed"
    return "unknown"


def basic_row(pr):
    kind = pr.get("kind", "pull_request")
    pr = source_record(pr)
    return {
        "id": pr["id"], "number": pr["number"], "title": pr["title"], "url": pr["url"],
        "repository": pr["repository"]["nameWithOwner"], "author": pr["author"]["login"],
        "updatedAt": pr["updatedAt"], "draft": pr["isDraft"], "review": "Unknown",
        "discussion": None, "inline": None, "checks": None, "counts": None,
        "error": "Details unavailable", "fetchedAt": None,
        "kind": kind,
    }


def review_pages(api, pr):
    api = budget_api(api)
    connection, seen, count, cursor = pr["reviews"], set(), 0, None
    pages, entries = 0, 0
    while True:
        pages += 1
        nodes = connection["nodes"]
        if not isinstance(nodes, list) or len(nodes) > 100 or entries + len(nodes) > MAX_ENTRIES:
            raise LimitError("Review entry limit reached; inline count is unavailable.")
        entries += len(nodes)
        for review in nodes:
            identifier = reference(review["id"])
            reference(review["state"], MAX_STATUS)
            if identifier not in seen and review["state"] != "PENDING":
                # Every inline reply belongs to a review; totalCount avoids downloading bodies.
                count += integer(review["comments"]["totalCount"])
                seen.add(identifier)
        cursor = next_cursor(connection, cursor)
        if cursor is None:
            return count
        if pages >= MAX_DETAIL_PAGES or entries >= MAX_ENTRIES:
            raise LimitError("Review pagination limit reached; inline count is unavailable.")
        data = api.graphql("query($id:ID!, $cursor:String!) { node(id:$id) { ... on PullRequest { reviews(first:100, after:$cursor) { " + REVIEWS + " } } } }", id=pr["id"], cursor=cursor)
        connection = data["node"]["reviews"]


def check_identity(check):
    """Group attempts by workflow and full job name, never by display text."""
    identifier = reference(check["id"])
    suite = check.get("checkSuite") or {}
    workflow = (suite.get("workflowRun") or {}).get("workflow") or {}
    run_id = check.get("databaseId")
    if check.get("__typename") != "CheckRun" or not isinstance(run_id, int) or run_id <= 0:
        return identifier, 0
    # Separate workflows/apps can legitimately use the same job name. Without
    # workflow metadata only coalesce retries within the same check suite.
    scope = workflow.get("id") or suite.get("id")
    if not scope:
        return identifier, 0
    return ("CheckRun", (suite.get("app") or {}).get("id"), scope, check["name"]), run_id


def check_pages(api, pr):
    api = budget_api(api)
    commits = pr["commits"]["nodes"]
    if not commits:
        raise FetchError("PR head commit is unavailable.")
    commit = commits[-1]["commit"]
    reference(commit["oid"])
    rollup = commit["statusCheckRollup"]
    if rollup is None:
        return []
    connection, found, cursor = rollup["contexts"], {}, None
    latest = {}
    pages, entries = 0, 0
    while True:
        pages += 1
        nodes = connection["nodes"]
        if not isinstance(nodes, list) or len(nodes) > 100 or entries + len(nodes) > MAX_ENTRIES:
            raise LimitError("Check entry limit reached; checks are unavailable.")
        entries += len(nodes)
        for check in nodes:
            if check:
                identifier, run_id = check_identity(check)
                if run_id < latest.get(identifier, -1):
                    continue
                latest[identifier] = run_id
                name = check.get("name", check.get("context", "Check"))
                status = check.get("conclusion") or check.get("status") or check.get("state") or "UNKNOWN"
                found[identifier] = {
                    "name": display_text(name),
                    "status": display_text(status, MAX_STATUS),
                    "bucket": bucket(check),
                    "_truncated": len(name) > MAX_TEXT or len(status) > MAX_STATUS,
                }
        cursor = next_cursor(connection, cursor)
        if cursor is None:
            return list(found.values())
        if pages >= MAX_DETAIL_PAGES or entries >= MAX_ENTRIES:
            raise LimitError("Check pagination limit reached; checks are unavailable.")
        # Pin subsequent pages to the original commit, even if a push races this fetch.
        owner, name = pr["repository"]["nameWithOwner"].split("/", 1)
        data = api.graphql("query($owner:String!, $name:String!, $oid:GitObjectID!, $cursor:String!) { repository(owner:$owner,name:$name) { object(oid:$oid) { ... on Commit { statusCheckRollup { contexts(first:100,after:$cursor) { " + CHECKS + " } } } } } }", owner=owner, name=name, oid=commit["oid"], cursor=cursor)
        connection = data["repository"]["object"]["statusCheckRollup"]["contexts"]


def stack_info(entry):
    if entry is None:
        return None
    stack = entry["stack"]
    result = {"number": integer(stack["number"]), "size": integer(stack["size"]),
              "position": integer(entry["position"])}
    if not result["number"] or not 1 <= result["position"] <= result["size"]:
        raise ValueError("Invalid stack position")
    return result


def group_stacks(rows):
    """Newest group first; GitHub's bottom-to-top positions within each stack."""
    groups = {}
    for row in sorted(rows, key=lambda row: row["updatedAt"], reverse=True):
        stack = row.get("stack")
        key = (row["repository"], stack["number"]) if stack else (row["id"],)
        groups.setdefault(key, []).append(row)
    return [row for group in groups.values()
            for row in sorted(group, key=lambda row: (row.get("stack") or {}).get("position", 0))]


def normalize(api, pr):
    api = budget_api(api)
    row = basic_row(pr)
    checks = check_pages(api, pr)
    truncated = len(pr["title"]) > MAX_TEXT
    for check in checks:
        truncated = check.pop("_truncated", False) or truncated
    counts = dict.fromkeys(BUCKETS, 0)
    for check in checks:
        counts[check["bucket"]] += 1
    row.update(
        review={"APPROVED": "Approved", "CHANGES_REQUESTED": "Changes requested", "REVIEW_REQUIRED": "Review required", None: "No review decision"}.get(pr["reviewDecision"], "Unknown"),
        stack=stack_info(pr.get("stackEntry")),
        mergeable=reference(pr.get("mergeable", "UNKNOWN"), MAX_STATUS),
        mergeStateStatus=reference(pr.get("mergeStateStatus", "UNKNOWN"), MAX_STATUS),
        discussion=integer(pr["comments"]["totalCount"]), inline=review_pages(api, pr),
        checks=checks, counts=counts, error="Display text shortened to the supported limit." if truncated else None, fetchedAt=time.time(),
    )
    row["kind"] = "pull_request"
    return row


def snapshot(api, login, previous, exclusions=None):
    api = budget_api(api)
    exclusions = exclusions or {"repositories": [], "owners": []}
    groups = contribution_groups(exclusions)
    previous = safe_previous(previous)
    discovered, truncated = discover(api, login, exclusions)
    rows, errors = [], []
    previous_rows = previous.get("prs", []) + previous.get("excluded", [])
    old = {pr["id"]: pr for pr in previous_rows}
    retry, stopped = 0, None
    for start in range(0, len(discovered), 20):
        batch = discovered[start:start + 20]
        batch_error = None
        try:
            if stopped:
                raise stopped
            nodes = api.graphql(DETAILS, ids=[pr["id"] for pr in batch])["nodes"]
            if not isinstance(nodes, list) or len(nodes) > len(batch):
                raise LimitError("PR detail response entry limit reached.")
            details = {reference(pr["id"]): pr for pr in nodes if pr}
        except FetchError as exc:
            details, batch_error = {}, exc
            if exc.kind == "deadline":
                stopped = exc
        except (KeyError, TypeError, ValueError, AttributeError):
            details, batch_error = {}, FetchError("Incomplete GitHub response.")
        for source in batch:
            try:
                if stopped:
                    raise stopped
                if retry:
                    raise FetchError("Refresh paused: GitHub rate limit reached.", "rate_limit", retry)
                if batch_error:
                    raise batch_error
                pr = details.get(source["id"])
                if not pr:
                    raise FetchError("PR details are unavailable.")
                if pr["state"] != "OPEN" or (pr.get("author") or {}).get("login", "").lower() != login.lower():
                    continue
                row = normalize(api, pr)
                rows.append(row)
                if row["error"]:
                    errors.append(row["error"])
            except (FetchError, KeyError, TypeError, ValueError, AttributeError) as exc:
                if isinstance(exc, FetchError) and exc.kind == "auth":
                    raise exc
                if isinstance(exc, FetchError) and exc.kind == "deadline":
                    stopped = exc
                row = copy.deepcopy(old.get(source["id"], basic_row(source)))
                row.update({key: value for key, value in basic_row(source).items() if key in {"title", "draft", "updatedAt", "url"}})
                row["error"] = str(exc) if isinstance(exc, FetchError) else "Incomplete GitHub response."
                rows.append(row)
                errors.append(row["error"])
                retry = max(retry, getattr(exc, "retry_after", 0))
        if retry:
            # Stop requesting after a rate limit; retain explicit unknown/stale rows for the remainder.
            for source in discovered[start + 20:]:
                row = copy.deepcopy(old.get(source["id"], basic_row(source)))
                row["error"] = "Refresh paused: GitHub rate limit reached."
                rows.append(row)
            break
    now = time.time()
    lifecycle_checked = previous.get("lifecycleCheckedAt", 0)
    refresh_lifecycle = not 0 <= now - lifecycle_checked < LIFECYCLE_REFRESH_SECONDS
    if not refresh_lifecycle:
        rows.extend(copy.deepcopy(row) for row in previous_rows if row.get("kind") == "merged")
    else:
        try:
            rows.extend(discover_merged(api, login, exclusions))
            lifecycle_checked = now
        except (FetchError, KeyError, TypeError, ValueError, AttributeError) as exc:
            rows.extend(copy.deepcopy(row) for row in previous_rows if row.get("kind") == "merged")
            errors.append("Release tracking is unavailable. " + (str(exc) if isinstance(exc, FetchError) else "Incomplete GitHub response."))
    try:
        rows.extend(discover_submissions(api, login))
    except (FetchError, KeyError, TypeError, ValueError, AttributeError) as exc:
        errors.append("Marketplace submissions are unavailable. " + (str(exc) if isinstance(exc, FetchError) else "Incomplete GitHub response."))
    try:
        rows.extend(discover_issues(api, login, groups))
    except (FetchError, KeyError, TypeError, ValueError, AttributeError) as exc:
        rows.extend(copy.deepcopy(row) for row in previous_rows if row.get("kind") == "issue")
        errors.append("Issues are unavailable. " + (str(exc) if isinstance(exc, FetchError) else "Incomplete GitHub response."))
    try:
        if not refresh_lifecycle:
            raise LookupError
        rows.extend(discover_closed(api, login, groups))
    except (LookupError, FetchError, KeyError, TypeError, ValueError, AttributeError) as exc:
        rows.extend(copy.deepcopy(row) for row in previous_rows if row.get("kind") == "closed")
        if not isinstance(exc, LookupError):
            errors.append("Closed pull requests are unavailable. " + (str(exc) if isinstance(exc, FetchError) else "Incomplete GitHub response."))
    tracks = [track for track in exclusions.get("tracks", []) if not track.get("summaryOnly")]
    failure = apply_tracks(api, rows, tracks, previous_rows, refresh_lifecycle)
    if failure:
        errors.append(failure)
    error = "Some contribution details are unavailable. " + errors[0] if errors else ""
    if truncated:
        error = truncated + " " + error
    visible, hidden = [], []
    for row in rows:
        group, group_by = group_for(row["repository"], row["url"], groups)
        row["group"] = group["id"]
        row["groupBy"] = group_by
        (visible if group["count"] else hidden).append(row)
    return {
        "schemaVersion": 1, "account": login,
        "prs": sorted(visible, key=lambda row: row["updatedAt"], reverse=True),
        "excluded": sorted(hidden, key=lambda row: row["updatedAt"], reverse=True),
        "partial": bool(errors or truncated), "stale": bool(errors or truncated), "error": error,
        "fetchedAt": now, "lastSuccessAt": previous.get("lastSuccessAt") if errors or truncated else now,
        "retryAt": now + retry if retry else 0, "lifecycleCheckedAt": lifecycle_checked,
        "groups": groups,
        "excludedRepositories": [], "excludedOwners": [],
    }


def integer(value):
    if type(value) is not int or not 0 <= value <= 2 ** 53 - 1:
        raise ValueError("Invalid count")
    return value


def boolean(value):
    if type(value) is not bool:
        raise ValueError("Invalid boolean")
    return value


def validate_snapshot(value):
    """Reject old/untrusted cache shapes before copying or passing them to QML."""
    def text(value, maximum, nullable=False):
        if value is None and nullable:
            return
        if not isinstance(value, str) or len(value) > maximum:
            raise ValueError("Invalid string")

    def timestamp(value):
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 2 ** 53 - 1):
            raise ValueError("Invalid timestamp")

    fields = {"schemaVersion", "account", "accounts", "availableAccounts", "groups", "prs", "excluded", "partial", "stale", "error", "fetchedAt",
              "lastSuccessAt", "retryAt", "attemptedAt", "authFingerprint", "failures",
              "lifecycleCheckedAt", "configFingerprint", "excludedRepositories", "excludedOwners"}
    if not isinstance(value, dict) or set(value) - fields or type(value.get("schemaVersion")) is not int or value["schemaVersion"] != 1:
        raise ValueError("Invalid snapshot")
    for key in ("account", "authFingerprint"):
        if key in value:
            text(value[key], MAX_NAME)
    for key in ("accounts", "availableAccounts"):
        if key in value:
            if not isinstance(value[key], list) or len(value[key]) > 20:
                raise ValueError("Invalid accounts")
            for account in value[key]:
                text(account, MAX_NAME)
    if "groups" in value:
        Settings.validate({"schemaVersion": 2, "accounts": [], "groups": value["groups"]})
    text(value.get("error", ""), MAX_TEXT)
    for key in ("partial", "stale"):
        if key in value:
            boolean(value[key])
    for key in ("fetchedAt", "lastSuccessAt", "retryAt", "attemptedAt", "lifecycleCheckedAt"):
        if key in value:
            if key in {"retryAt", "attemptedAt"} and value[key] is None:
                raise ValueError("Invalid timestamp")
            timestamp(value[key])
    if "failures" in value:
        integer(value["failures"])
    for key in ("excludedRepositories", "excludedOwners"):
        if key in value:
            if not isinstance(value[key], list) or len(value[key]) > MAX_EXCLUSIONS:
                raise ValueError("Invalid exclusions")
            for item in value[key]:
                text(item, MAX_NAME)
    text(value.get("configFingerprint", ""), MAX_NAME)
    active, hidden = value.get("prs"), value.get("excluded", [])
    if not isinstance(active, list) or not isinstance(hidden, list) or len(active) + len(hidden) > MAX_ROWS:
        raise ValueError("Invalid contribution list")
    row_fields = {"id", "number", "title", "url", "repository", "author", "updatedAt", "draft",
                  "review", "discussion", "inline", "checks", "counts", "error", "fetchedAt"}
    seen = set()
    for row in active + hidden:
        optional = {"mergeable", "mergeStateStatus", "awaitingReady", "stack", "account", "group", "groupBy",
                    "kind", "status", "labels", "releaseUrl", "excludedBy", "landing", "followUp", "closed"}
        if not isinstance(row, dict) or not row_fields <= set(row) or set(row) - row_fields - optional:
            raise ValueError("Invalid PR")
        for key, maximum in (("id", MAX_REFERENCE), ("url", MAX_REFERENCE), ("title", MAX_TEXT),
                             ("repository", MAX_NAME), ("author", MAX_NAME), ("updatedAt", MAX_STATUS),
                             ("review", MAX_STATUS)):
            text(row[key], maximum)
        for key in ("mergeable", "mergeStateStatus"):
            if key in row:
                text(row[key], MAX_STATUS)
        if "kind" in row:
            text(row["kind"], MAX_STATUS)
            if row["kind"] not in {"pull_request", "submission", "merged", "closed", "tracked", "issue"}:
                raise ValueError("Invalid contribution kind")
        if "closed" in row:
            boolean(row["closed"])
        if "landing" in row:
            landing = row["landing"]
            if (not isinstance(landing, dict) or set(landing) - {"status", "sessions"} != {"landed", "steps", "workaround", "note"}
                    or not isinstance(landing["steps"], list)
                    or not 1 <= len(landing["steps"]) <= 1 + 2 * MAX_REQUIREMENTS):
                raise ValueError("Invalid landing")
            boolean(landing["landed"])
            text(landing["workaround"], MAX_TEXT)
            text(landing["note"], MAX_TEXT)
            for step in landing["steps"]:
                if not isinstance(step, dict) or set(step) != {"kind", "label", "done"} or step["kind"] not in {"github", "package"}:
                    raise ValueError("Invalid landing step")
                text(step["label"], MAX_NAME)
                boolean(step["done"])
        if "followUp" in row and (not isinstance(row["followUp"], dict) or set(row["followUp"]) != {"status", "sessions"}):
            raise ValueError("Invalid follow-up")
        # Status line and sessions: on a landing for followed items, on a follow-up for the rest.
        for context in (row.get("landing"), row.get("followUp")):
            if context and context.get("status") is not None:
                status = dict(context["status"]) if isinstance(context["status"], dict) else context["status"]
                if isinstance(status, dict) and "activity" in status:
                    text(status.pop("activity"), MAX_STATUS * 2)
                if isinstance(status, dict) and "outdated" in status:
                    boolean(status.pop("outdated"))
                Settings.validate_status(status)
            if context and "sessions" in context:
                if not isinstance(context["sessions"], list) or len(context["sessions"]) > MAX_SESSIONS:
                    raise ValueError("Invalid sessions")
                for session in context["sessions"]:
                    Settings.validate_session(session)
        if "account" in row:
            text(row["account"], MAX_NAME)
        if "group" in row:
            text(row["group"], MAX_STATUS)
        if "groupBy" in row:
            text(row["groupBy"], MAX_STATUS)
            if row["groupBy"] not in {"default", "item", "repository", "owner"}:
                raise ValueError("Invalid group rule scope")
        if "status" in row:
            text(row["status"], MAX_STATUS)
        if "releaseUrl" in row:
            text(row["releaseUrl"], MAX_REFERENCE, nullable=True)
        if "labels" in row:
            if not isinstance(row["labels"], list) or len(row["labels"]) > 30:
                raise ValueError("Invalid labels")
            for label in row["labels"]:
                text(label, MAX_STATUS)
        if "excludedBy" in row:
            if not isinstance(row["excludedBy"], list) or any(reason not in {"repository", "owner"} for reason in row["excludedBy"]):
                raise ValueError("Invalid exclusion reasons")
        if row.get("stack") is not None:
            stack = row["stack"]
            if not isinstance(stack, dict) or set(stack) != {"number", "size", "position"}:
                raise ValueError("Invalid stack")
            stack_info({"stack": stack, "position": stack["position"]})
        if "awaitingReady" in row:
            boolean(row["awaitingReady"])
        if not row["id"] or row["id"] in seen:
            raise ValueError("Invalid PR identifier")
        seen.add(row["id"])
        text(row["error"], MAX_TEXT, nullable=True)
        integer(row["number"])
        boolean(row["draft"])
        timestamp(row["fetchedAt"])
        for key in ("discussion", "inline"):
            if row[key] is not None:
                integer(row[key])
        checks, counts = row["checks"], row["counts"]
        if checks is None:
            if counts is not None:
                raise ValueError("Counts without checks")
            continue
        if not isinstance(checks, list) or len(checks) > MAX_ENTRIES:
            raise ValueError("Invalid checks")
        expected = dict.fromkeys(BUCKETS, 0)
        for check in checks:
            if not isinstance(check, dict) or set(check) != {"name", "status", "bucket"}:
                raise ValueError("Invalid check")
            text(check["name"], MAX_TEXT)
            text(check["status"], MAX_STATUS)
            text(check["bucket"], MAX_STATUS)
            if check["bucket"] not in expected:
                raise ValueError("Invalid check bucket")
            expected[check["bucket"]] += 1
        if not isinstance(counts, dict) or set(counts) != set(BUCKETS):
            raise ValueError("Invalid counts")
        for count in counts.values():
            integer(count)
        if counts != expected:
            raise ValueError("Check counts disagree")
    return value


def safe_previous(value):
    try:
        return validate_snapshot(dict({"schemaVersion": 1, "prs": [], "excluded": []}, **value))
    except (ValueError, TypeError, AttributeError):
        return {}


def encoded_snapshot(value, public=False):
    fields = {key: item for key, item in value.items() if not public or key != "authFingerprint"}
    buffer = bytearray()
    encoder = json.JSONEncoder(ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    for chunk in encoder.iterencode(fields):
        encoded = chunk.encode("utf-8")
        if len(buffer) + len(encoded) + 1 > MAX_SNAPSHOT_BYTES:
            raise LimitError("Snapshot size limit reached.")
        buffer.extend(encoded)
    buffer.extend(b"\n")
    return bytes(buffer)


def finalize(value, last_success=None):
    """The same bounded representation feeds the cache and stdout on every path."""
    try:
        result = copy.deepcopy(validate_snapshot(value))
    except (ValueError, TypeError, AttributeError):
        result = {"schemaVersion": 1, "prs": [], "excluded": [], "partial": True, "stale": True,
                  "error": "Invalid or oversized PR snapshot; refresh to try again."}
    result["prs"].sort(key=lambda row: row["updatedAt"], reverse=True)
    try:
        encoded_snapshot(result)
    except LimitError:
        result.update(partial=True, stale=True, lastSuccessAt=last_success,
                      error="Snapshot size limit reached; oldest PRs were omitted.")
        # Find the longest newest-first prefix without repeatedly encoding a huge tail.
        active, hidden = result["prs"], result.get("excluded", [])
        combined = [("prs", row) for row in active] + [("excluded", row) for row in hidden]
        combined.sort(key=lambda pair: pair[1]["updatedAt"], reverse=True)
        low, high = 0, len(combined)
        while low < high:
            middle = (low + high + 1) // 2
            kept = combined[:middle]
            result["prs"] = [row for bucket, row in kept if bucket == "prs"]
            result["excluded"] = [row for bucket, row in kept if bucket == "excluded"]
            try:
                encoded_snapshot(result)
                low = middle
            except LimitError:
                high = middle - 1
        kept = combined[:low]
        result["prs"] = [row for bucket, row in kept if bucket == "prs"]
        result["excluded"] = [row for bucket, row in kept if bucket == "excluded"]
    result["prs"] = group_stacks(result["prs"])
    result["excluded"] = group_stacks(result.get("excluded", []))
    return result


class Cache:
    def __init__(self, path):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise FetchError("Cache directory must be owned by you and must not be a symlink.", "cache")
        os.chmod(path, 0o700)
        self.path = path
        self.fd = None

    def lock(self, deadline):
        self.fd = os.open(self.path / "refresh.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        while True:
            deadline.remaining()
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                time.sleep(min(0.05, deadline.remaining()))

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def read(self):
        try:
            fd = os.open(self.path / "snapshot.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    return {}
                raw = stream.read(MAX_SNAPSHOT_BYTES + 1)
            if len(raw) > MAX_SNAPSHOT_BYTES:
                return {}
            return validate_snapshot(json.loads(raw))
        except (OSError, ValueError, AttributeError, TypeError, RecursionError):
            return {}

    def write(self, data):
        data = finalize(data, data.get("lastSuccessAt"))
        name = self.path / f"snapshot.{os.getpid()}.tmp"
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded_snapshot(data))
            os.replace(name, self.path / "snapshot.json")
        finally:
            name.unlink(missing_ok=True)


class Settings:
    """Private, user-owned account selection and contribution groups."""
    def __init__(self, path):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise FetchError("Config directory must be owned by you and must not be a symlink.", "config")
        os.chmod(path, 0o700)
        self.path = path / "config.json"

    @staticmethod
    def validate(value):
        if not isinstance(value, dict):
            raise ValueError("Invalid settings")
        if value.get("schemaVersion") == 1:
            value = {
                "schemaVersion": 2, "accounts": value.get("accounts", []),
                "groups": contribution_groups(value),
            }
        if value.get("schemaVersion") != 2 or set(value) - {"schemaVersion", "accounts", "groups", "tracks", "dismissed", "autoSummary"}:
            raise ValueError("Invalid settings")
        accounts = value.get("accounts", [])
        groups = value.get("groups", [])
        if not isinstance(accounts, list) or not isinstance(groups, list):
            raise ValueError("Invalid settings")
        if len(accounts) > 20 or not 1 <= len(groups) <= MAX_GROUPS:
            raise ValueError("Too many settings")
        clean_accounts = set()
        for account in accounts:
            if not isinstance(account, str) or not GITHUB_OWNER.fullmatch(account):
                raise ValueError("Invalid account")
            clean_accounts.add(account)
        clean_groups, ids, names, used_items, used_repositories, used_owners = [], set(), set(), set(), set(), set()
        for index, group in enumerate(groups):
            if not isinstance(group, dict):
                raise ValueError("Invalid group")
            group = dict(group)
            group.setdefault("items", [])
            if set(group) != {"id", "name", "count", "items", "repositories", "owners"}:
                raise ValueError("Invalid group")
            identifier, name = group["id"], group["name"].strip() if isinstance(group["name"], str) else ""
            if (not isinstance(identifier, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", identifier)
                    or not name or len(name) > 40 or identifier in ids or name.lower() in names
                    or type(group["count"]) is not bool):
                raise ValueError("Invalid group")
            if index == 0 and identifier != "inbox":
                raise ValueError("First group must be inbox")
            items, repositories, owners = group["items"], group["repositories"], group["owners"]
            if not isinstance(items, list) or not isinstance(repositories, list) or not isinstance(owners, list):
                raise ValueError("Invalid group rules")
            if len(items) + len(repositories) + len(owners) > MAX_EXCLUSIONS:
                raise ValueError("Too many group rules")
            clean_items, clean_repositories, clean_owners = set(), set(), set()
            for url in items:
                if not isinstance(url, str) or not GITHUB_ITEM.fullmatch(url):
                    raise ValueError("Invalid item rule")
                clean_items.add(url)
            for repository in repositories:
                if not isinstance(repository, str) or repository.count("/") != 1:
                    raise ValueError("Invalid repository rule")
                owner, repository_name = repository.split("/", 1)
                if not GITHUB_OWNER.fullmatch(owner) or not GITHUB_REPOSITORY.fullmatch(repository_name):
                    raise ValueError("Invalid repository rule")
                clean_repositories.add(repository.lower())
            for owner in owners:
                if not isinstance(owner, str) or not GITHUB_OWNER.fullmatch(owner):
                    raise ValueError("Invalid owner rule")
                clean_owners.add(owner.lower())
            if used_items & clean_items or used_repositories & clean_repositories or used_owners & clean_owners:
                raise ValueError("A rule can belong to only one group")
            used_items |= clean_items
            used_repositories |= clean_repositories
            used_owners |= clean_owners
            ids.add(identifier)
            names.add(name.lower())
            clean_groups.append({"id": identifier, "name": name, "count": group["count"], "items": sorted(clean_items),
                                 "repositories": sorted(clean_repositories), "owners": sorted(clean_owners)})
        result = {"schemaVersion": 2, "accounts": sorted(clean_accounts, key=str.lower), "groups": clean_groups}
        tracks = Settings.validate_tracks(value.get("tracks", []))
        if tracks:
            result["tracks"] = tracks
        dismissed = value.get("dismissed", [])
        if (not isinstance(dismissed, list) or len(dismissed) > MAX_EXCLUSIONS
                or not all(isinstance(url, str) and GITHUB_ITEM.fullmatch(url) for url in dismissed)):
            raise ValueError("Invalid dismissed items")
        if dismissed:
            result["dismissed"] = list(dict.fromkeys(dismissed))
        if value.get("autoSummary", True) is not True:
            if value["autoSummary"] is not False:
                raise ValueError("Invalid automatic summary setting")
            result["autoSummary"] = False
        return result

    @staticmethod
    def validate_tracks(tracks):
        """What must still happen, after the contribution itself, before it reaches this machine."""
        if (not isinstance(tracks, list)
                or sum(1 for track in tracks if not (isinstance(track, dict) and track.get("summaryOnly"))) > MAX_TRACKS
                or len(tracks) > MAX_TRACKS + MAX_SUMMARIES):
            raise ValueError("Too many tracked contributions")
        clean, seen = [], set()
        for track in tracks:
            if not isinstance(track, dict) or set(track) - {"url", "after", "packages", "workaround", "note", "status", "sessions", "summaryOnly"}:
                raise ValueError("Invalid tracked contribution")
            url, after, packages = track.get("url"), track.get("after", []), track.get("packages", [])
            if not isinstance(url, str) or not GITHUB_ITEM.fullmatch(url) or url in seen:
                raise ValueError("Invalid tracked contribution")
            seen.add(url)
            if (not isinstance(after, list) or not isinstance(packages, list)
                    or len(after) > MAX_REQUIREMENTS or len(packages) > MAX_REQUIREMENTS):
                raise ValueError("Too many landing requirements")
            for item in after:
                if not isinstance(item, str) or not GITHUB_ITEM.fullmatch(item) or item == url:
                    raise ValueError("Invalid landing requirement")
            clean_packages = []
            for package in packages:
                if (not isinstance(package, dict) or set(package) != {"name", "operator", "version"}
                        or not all(isinstance(item, str) for item in package.values())
                        or not PACKAGE_NAME.fullmatch(package["name"]) or package["operator"] not in {">", ">="}
                        or not PACKAGE_VERSION.fullmatch(package["version"])):
                    raise ValueError("Invalid package requirement")
                clean_packages.append(dict(package))
            texts = {}
            for key in ("workaround", "note"):
                texts[key] = track.get(key, "")
                if not isinstance(texts[key], str) or len(texts[key]) > MAX_TEXT:
                    raise ValueError("Invalid tracked contribution text")
                texts[key] = " ".join(texts[key].split())
            entry = {"url": url, "after": list(dict.fromkeys(after)), "packages": clean_packages, **texts}
            # Asked about or discussed, but never declared: it has no landing to wait for.
            if track.get("summaryOnly") is not None:
                if track["summaryOnly"] is not True or after or packages:
                    raise ValueError("Invalid tracked contribution")
                entry["summaryOnly"] = True
            if track.get("status") is not None:
                entry["status"] = Settings.validate_status(track["status"])
            sessions = track.get("sessions", [])
            if not isinstance(sessions, list) or len(sessions) > MAX_SESSIONS:
                raise ValueError("Too many sessions")
            if sessions:
                entry["sessions"] = [Settings.validate_session(session) for session in sessions]
            clean.append(entry)
        return clean

    @staticmethod
    def validate_status(status):
        """A status line; `revision` is the GitHub state it describes, `waiting` who has to act next."""
        if (not isinstance(status, dict) or not {"text", "at", "by"} <= set(status) <= {"text", "at", "by", "revision", "waiting"}
                or not all(isinstance(value, str) for value in status.values())
                or not status["text"].strip() or len(status["text"]) > MAX_TEXT
                or not TIMESTAMP.fullmatch(status["at"]) or len(status["by"]) > MAX_STATUS
                or ("revision" in status and not REVISION.fullmatch(status["revision"]))
                or ("waiting" in status and status["waiting"] not in WAITING)):
            raise ValueError("Invalid status")
        return dict(status, text=" ".join(status["text"].split()))

    @staticmethod
    def validate_session(session):
        """Where the work started: enough to reopen it, locally or over SSH."""
        if (not isinstance(session, dict) or set(session) != {"agent", "id", "host", "cwd", "at"}
                or not all(isinstance(value, str) for value in session.values())
                or session["agent"] not in AGENTS or not SESSION_ID.fullmatch(session["id"])
                or not HOST_NAME.fullmatch(session["host"]) or not TIMESTAMP.fullmatch(session["at"])
                or not session["cwd"].startswith("/") or len(session["cwd"]) > MAX_REFERENCE
                or any(ord(character) < 32 for character in session["cwd"])):
            raise ValueError("Invalid session")
        return dict(session)

    def update_track(self, url, change, summary_only=False):
        """Read-modify-write one followed item under a lock; creates it when missing."""
        if not isinstance(url, str) or not GITHUB_ITEM.fullmatch(url):
            raise ValueError("Follow a GitHub issue, pull request or discussion URL")
        fd = os.open(self.path.with_name("config.lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            current = self.read()
            tracks = current.setdefault("tracks", [])
            track = next((track for track in tracks if track["url"] == url), None)
            if track is None:
                track = {"url": url, "after": [], "packages": [], "workaround": "", "note": ""}
                if summary_only:
                    track["summaryOnly"] = True
                    # The oldest summary makes room: it can always be asked for again.
                    summaries = sorted((item for item in tracks if item.get("summaryOnly")),
                                       key=lambda item: (item.get("status") or {}).get("at", ""))
                    for item in summaries[:max(0, len(summaries) + 1 - MAX_SUMMARIES)]:
                        tracks.remove(item)
                tracks.append(track)
            change(track)
            self.write(current)
            return track
        finally:
            os.close(fd)

    def set_status(self, url, text, by, revision=None, waiting=None):
        status = {"text": text, "at": utc_now(), "by": by}
        status.update({key: value for key, value in (("revision", revision), ("waiting", waiting)) if value})
        self.update_track(url, lambda track: track.update(status=status))

    def add_session(self, url, session):
        session = self.validate_session(dict(session, at=utc_now()))

        def change(track):
            kept = [item for item in track.get("sessions", []) if item["id"] != session["id"]]
            track["sessions"] = (kept + [session])[-MAX_SESSIONS:]
        self.update_track(url, change)

    def track(self, url, after, packages, workaround, note):
        """Replace the requirements; the status line and sessions belong to the item and are kept."""
        requirements = [parse_requirement(package) for package in packages]

        def change(track):
            track.pop("summaryOnly", None)
            track.update(after=after, packages=requirements, workaround=workaround or "", note=note or "")
        self.update_track(url, change)

    def untrack(self, url):
        current = self.read()
        current["tracks"] = [track for track in current.get("tracks", []) if track["url"] != url]
        self.write(current)

    def follow_up(self, url):
        """The item's status line and sessions, creating a summary-only entry for an unfollowed one."""
        return self.update_track(url, lambda track: None, summary_only=True)

    def dismiss(self, url, dismissed=True):
        """Hide an item for good (or show it again); dismissing also stops following it."""
        if not isinstance(url, str) or not GITHUB_ITEM.fullmatch(url):
            raise ValueError("Dismiss a GitHub issue, pull request or discussion URL")
        current = self.read()
        kept = [item for item in current.get("dismissed", []) if item != url]
        current["dismissed"] = (kept + [url] if dismissed else kept)[-MAX_EXCLUSIONS:]
        if dismissed:
            current["tracks"] = [track for track in current.get("tracks", []) if track["url"] != url]
        self.write(current)

    def read(self):
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "r", encoding="utf-8") as stream:
                return self.validate(json.load(stream))
        except FileNotFoundError:
            return {"schemaVersion": 2, "accounts": [], "groups": contribution_groups({})}
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            raise FetchError("Cannot read OmaContrib settings.", "config") from exc

    def write(self, value):
        value = self.validate(value)
        temporary = self.path.with_name("config.json.new")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(value, stream, indent=2, sort_keys=True)
                stream.write("\n")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def add(self, scope, value):
        current = self.read()
        if scope != "account":
            raise ValueError("Rules must be assigned to a group")
        current["accounts"].append(value)
        self.write(current)

    def remove(self, scope, value):
        current = self.read()
        if scope != "account":
            raise ValueError("Rules must be assigned to a group")
        current["accounts"] = [item for item in current["accounts"] if item.lower() != value.lower()]
        self.write(current)

    def create_group(self, name):
        current = self.read()
        identifier = self._append_group(current, name)
        self.write(current)
        return identifier

    @staticmethod
    def _append_group(current, name):
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32] or "group"
        if not base[0].isalpha():
            base = "group-" + base
        identifier, suffix = base, 2
        used = {group["id"] for group in current["groups"]}
        while identifier in used:
            identifier, suffix = f"{base[:35]}-{suffix}", suffix + 1
        current["groups"].append({"id": identifier, "name": name, "count": True, "items": [],
                                  "repositories": [], "owners": []})
        return identifier

    def create_group_and_move(self, scope, value, name):
        current = self.read()
        identifier = self._append_group(current, name)
        key = {"item": "items", "repository": "repositories", "owner": "owners"}[scope]
        current["groups"][-1][key].append(value)
        self.write(current)
        return identifier

    def delete_group(self, identifier):
        if identifier == "inbox":
            raise ValueError("The inbox group cannot be deleted")
        current = self.read()
        current["groups"] = [group for group in current["groups"] if group["id"] != identifier]
        self.write(current)

    def set_group_count(self, identifier, enabled):
        current = self.read()
        found = False
        for group in current["groups"]:
            if group["id"] == identifier:
                group["count"], found = enabled, True
        if not found:
            raise ValueError("Unknown group")
        self.write(current)

    def move_rule(self, scope, identifier, value):
        current = self.read()
        key = {"item": "items", "repository": "repositories", "owner": "owners"}[scope]
        for group in current["groups"]:
            group[key] = [item for item in group[key] if item.lower() != value.lower()]
        target = next((group for group in current["groups"] if group["id"] == identifier), None)
        if target is None:
            raise ValueError("Unknown group")
        target[key].append(value)
        self.write(current)

    @staticmethod
    def fingerprint(value):
        # Status lines, sessions and summary-only entries are attached at output time; they must
        # not invalidate the cache.
        value = {key: item for key, item in value.items() if key != "autoSummary"}
        value["tracks"] = [{key: item for key, item in track.items() if key not in {"status", "sessions"}}
                           for track in value.get("tracks", []) if not track.get("summaryOnly")]
        if not value["tracks"]:
            del value["tracks"]
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def auth_fingerprint():
    """Invalidate cached private data on gh account/config/token changes, without storing tokens."""
    home = Path(os.environ.get("GH_CONFIG_DIR", str(Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "gh")))
    digest = hashlib.sha256()
    for filename in ("hosts.yml", "config.yml"):
        try:
            digest.update((home / filename).read_bytes())
        except OSError:
            pass
    digest.update(os.environ.get("GH_TOKEN", os.environ.get("GITHUB_TOKEN", "")).encode())
    return digest.hexdigest()


def authenticated_accounts(timeout=5):
    """List local gh accounts without exposing tokens or changing the active account."""
    env = dict(os.environ, GH_PROMPT_DISABLED="1", GH_HOST="github.com")
    try:
        proc = bounded_process(
            ["gh", "auth", "status", "--hostname", "github.com", "--json", "hosts"],
            "", timeout, env,
        )
        payload = json.loads(proc.stdout) if proc.stdout else {}
        entries = payload.get("hosts", {}).get("github.com", [])
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, AttributeError):
        return []
    accounts = []
    for entry in entries if isinstance(entries, list) else []:
        login = entry.get("login") if isinstance(entry, dict) else None
        if isinstance(login, str) and GITHUB_OWNER.fullmatch(login) and login.lower() not in {item.lower() for item in accounts}:
            if entry.get("active"):
                accounts.insert(0, login)
            else:
                accounts.append(login)
    return accounts


def account_token(login, timeout=5):
    env = dict(os.environ, GH_PROMPT_DISABLED="1", GH_HOST="github.com")
    try:
        proc = bounded_process(
            ["gh", "auth", "token", "--hostname", "github.com", "--user", login],
            "", timeout, env,
        )
    except FileNotFoundError as exc:
        raise FetchError("Install GitHub CLI (gh), then add an account.", "auth") from exc
    except subprocess.TimeoutExpired as exc:
        raise FetchError("GitHub account lookup timed out.", "auth") from exc
    token = proc.stdout.strip()
    if proc.returncode or not token:
        raise FetchError("GitHub account is not available locally. Add it again.", "auth")
    return token


def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def current_session(agent=None, session_id=None, host=None, cwd=None):
    """Describe the calling agent session from its environment, overridable per field."""
    detected = {"claude": os.environ.get("CLAUDE_CODE_SESSION_ID"), "codex": os.environ.get("CODEX_THREAD_ID")}
    agent = agent or next((name for name, value in detected.items() if value), None)
    session_id = session_id or detected.get(agent or "")
    if not agent or not session_id:
        raise ValueError("No agent session found here; pass --agent and --session-id")
    return {"agent": agent, "id": session_id, "host": host or socket.gethostname().split(".")[0],
            "cwd": os.path.abspath(cwd or os.getcwd())}


def annotate_tracks(result, settings):
    """Attach the latest status line and sessions, and drop dismissed items: both change without a GitHub refresh."""
    tracks = {track["url"]: track for track in settings.get("tracks", [])}
    dismissed = set(settings.get("dismissed", []))
    for bucket in ("prs", "excluded"):
        result[bucket] = [row for row in result.get(bucket, []) if row["url"] not in dismissed]
        for row in result[bucket]:
            track = tracks.get(row["url"])
            status = dict(track["status"]) if track and track.get("status") else None
            if status and status["by"] != "checking" and status.get("revision") not in {None, revision(row)}:
                status["outdated"] = True
            if status and status["by"] == "checking":
                # What the background agent is doing right now, from its own stream.
                try:
                    current = " ".join(agent_log_path(row["url"], ".activity").read_text(errors="replace").split())
                except OSError:
                    current = ""
                if current:
                    status["activity"] = current[:MAX_STATUS * 2]
            if track and "landing" in row:
                row["landing"]["status"] = status
                row["landing"]["sessions"] = track.get("sessions", [])
            elif track and track.get("summaryOnly"):
                row["followUp"] = {"status": status, "sessions": track.get("sessions", [])}


def revision(row):
    """The GitHub state a summary describes: what the row shows, minus checks still running."""
    counts = row.get("counts") or {}
    landing = row.get("landing") or {}
    state = [row.get("kind"), row.get("status"), row.get("review"), row.get("draft"), row.get("discussion"),
             row.get("inline"), row.get("updatedAt"), None if counts.get("running") else counts,
             landing.get("landed"), [step.get("done") for step in landing.get("steps", [])]]
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()[:12]


def waiting_on_me(row):
    status = (row.get("landing") or row.get("followUp") or {}).get("status") or {}
    return status.get("waiting") == "me"


def order_rows(result):
    """Items waiting on me first, then the most recently changed; a stack stays together."""
    for bucket in ("prs", "excluded"):
        groups = {}
        for row in result.get(bucket, []):
            stack = row.get("stack")
            groups.setdefault((row["repository"], stack["number"]) if stack else (row["url"],), []).append(row)
        ordered = sorted(groups.values(), key=lambda group: max(row["updatedAt"] for row in group), reverse=True)
        ordered.sort(key=lambda group: not any(waiting_on_me(row) for row in group))
        result[bucket] = [row for group in ordered for row in group]


def auto_state_path():
    return agent_log_path("automatic", ".json")


def auto_summaries(result, settings_store, now=time.time, spawn=None):
    """Keep every active contribution's summary fresh: start one background check when one is due.

    Due: no summary yet, or the GitHub state changed and has held for AUTO_SETTLE. A run in
    progress, or AUTO_DAILY runs in the last day, defers the rest to a later refresh.
    """
    spawn = spawn or ask_status
    settings = settings_store.read()
    if settings.get("autoSummary") is False:
        return None
    tracks = {track["url"]: track for track in settings.get("tracks", [])}

    def stuck(status):
        return now() - calendar.timegm(time.strptime(status["at"], "%Y-%m-%dT%H:%M:%SZ")) > AGENT_SECONDS + 120

    checking = [track for track in tracks.values() if (track.get("status") or {}).get("by") == "checking"]
    if any(not stuck(track["status"]) for track in checking):
        return None
    try:
        state = json.loads(auto_state_path().read_text())
        seen, runs = dict(state["seen"]), [float(at) for at in state["runs"]]
    except (OSError, ValueError, KeyError, TypeError):
        seen, runs = {}, []
    runs = [at for at in runs if at > now() - 86400]
    fresh, due = {}, []
    for row in result.get("prs", []):
        if row.get("kind") == "submission" or row.get("error"):
            continue
        url, current = row["url"], revision(row)
        first = seen.get(url)
        fresh[url] = [current, first[1] if isinstance(first, list) and len(first) == 2 and first[0] == current else now()]
        status = (tracks.get(url) or {}).get("status")
        if status and status["by"] == "checking":
            due.append(row)
        elif status and "revision" not in status:
            # Written by hand or by an agent: it describes the state as it is now.
            settings_store.update_track(url, lambda track, current=current: track["status"].update(revision=current))
        elif not status or (status["revision"] != current and now() - fresh[url][1] >= AUTO_SETTLE):
            due.append(row)
    started = None
    if due and len(runs) < AUTO_DAILY:
        due.sort(key=lambda row: row["updatedAt"], reverse=True)
        row = min(due, key=lambda row: not waiting_on_me(row))
        spawn(settings_store, row["url"], revision(row))
        runs.append(now())
        started = row["url"]
    try:
        auto_state_path().write_text(json.dumps({"seen": fresh, "runs": runs}))
    except OSError:
        pass
    return started


WAITING_LINES = (
    "Then judge who has to act next and put it on its own line just before the last one:",
    "WAITING: me (I, the contributor, must reply, fix, rebase, test or decide), them (maintainers,",
    "reviewers, CI, a release or a downstream package), or nobody (done, or nothing more will happen).",
)


def summary_prompt(track):
    """For an item that is not followed to landing: an open contribution, or one closed without a merge."""
    lines = [f"Something I contributed to on GitHub: {track['url']}"]
    if track.get("status"):
        lines.append(f"Earlier summary ({track['status']['at']}): {track['status']['text']}")
    lines += [
        "",
        "Find out where it stands: read the item, its comments, reviews and checks, and any linked or follow-up",
        "issues, PRs or commits (gh ... view, gh search, git ls-remote).",
        "If it is still open: what is it waiting for (review, failing checks, conflicts, a reply from me)?",
        "If it was closed: was it applied by hand, superseded, rejected, or abandoned?",
        "Either way: is anything left for me to do?",
        "Do not comment, edit, or change anything anywhere.",
        *WAITING_LINES,
        "End with exactly one final line: STATUS: <at most 200 characters: where it stands and anything left to do>",
    ]
    return "\n".join(lines)


def status_prompt(track):
    if track.get("summaryOnly"):
        return summary_prompt(track)
    lines = [f"I'm following this contribution until its fix reaches my machine: {track['url']}"]
    if track["after"]:
        lines.append("It also waits for: " + ", ".join(track["after"]))
    if track["packages"]:
        lines.append("It counts as landed when these installed pacman packages carry the fix: "
                     + ", ".join(f"{p['name']} {p['operator']} {p['version']}" for p in track["packages"]))
    for label, key in (("Why it matters", "note"), ("Workaround in place", "workaround")):
        if track[key]:
            lines.append(f"{label}: {track[key]}")
    if track.get("status"):
        lines.append(f"Previous status ({track['status']['at']}): {track['status']['text']}")
    lines += [
        "",
        "Find out where it stands now: read the item and its comments, linked or follow-up issues and PRs,",
        "commits, tags (git ls-remote --tags) or releases that carry the fix, installed and repository",
        "versions (gh ... view, gh search, pacman -Q, pacman -Si).",
        "Do not comment, edit, or change anything anywhere.",
        *WAITING_LINES,
        "End with exactly one final line: STATUS: <at most 200 characters: where it stands and the next thing that has to happen>",
    ]
    return "\n".join(lines)


def find_track(settings, url):
    track = next((track for track in settings.get("tracks", []) if track["url"] == url), None)
    if track is None:
        raise ValueError("Not followed yet; use --track first")
    return track


def agent_log_path(url, suffix=".log"):
    root = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "omacontrib" / "agent"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root / (hashlib.sha256(url.encode()).hexdigest()[:16] + suffix)


def ask_status(settings_store, url, revision=None):
    """Start a background, read-only agent run that records a fresh status line.

    `revision` is the GitHub state the run describes; the result keeps it, so a later
    change shows the summary as outdated.
    """
    track = settings_store.follow_up(url)
    # "checking" keeps the previous text visible while the run is in progress.
    previous = track.get("status") or {}
    settings_store.set_status(url, previous.get("text", "No status yet"), "checking",
                              revision or previous.get("revision"), previous.get("waiting"))
    agent_log_path(url, ".activity").unlink(missing_ok=True)
    with open(agent_log_path(url), "wb") as log:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--run-status-agent", url],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)


def stream_agent(command, on_line, timeout=AGENT_SECONDS):
    """Run the agent, handing over each output line as it comes; returns the exit code."""
    proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, cwd=str(Path.home()))
    watchdog = threading.Timer(timeout, proc.kill)
    watchdog.start()
    try:
        for line in proc.stdout:
            on_line(line)
        return proc.wait()
    finally:
        watchdog.cancel()


def agent_event(line):
    try:
        event = json.loads(line)
    except ValueError:
        return None
    return event if isinstance(event, dict) else None


def agent_steps(event):
    """What an agent's stream-json event says it is doing: ("tool", text) and ("text", text) pairs."""
    message = event.get("message") if event.get("type") == "assistant" else None
    content = message.get("content") if isinstance(message, dict) else None
    for item in content if isinstance(content, list) else []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "tool_use":
            arguments = item.get("input") if isinstance(item.get("input"), dict) else {}
            detail = next((arguments[key] for key in ("command", "url", "query", "pattern", "file_path")
                           if isinstance(arguments.get(key), str)), "")
            name = "$" if item.get("name") == "Bash" else str(item.get("name", "tool"))
            yield "tool", " ".join(f"{name} {detail}".split())
        elif item.get("type") == "text" and isinstance(item.get("text"), str) and item["text"].strip():
            yield "text", item["text"].strip()


def run_status_agent(settings_store, url, runner=stream_agent):
    track = find_track(settings_store.read(), url)
    described = (track.get("status") or {}).get("revision")
    if (track.get("status") or {}).get("text") == "No status yet":
        track = {key: value for key, value in track.items() if key != "status"}
    command = ["claude", "-p", status_prompt(track), "--output-format", "stream-json", "--verbose",
               "--allowedTools", *STATUS_TOOLS]
    seen = {"session": None, "result": None, "failed": False}
    activity = agent_log_path(url, ".activity")

    def on_line(line):
        # The raw stream is the log `--watch-status` follows.
        print(line, end="" if line.endswith("\n") else "\n", flush=True)
        event = agent_event(line)
        if event is None:
            return
        if isinstance(event.get("session_id"), str) and SESSION_ID.fullmatch(event["session_id"]):
            seen["session"] = event["session_id"]
        if event.get("type") == "result":
            seen["result"] = event.get("result") if isinstance(event.get("result"), str) else ""
            seen["failed"] = bool(event.get("is_error"))
        steps = list(agent_steps(event))
        if steps:
            activity.write_text(" ".join(steps[-1][1].splitlines()[0].split())[:MAX_STATUS * 2])

    try:
        code = runner(command, on_line)
    except (OSError, subprocess.SubprocessError) as exc:
        print(exc)
        code = 1
    output = [line.strip() for line in (seen["result"] or "").splitlines()]
    lines = [line for line in output if line.startswith("STATUS:")]
    text = lines[-1].removeprefix("STATUS:").strip() if lines and not code and not seen["failed"] else ""
    waiting = [line.removeprefix("WAITING:").strip().lower().split(" ")[0].strip(".,;:")
               for line in output if line.startswith("WAITING:")]
    settings_store.set_status(url, text[:MAX_TEXT] or "Status check failed; see " + str(agent_log_path(url)), "claude",
                              described,
                              waiting[-1] if text and waiting and waiting[-1] in WAITING else None)
    activity.unlink(missing_ok=True)
    # With nothing to go back to, the check itself becomes the conversation `o` resumes.
    if seen["session"] and not find_track(settings_store.read(), url).get("sessions"):
        settings_store.add_session(url, current_session("claude", seen["session"], cwd=str(Path.home())))


def print_agent_event(event, output=print):
    if event.get("type") == "system" and event.get("subtype") == "init":
        output(f"· session {event.get('session_id', '?')}\n")
    for kind, text in agent_steps(event):
        output(("→ " + text) if kind == "tool" else "\n" + text + "\n")
    if event.get("type") == "result":
        seconds = event.get("duration_ms", 0) / 1000 if isinstance(event.get("duration_ms"), (int, float)) else 0
        output(f"\n· done in {seconds:.0f}s" + (" (failed)" if event.get("is_error") else ""))


def follow_agent(url, settings_store, sleep=time.sleep):
    """Show a status check as it runs (or the last one's full report), then offer to continue it."""
    path, done, session = agent_log_path(url), False, None
    if not path.exists():
        print("No agent check has run for this item yet.")
    else:
        with open(path, encoding="utf-8", errors="replace") as log:
            while not done:
                line = log.readline()
                if not line:
                    track = find_track(settings_store.read(), url)
                    if (track.get("status") or {}).get("by") != "checking":
                        break
                    sleep(0.5)
                    continue
                event = agent_event(line)
                if event is None:
                    print(line.rstrip())
                    continue
                session = event.get("session_id") if isinstance(event.get("session_id"), str) else session
                print_agent_event(event)
                done = event.get("type") == "result"
    resumable = bool(session and SESSION_ID.fullmatch(session) and shutil.which("claude"))
    try:
        answer = input("\nEnter to close" + (" · c + Enter to continue this conversation: " if resumable else ""))
    except EOFError:
        return
    if resumable and answer.strip().lower() == "c":
        os.chdir(Path.home())
        os.execvp("claude", ["claude", "--resume", session])


def watch_status(url):
    open_terminal([sys.executable, str(Path(__file__).resolve()), "--follow-agent", url], str(Path.home()))
    return "terminal"


def open_terminal(command, directory):
    subprocess.Popen(["setsid", "uwsm-app", "--", "xdg-terminal-exec", f"--dir={directory}", *command],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def herdr(*args, machine=None, timeout=60):
    command = ["herdr"] + (["--machine", machine] if machine else []) + list(args)
    env = {key: value for key, value in os.environ.items() if not key.startswith("HERDR_") or key == "HERDR_SOCKET_PATH"}
    proc = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout, env=env)
    if proc.returncode:
        raise FetchError("herdr: " + (proc.stderr.strip() or "failed")[:200], "herdr")
    return json.loads(proc.stdout or "{}")


def herdr_machines():
    try:
        proc = subprocess.run(["herdr", "machine", "list", "--json"], capture_output=True, text=True, timeout=10)
        payload = json.loads(proc.stdout or "[]")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return set()
    machines = payload if isinstance(payload, list) else payload.get("machines", []) if isinstance(payload, dict) else []
    return {item.get("label") for item in machines if isinstance(item, dict) and item.get("enabled", True)}


def open_in_herdr(label, cwd, kind, arguments, machine=None):
    created = herdr("workspace", "create", "--cwd", cwd, "--label", label[:60], "--focus", machine=machine)["result"]
    name = "oc-" + hashlib.sha256((label + utc_now()).encode()).hexdigest()[:8]
    herdr("agent", "start", name, "--kind", kind, "--pane", created["root_pane"]["pane_id"], "--", *arguments,
          machine=machine, timeout=90)


def open_session(settings_store, url, launcher=None, local_host=None):
    """Reopen the newest originating session, or start a fresh one primed to check status.

    herdr is preferred (a saved herdr machine for another computer); a plain terminal,
    over SSH when the session lives elsewhere, is the fallback.
    """
    track = settings_store.follow_up(url)
    local_host = local_host or socket.gethostname().split(".")[0]
    owner, name, _, number = GITHUB_ITEM.fullmatch(url).groups()
    label = f"{owner}/{name}#{number}"
    sessions = track.get("sessions", [])
    if sessions:
        session = sessions[-1]
        executable, resume = AGENTS[session["agent"]]
        kind, arguments, host, cwd = session["agent"], [resume, session["id"]], session["host"], session["cwd"]
    else:
        kind, arguments, host, cwd = "claude", [status_prompt(track)], local_host, str(Path.home())
        executable = "claude"
    remote = host.lower() != local_host.lower()
    if launcher is None:
        launcher = launch
    return launcher(label, kind, executable, arguments, host if remote else None, cwd)


def launch(label, kind, executable, arguments, host, cwd):
    if host is None or host in herdr_machines():
        try:
            open_in_herdr(label, cwd if host or os.path.isdir(cwd) else str(Path.home()), kind, arguments, machine=host)
            return "herdr"
        except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError, FetchError) as exc:
            print(f"herdr unavailable ({exc}); using a terminal", file=sys.stderr)
    if host:
        remote = "cd {} && exec {}".format(shlex.quote(cwd), shlex.join([executable, *arguments]))
        command, directory = ["ssh", "-t", host, remote], str(Path.home())
    else:
        command, directory = [executable, *arguments], cwd if os.path.isdir(cwd) else str(Path.home())
    open_terminal(command, directory)
    return "terminal"


def ready_notifications(result, previous, enabled):
    """Persist review cycles in the locked cache, including while CI is pending."""
    old = {row["id"]: row for row in previous.get("prs", [])}
    notifications = []
    for row in result["prs"]:
        before = old.get(row["id"], {})
        awaiting = before.get("awaitingReady", before.get("review") in {"Review required", "Changes requested"})
        if not row.get("error"):
            if row["review"] in {"Review required", "Changes requested"}:
                awaiting = True
            counts = row.get("counts")
            ready = (not result.get("stale") and not result.get("partial")
                     and row["review"] == "Approved" and not row["draft"]
                     and row.get("mergeable") == "MERGEABLE"
                     and row.get("mergeStateStatus") == "CLEAN"
                     and counts is not None
                     and not any(counts[key] for key in ("running", "failed", "unknown")))
            if enabled and awaiting and ready:
                notifications.append(row)
                awaiting = False
        if awaiting or "awaitingReady" in before:
            row["awaitingReady"] = awaiting
    return notifications


def notify_ready(row, deadline):
    """Best-effort local notification; never let a desktop failure break fetching."""
    try:
        subprocess.run(
            ["notify-send", "--app-name=OmaContrib", f"--icon={NOTIFICATION_ICON}",
             "--", "Pull request ready to merge",
             html.escape(f'{row["repository"]} #{row["number"]}: {row["title"]}')],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=min(2, deadline.remaining()), check=False,
        )
    except (OSError, subprocess.TimeoutExpired, FetchError):
        pass


def lifecycle_notifications(result, previous, enabled):
    """Report contribution state advances without flooding on first discovery."""
    if not enabled or not previous.get("attemptedAt"):
        return []
    old = {row["id"]: row for row in previous.get("prs", [])}
    notifications = []
    for row in result["prs"]:
        if row.get("kind") == "submission":
            before = old.get(row["id"])
            if before and before.get("status") != row.get("status"):
                notifications.append(("Submission " + row["status"], row))
        elif row.get("kind") in {"merged", "closed", "tracked", "issue"}:
            raw_id = row["id"].removeprefix(row["kind"] + ":")
            before = old.get(row["id"])
            if not before and raw_id in old and row["kind"] != "tracked":
                notifications.append(("Contribution " + row["kind"], row))
            elif before and before.get("status") != row.get("status"):
                notifications.append((row["status"], row))
    return notifications


def landed_reminder(row):
    landing = row.get("landing") or {}
    return "\nYou can now drop: " + landing["workaround"] if landing.get("landed") and landing.get("workaround") else ""


def notify_lifecycle(title, row, deadline):
    try:
        subprocess.run(
            ["notify-send", "--app-name=OmaContrib", f"--icon={NOTIFICATION_ICON}",
             "--", title, html.escape(f'{row["repository"]} #{row["number"]}: {row["title"]}' + landed_reminder(row))],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=min(2, deadline.remaining()), check=False,
        )
    except (OSError, subprocess.TimeoutExpired, FetchError):
        pass


def run(api, cache, interval=60, force=False, now=time.time, monotonic=time.monotonic,
        notify=False, exclusions=None):
    exclusions = exclusions or {"repositories": [], "owners": []}
    config_fingerprint = Settings.fingerprint(exclusions)
    deadline = Deadline(monotonic)
    api = BudgetAPI(api, deadline)
    try:
        try:
            cache.lock(deadline)
        except FetchError as exc:
            previous = cache.read()
            if previous.get("authFingerprint") != auth_fingerprint():
                previous = {}
            result = copy.deepcopy(previous)
            result.update(schemaVersion=1, prs=previous.get("prs", []), excluded=previous.get("excluded", []),
                          partial=True, stale=True, error=str(exc))
            # Another process owns the lock; do not overwrite its snapshot.
            return finalize(result, previous.get("lastSuccessAt"))
        previous = cache.read()
        fingerprint = api.api.fingerprint() if isinstance(api.api, GitHub) else auth_fingerprint()
        if (previous.get("authFingerprint") != fingerprint
                or ("configFingerprint" in previous and previous.get("configFingerprint") != config_fingerprint)):
            previous = {}
        timestamp = now()
        age = timestamp - previous.get("attemptedAt", 0)
        # Coalesce simultaneous manual requests and respect server backoff even on manual refresh.
        if previous and (previous.get("retryAt", 0) > timestamp or 0 <= age < (3 if force else interval)):
            return finalize(previous, previous.get("lastSuccessAt"))
        try:
            login = reference(api.graphql("query { viewer { login } }")["viewer"]["login"], MAX_NAME)
            if previous.get("account") != login:
                previous = {}
            result = snapshot(api, login, previous, exclusions)
        except (FetchError, KeyError, TypeError, ValueError, AttributeError) as exc:
            auth = isinstance(exc, FetchError) and exc.kind == "auth"
            result = {} if auth else copy.deepcopy(previous)
            result.update(schemaVersion=1, stale=True, partial=result.get("partial", False) or getattr(exc, "kind", "") in {"limit", "deadline"},
                          error=str(exc) if isinstance(exc, FetchError) else "Incomplete GitHub response.")
            result.setdefault("prs", [])
            result.setdefault("excluded", [])
            result["failures"] = min(previous.get("failures", 0) + 1, 1000)
            result["retryAt"] = timestamp + max(getattr(exc, "retry_after", 0), min(900, 60 * 2 ** min(result["failures"] - 1, 4)))
        result.update(attemptedAt=timestamp, authFingerprint=fingerprint,
                      configFingerprint=config_fingerprint,
                      excludedRepositories=[], excludedOwners=[])
        result = finalize(result, previous.get("lastSuccessAt"))
        notifications = ready_notifications(result, previous, notify)
        lifecycle = lifecycle_notifications(result, previous, notify)
        result = finalize(result, previous.get("lastSuccessAt"))
        # Claim delivery before sending while holding the shared lock: no duplicates
        # across monitors or restarts, even if the desktop notification service fails.
        cache.write(result)
        retained = {row["id"] for row in result["prs"]}
        for row in notifications:
            if row["id"] in retained and not result.get("stale"):
                notify_ready(row, deadline)
        for title, row in lifecycle:
            if row["id"] in retained and not result.get("stale"):
                notify_lifecycle(title, row, deadline)
        return result
    finally:
        cache.close()


def merge_account_results(results, available_accounts, exclusions):
    active, hidden, errors = [], [], []
    for result in results:
        account = result.get("account", "")
        if result.get("error"):
            errors.append(("@" + account + ": " if account else "") + result["error"])
        for bucket, target in (("prs", active), ("excluded", hidden)):
            for original in result.get(bucket, []):
                row = copy.deepcopy(original)
                if account:
                    row["account"] = account
                    row["id"] = account + ":" + row["id"]
                target.append(row)
    # Every account evaluates the same tracked links; keep the row with the most context.
    known = {row["url"] for row in active + hidden if row.get("kind") != "tracked"}
    for target in (active, hidden):
        kept = []
        for row in target:
            if row.get("kind") == "tracked":
                if row["url"] in known:
                    continue
                known.add(row["url"])
            kept.append(row)
        target[:] = kept
    limit = MAX_ROWS
    combined = [("prs", row) for row in active] + [("excluded", row) for row in hidden]
    combined.sort(key=lambda pair: pair[1]["updatedAt"], reverse=True)
    combined = combined[:limit]
    active = [row for bucket, row in combined if bucket == "prs"]
    hidden = [row for bucket, row in combined if bucket == "excluded"]
    selected = [result.get("account") for result in results if result.get("account")]
    message = " ".join(dict.fromkeys(errors))[:MAX_TEXT]
    value = {
        "schemaVersion": 1, "accounts": selected, "availableAccounts": available_accounts,
        "prs": active, "excluded": hidden,
        "partial": any(result.get("partial") for result in results),
        "stale": any(result.get("stale") for result in results), "error": message,
        "groups": contribution_groups(exclusions),
        "excludedRepositories": [], "excludedOwners": [],
        "configFingerprint": Settings.fingerprint(exclusions),
    }
    if len(selected) == 1:
        value["account"] = selected[0]
    for key, reducer in (("fetchedAt", max), ("lastSuccessAt", min), ("retryAt", max), ("attemptedAt", max)):
        values = [result.get(key) for result in results if result.get(key) is not None]
        if values:
            value[key] = reducer(values)
        elif any(key in result for result in results):
            value[key] = None
    return finalize(value, value.get("lastSuccessAt"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=int, choices=(20, 60), default=60)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--notify", action="store_true", help="Notify when a previously review-blocked PR is ready to merge")
    parser.add_argument("--exclude-repository", metavar="OWNER/REPOSITORY")
    parser.add_argument("--include-repository", metavar="OWNER/REPOSITORY")
    parser.add_argument("--exclude-owner", metavar="OWNER")
    parser.add_argument("--include-owner", metavar="OWNER")
    parser.add_argument("--enable-account", metavar="LOGIN")
    parser.add_argument("--disable-account", metavar="LOGIN")
    parser.add_argument("--clear-exclusions", action="store_true")
    parser.add_argument("--create-group", metavar="NAME")
    parser.add_argument("--delete-group", metavar="GROUP")
    parser.add_argument("--group-count", nargs=2, metavar=("GROUP", "ON_OR_OFF"))
    parser.add_argument("--move-repository", nargs=2, metavar=("GROUP", "OWNER/REPOSITORY"))
    parser.add_argument("--move-owner", nargs=2, metavar=("GROUP", "OWNER"))
    parser.add_argument("--move-item", nargs=2, metavar=("GROUP", "GITHUB_URL"))
    parser.add_argument("--create-group-and-move", nargs=3, metavar=("SCOPE", "VALUE", "NAME"))
    parser.add_argument("--track", metavar="GITHUB_URL",
                        help="Follow an issue, PR or discussion until it lands here; replaces earlier requirements for it")
    parser.add_argument("--after", metavar="GITHUB_URL", action="append", default=[],
                        help="With --track: another issue, PR or discussion that must close first (repeatable)")
    parser.add_argument("--needs-package", metavar="NAME[>VERSION|>=VERSION]", action="append", default=[],
                        help="With --track: installed package that must carry the fix; a bare NAME means newer than now")
    parser.add_argument("--workaround", metavar="TEXT", help="With --track: what to remove once it lands")
    parser.add_argument("--note", metavar="TEXT", help="With --track: why this matters or where the analysis came from")
    parser.add_argument("--untrack", metavar="GITHUB_URL")
    parser.add_argument("--dismiss", metavar="GITHUB_URL", help="Hide an item for good and stop following it")
    parser.add_argument("--undismiss", metavar="GITHUB_URL", help="Show a dismissed item again")
    parser.add_argument("--status", nargs=2, metavar=("GITHUB_URL", "TEXT"),
                        help="Record a one-line status for a followed item (follows it if needed)")
    parser.add_argument("--by", metavar="NAME", help="With --status: who wrote it (default: the detected agent, else you)")
    parser.add_argument("--session", metavar="GITHUB_URL",
                        help="Remember the calling agent session (agent, id, computer, directory) for an item")
    parser.add_argument("--agent", choices=sorted(AGENTS), help="With --session: override the detected agent")
    parser.add_argument("--session-id", metavar="ID", help="With --session: override the detected session id")
    parser.add_argument("--host", metavar="NAME", help="With --session: override this computer's name")
    parser.add_argument("--cwd", metavar="PATH", help="With --session: override the working directory")
    parser.add_argument("--ask-status", metavar="GITHUB_URL", help="Start a background read-only agent check of a followed item")
    parser.add_argument("--open-session", metavar="GITHUB_URL",
                        help="Reopen the originating session, or start one primed to check the item")
    parser.add_argument("--auto-summary", choices=("on", "off"),
                        help="Keep every active contribution's summary fresh with background agent checks (default on)")
    parser.add_argument("--watch-status", metavar="GITHUB_URL",
                        help="Open a terminal that follows the item's agent check live, or shows its last full report")
    parser.add_argument("--run-status-agent", metavar="GITHUB_URL", help=argparse.SUPPRESS)
    parser.add_argument("--follow-agent", metavar="GITHUB_URL", help=argparse.SUPPRESS)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        config_path = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "omacontrib"
        settings_store = Settings(config_path)
        actions = [
            ("repository", args.exclude_repository, True), ("repository", args.include_repository, False),
            ("owner", args.exclude_owner, True), ("owner", args.include_owner, False),
            ("account", args.enable_account, True), ("account", args.disable_account, False),
        ]
        group_actions = [args.create_group, args.delete_group, args.group_count,
                         args.move_repository, args.move_owner, args.move_item,
                         args.create_group_and_move, args.track, args.untrack, args.dismiss, args.undismiss, args.status, args.session,
                         args.ask_status, args.open_session, args.run_status_agent,
                         args.watch_status, args.follow_agent, args.auto_summary]
        if args.session is None and any(value is not None for value in (args.agent, args.session_id, args.host, args.cwd)):
            parser.error("--agent, --session-id, --host and --cwd describe a --session")
        if args.track is None and (args.after or args.needs_package or args.workaround is not None or args.note is not None):
            parser.error("--after, --needs-package, --workaround and --note describe a --track")
        if sum(value is not None for _, value, _ in actions + [("", value, True) for value in group_actions]) + int(args.clear_exclusions) > 1:
            parser.error("choose one settings action at a time")
        for scope, value, add in actions:
            if value is not None:
                if scope in {"repository", "owner"}:
                    settings_store.move_rule(scope, "excluded" if add else "inbox", value)
                    print(json.dumps(settings_store.read(), separators=(",", ":")))
                    return
                if scope == "account" and add and not settings_store.read()["accounts"]:
                    current = authenticated_accounts()
                    if current:
                        settings_store.add("account", current[0])
                (settings_store.add if add else settings_store.remove)(scope, value)
                print(json.dumps(settings_store.read(), separators=(",", ":")))
                return
        if args.clear_exclusions:
            current = settings_store.read()
            for group in current["groups"][1:]:
                group.update(items=[], repositories=[], owners=[])
            settings_store.write(current)
            print(json.dumps(settings_store.read(), separators=(",", ":")))
            return
        if args.create_group is not None:
            settings_store.create_group(args.create_group)
        elif args.delete_group is not None:
            settings_store.delete_group(args.delete_group)
        elif args.group_count is not None:
            identifier, enabled = args.group_count
            if enabled not in {"true", "false"}:
                parser.error("group count must be true or false")
            settings_store.set_group_count(identifier, enabled == "true")
        elif args.move_repository is not None:
            settings_store.move_rule("repository", args.move_repository[0], args.move_repository[1])
        elif args.move_owner is not None:
            settings_store.move_rule("owner", args.move_owner[0], args.move_owner[1])
        elif args.move_item is not None:
            settings_store.move_rule("item", args.move_item[0], args.move_item[1])
        elif args.create_group_and_move is not None:
            scope, value, name = args.create_group_and_move
            if scope not in {"item", "repository", "owner"}:
                parser.error("group rule scope must be item, repository, or owner")
            settings_store.create_group_and_move(scope, value, name)
        elif args.track is not None:
            try:
                settings_store.track(args.track, args.after, args.needs_package, args.workaround, args.note)
            except ValueError as exc:
                parser.error(str(exc))
        elif args.untrack is not None:
            settings_store.untrack(args.untrack)
        elif args.dismiss is not None or args.undismiss is not None:
            try:
                settings_store.dismiss(args.dismiss or args.undismiss, args.dismiss is not None)
            except ValueError as exc:
                parser.error(str(exc))
        elif args.run_status_agent is not None:
            run_status_agent(settings_store, args.run_status_agent)
            return
        elif args.follow_agent is not None:
            follow_agent(args.follow_agent, settings_store)
            return
        else:
            try:
                if args.status is not None:
                    by = args.by or next((name for name, key in (("claude", "CLAUDE_CODE_SESSION_ID"), ("codex", "CODEX_THREAD_ID"))
                                          if os.environ.get(key)), "you")
                    settings_store.set_status(args.status[0], args.status[1], by)
                elif args.session is not None:
                    settings_store.add_session(args.session, current_session(args.agent, args.session_id, args.host, args.cwd))
                elif args.ask_status is not None:
                    ask_status(settings_store, args.ask_status)
                elif args.open_session is not None:
                    print(json.dumps({"opened": open_session(settings_store, args.open_session)}))
                    return
                elif args.auto_summary is not None:
                    current = settings_store.read()
                    current.pop("autoSummary", None)
                    if args.auto_summary == "off":
                        current["autoSummary"] = False
                    settings_store.write(current)
                elif args.watch_status is not None:
                    find_track(settings_store.read(), args.watch_status)
                    print(json.dumps({"opened": watch_status(args.watch_status)}))
                    return
            except ValueError as exc:
                parser.error(str(exc))
        if any(value is not None for value in group_actions):
            print(json.dumps(settings_store.read(), separators=(",", ":")))
            return

        settings = settings_store.read()
        exclusions = settings
        available = authenticated_accounts()
        selected = settings["accounts"] or available[:1]
        cache_root = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "omacontrib" / "accounts"
        results = []
        if selected:
            for login in selected:
                token = account_token(login)
                results.append(run(GitHub(token), Cache(cache_root / login.lower()), args.interval,
                                   args.force, notify=args.notify, exclusions=exclusions))
        else:
            results.append(run(GitHub(), Cache(cache_root / "default"), args.interval,
                               args.force, notify=args.notify, exclusions=exclusions))
        result = merge_account_results(results, available, exclusions)
        # Only the bar's own refreshes start background agents, never a manual run.
        if args.notify:
            try:
                if auto_summaries(result, settings_store):
                    settings = settings_store.read()
            except (OSError, ValueError) as exc:
                print(f"Automatic summary skipped: {exc}", file=sys.stderr)
        annotate_tracks(result, settings)
        order_rows(result)
    except (OSError, FetchError) as exc:
        result = {"schemaVersion": 1, "prs": [], "excluded": [], "stale": True,
                  "error": str(exc) if isinstance(exc, FetchError) else "Cannot use OmaContrib data. Check directory permissions."}
    # The fingerprint is only for internal cache invalidation.
    sys.stdout.buffer.write(encoded_snapshot(finalize(result, result.get("lastSuccessAt")), public=True))


if __name__ == "__main__":
    main()
