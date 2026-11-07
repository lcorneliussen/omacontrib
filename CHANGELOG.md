# Changelog

## OmaContrib 0.2.0 — 2026-11-07

- Faster mouse-wheel scrolling in the list (260 px per notch), merged from
  GitHub PR Status's `dev` branch.
- Sort contributions automatically by your standing on the repository: an
  **Upstream** group for public repos you only contribute to, **Private** for
  private repos you do not maintain, and **Maintained** for repos you own or
  maintain (not counted in the bar by default). Explicit group rules still win,
  and these buckets fill themselves — they cannot be renamed, deleted, moved
  into, or toggled.
- Show each pull request's provenance read-only: a **Private** tag, whether you
  maintain the repo, and — for a PR opened from one of your forks — where its
  branch lives (`from you/repo:branch`). OmaContrib never touches the fork.
- Prime a freshly opened follow-up agent (the panel's `o`) to answer review
  feedback by pushing to that fork's branch, never the base repository. The
  read-only background status check is unchanged.
- Discover up to 300 authored pull requests (was 100). To stay within GitHub API
  limits, refreshes are incremental: changed or still-running PRs are always
  re-read, while stable ones past a per-refresh budget are served from cache and
  rotated back in by staleness. Below the budget every PR is refreshed as before.

## OmaContrib 0.1.0 — 2026-11-07

First release under the new name and plugin id `community.omacontrib`. It
installs beside GitHub PR Status (`torugo.github-pr-status`) rather than
replacing it.

- Follow contributions past the merge: recently merged PRs and whether the
  latest release contains them, PRs closed without a merge (kept 30 days), and
  issues you opened in other people's repositories.
- `--track` a GitHub issue, PR or discussion with the steps still needed before
  the fix reaches this machine: downstream PRs (`--after`) and pacman package
  versions (`--needs-package`). The row reads **Awaiting …** until it **Landed**,
  and the landing notification reminds you which workaround to drop.
- One-line status per item, written by you, by an agent (`--status`), or by a
  read-only background `claude -p` check (`s`). Watch a check live or read its
  report with `w`; `o` resumes the agent session that worked on the item, on
  this computer or another one through herdr or SSH.
- Summaries refresh themselves when an item's GitHub state changes, at most one
  agent at a time and 24 runs a day (`--auto-summary off` to stop). The agent
  judges who has to act; items waiting on you read **Your move** and sort first.
- Groups: move an item, repository or owner by menu, drag and drop or keys;
  `x` excludes the selected item, `Shift+x` its repository; `d` dismisses a
  closed item for good.
- Row actions use the shell's hover styling and themed tooltips; the selection
  follows the mouse.
- `skill/SKILL.md` teaches agents to register sessions and statuses.
- The helper is now `bin/omacontrib.py`; `bin/github_pr_status.py` is gone.
- Without a `gh` login the panel now asks you to sign in instead of reporting
  GitHub's anonymous rate limit.
- Install with `omarchy plugin add`; the README documents removal.

The entries below are from GitHub PR Status, which OmaContrib is derived from.

## 1.2.0 — 2026-09-10

- Press C on the selected PR to copy its full URL, or Shift+C to copy an
  `owner/repo#123` reference for agent conversations, reviews, and sharing.
- Confirm clipboard actions with a small fading “URL copied” or “Reference
  copied” toast. Copy failures show “Could not copy”; the panel stays open.
- Show only the newest attempt of each workflow job, so superseded CI failures
  no longer block merge readiness. Keep jobs from different workflows and apps
  separate, with conservative handling when workflow metadata is unavailable.
- Add clipboard and keyboard regression coverage and refresh dark/light previews.

Copying requires `wl-copy` from `wl-clipboard` on the shell PATH. Existing Git
installations should pull and rerun `python3 install.py --enable`. If the new
shortcuts do not appear, run `omarchy restart shell` to reload cached QML;
this briefly reloads the bar and desktop overlays.

## 1.1.2 — 2026-09-09

- Fix missing-icon placeholders in ready-to-merge notifications by bundling a
  green pull-request SVG and referencing its absolute path.
- Include the icon in local installations and add regression coverage for
  notification delivery from copied installations, including paths with spaces.

No new requirements or special upgrade steps. The bundled icon is used by new
notifications after updating.

## 1.1.1 — 2026-09-09

- Replace the Open pill with a solid green “✓ Ready to merge” badge when fresh
  data confirms readiness, retaining the subtle card tint and border.
- Allow cancelled checks alongside passing CI when GitHub reports MERGEABLE and
  CLEAN, matching its ready-to-merge state. Drafts, stale/partial data,
  review/merge blockers, and running, failed, or unknown checks still suppress
  the badge.
- Add badge regression coverage and refresh the fictional dark/light previews.

No new requirements. If the old badge persists after updating, run
`omarchy restart shell` to reload cached QML components; this briefly reloads
the bar and desktop overlays.

## 1.1.0 — 2026-09-09

- Notify when a PR previously needing review becomes approved and ready to merge.
  Pending review cycles survive restarts and wait for CI; shared cache state
  prevents duplicate alerts across displays. Requires `notify-send` (libnotify).
- Group stacked PRs under separate headings with indented cards and a connecting
  guide. Order members bottom-to-top using GitHub's actual position/size labels,
  and order groups by their latest visible PR update. Keep the authored/open-only
  scope, including drafts; no `gh stack` extension or local checkout is needed.
- Give merge-ready PRs with passing CI a subtle green tint and border. Stale data,
  review/merge blockers, and running, failed, unknown, or cancelled checks suppress
  the accent.
- Expand regression coverage and refresh the fictional dark/light previews.

If the old layout persists after updating, run `omarchy restart shell` to reload
cached QML components; this briefly reloads the bar and desktop overlays.

## 1.0.1 — 2026-09-07

Security fix for externally influenceable pagination and memory growth during
GitHub PR refreshes.

- Enforce a shared 60-second refresh deadline, including cache lock waiting and
  all API requests.
- Cap discovery at 100 PRs and ten search pages; cap checks and reviews at five
  pages and 500 entries each per PR.
- Bound GitHub CLI output and retained strings; terminate timed-out or oversized
  requests. Validate cached data before reuse and cap serialized snapshots at
  2 MiB.
- Mark capped results incomplete/stale, retain safe previous details or show
  unknown values, and preserve the last complete update timestamp. Omit oldest
  PRs when necessary to fit the snapshot budget.
- Add regression coverage for deadlines, subprocess limits, cache validation,
  and bounded JSON output.

## 1.0.0 — 2026-09-06

First stable release of GitHub PR Status for Omarchy.

- Monitor your authored open pull requests, including drafts, across repositories.
- Show check status bars, approval state, and separate discussion/inline counts.
- Browse individual checks and open PRs using the mouse or keyboard.
- Reuse GitHub CLI authentication, including access to private repositories.
- Share refreshes between displays, paginate checks and reviews, and retain
  explicitly stale results when requests fail.
- Include dark/light previews, regression tests, and Git-based installation docs.

Requires Omarchy's Quattro plugin runtime, Python 3.10+, and GitHub CLI.
Tested desktop: Omarchy 4.0.2 with Qt 6.11.2. Supports the active github.com account;
Enterprise hosts, multiple simultaneous accounts, and GitHub write actions are
outside this release.
