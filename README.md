# OmaContrib

OmaContrib answers one question from the Omarchy bar: **what happened to the
things I contributed?**

It tracks:

- authored open pull requests across your own and other repositories;
- CI, review decisions, discussion, inline review comments, and merge readiness;
- recently merged external pull requests and whether their merge commit is
  contained in the repository's latest GitHub release tag;
- pull requests closed without a GitHub merge, kept for 30 days because
  maintainers of mirrored projects often apply a patch by hand and close it;
- issues you opened in other people's repositories, open or closed in the
  last 30 days, with their comment count;
- declared landing requirements: what still has to happen after the
  contribution itself before the fix reaches this machine;
- your own Omarchy plugin and theme submission issues, including marketplace
  labels such as `needs-fixes`, `security-review-required`, `validated`,
  `approved-and-verified`, and `listed`.

Out of the box, contributions sort themselves by your standing on each
repository: **Upstream** for public repos you only contribute to, **Private**
for private repos you do not maintain, and **Maintained** for repos you own or
maintain (shown but not counted in the bar). Each row also carries this
provenance read-only — a private tag, whether you maintain the repo, and, for a
pull request opened from one of your forks, where its branch lives.

You can still organize contributions into named groups using individual item,
repository, or owner rules, which take precedence over the automatic buckets.
Each group remains browsable and has its own toggle for whether its items
contribute to the number shown in the bar. The automatic buckets fill
themselves, so they cannot be renamed, deleted, moved into, or toggled.

For `omacom/omarchy`, inclusion in a release is shown as **Shipped in Omarchy**.
For other repositories it is shown as **Released in** the verified tag. A merge
without a containing release is shown as **Merged · awaiting release**.

## Privacy and permissions

OmaContrib uses one or more existing `gh` logins and makes read-only GitHub API requests.
It never posts, reviews, merges, or changes GitHub data. It stores a private local
cache beneath `~/.cache/omacontrib/` and account/exclusion settings beneath
`~/.config/omacontrib/`, with directory/file modes 700/600. Tokens stay in the
GitHub CLI credential store and are never written to OmaContrib files.

Summaries are written by a local `claude -p` run that may only read: `gh`
view/search commands, `git ls-remote`, `pacman -Q`/`-Si` and GitHub pages. They
use your Claude account and its tokens; see
[Automatic summaries](#automatic-summaries) to limit or turn them off.

## Requirements

- Omarchy Quattro plugin runtime
- Python 3.10 or newer
- GitHub CLI authenticated with `gh auth login --hostname github.com`
- `wl-copy` for copying links and references
- optional: [Claude Code](https://claude.com/claude-code) for summaries, `herdr`
  or a terminal for resuming agent sessions

## Install

```sh
omarchy plugin add https://github.com/lcorneliussen/omacontrib --enable
```

Update it with `omarchy plugin update community.omacontrib`.

To work on a checkout instead, run `python3 install.py --enable` in it; it copies
only the plugin's files into the same place.

## Remove

```sh
omarchy plugin remove community.omacontrib
rm -rf ~/.config/omacontrib ~/.cache/omacontrib   # settings, cache and agent logs
```

If you linked the agent skill, remove that link too (for example
`rm ~/.agents/skills/omacontrib`).

## Controls

| Action | Control |
| --- | --- |
| Open or close | Left-click the widget |
| Refresh | Right-click, `r`, or the panel action |
| Select | Up/Down or `j`/`k` |
| Expand checks | Left/Right or `h`/`l` |
| Open on GitHub | Enter, Space, or click the title |
| Copy URL/reference | `c` / `Shift+c` |
| Exclude the selected item, or bring it back from Excluded (also the ⊘ button on each row) | `x` |
| Move the selected item's whole repository to Excluded | `Shift+x` |
| Select groups | `1`–`9`, or click the group name |
| Include a group in the bar count | Click the filled/empty dot beside its name |
| Move one item | Drag its move control onto a group, or click it |
| Assign a repository or owner | Click a card's move control for the scoped actions |
| Create and move in one step | Use **New group** in the move menu, or drop on the new-group field and enter a name |
| Create or delete a group | Use the group controls above the contribution list |
| Select authenticated accounts | Click the account chips |
| Authenticate another account | Click **Add GitHub account** |

Open PRs refresh every minute (every 20 seconds while visible checks are
running). Release containment is refreshed hourly to avoid unnecessary API use.

## Landing: merged is not the same as available

A fix only helps once it reaches the software you run. OmaContrib does not
guess that path; you (or the agent that did the analysis) declare it once, and
the row then stays visible until every step is done:

```sh
python3 bin/omacontrib.py --track https://github.com/file/file/pull/178 \
  --needs-package file \
  --after https://github.com/example/terminal/pull/42 \
  --workaround "wrapper script that runs file -S" \
  --note "seccomp filter kills file on pipes; found while debugging previews"
```

| Option | Meaning |
| --- | --- |
| `--track URL` | Any GitHub issue, pull request or discussion, yours or not. Tracking it again replaces its requirements. |
| `--after URL` | Another issue, pull request or discussion that must close first, such as a downstream dependency bump. Repeatable. |
| `--needs-package NAME` | The installed pacman package must be newer than the upstream version installed right now. A rebuild of the same version does not count. |
| `--needs-package 'NAME>=VERSION'` | Use this once you know which release carries the fix (`>` also works). Repeatable. |
| `--workaround TEXT` | The fork, patch, or setting to remove. Shown on the row and repeated in the landing notification. |
| `--note TEXT` | Why this matters to you, or where the analysis came from. |
| `--untrack URL` | Stop following it. |

The row shows each step (`✓ Closed · not merged → ○ file > 5.48 · installed
5.48-1`), its status reads **Awaiting …** for the first unfinished step, and it
becomes **Landed** with a notification when the last one completes. Linked
GitHub items are refreshed hourly; installed versions are read on every
refresh. A closed step counts as done, because a hand-applied patch and a
rejected one look the same on GitHub: the steps after it are the evidence.
A discussion counts as done once it is closed or has an accepted answer.

### Follow-through: status lines and originating sessions

Every followed row carries a one-line status and the agent sessions it came
from, so a contribution that needs occasional energy can be picked up again
in seconds:

| Key | Action |
| --- | --- |
| `s` | Run a background, read-only `claude -p` check (gh `view`/`search`, `git ls-remote`, `pacman -Q`/`-Si`, GitHub pages only) that records a new status line. While it runs, the row shows the step it is on |
| `w` | Follow that check live in a terminal, or read the last check's full report; afterwards `c` continues the conversation. A check on an item with no registered session also becomes the session `o` resumes |
| `o` | Resume the newest registered session (`claude --resume` / `codex resume`) in a new herdr workspace in its directory; a session on another computer opens through the herdr machine with that label, or `ssh -t` in a terminal. With no session, starts a fresh agent primed to check the item |

```sh
omacontrib.py --session URL          # run inside an agent: remembers agent, session id, computer, directory
omacontrib.py --status URL "Committed upstream; waiting for file 5.49 in Arch"
omacontrib.py --ask-status URL       # same as s
omacontrib.py --watch-status URL     # same as w
omacontrib.py --open-session URL     # same as o
omacontrib.py --dismiss URL          # same as d on a closed row; --undismiss URL shows it again
```

Every other row gets the same treatment without being followed: `s` asks an
agent what it is waiting for (an open item) or what happened to it (applied by
hand, superseded, rejected), `o` discusses it in its originating session or a
fresh agent, and on a closed pull request or issue `d` dismisses it for good.
Asking or discussing records the item for its summary and sessions only;
`--track` makes it a followed item with landing steps.

### Automatic summaries

Each summary remembers the GitHub state it describes: state, review, comment
counts, last update, finished checks and landing steps. When that state changes
and then holds for ten minutes, the bar starts a fresh check by itself; until it
finishes, the old summary is marked **outdated**. Items without a summary get one
right away. A status you write with `--status` counts as current.

The agent also judges who has to act. Items waiting on you read **Your move** and
sort first in their group; everything else follows, most recently changed first.

Only active (counted) rows are summarized, one agent at a time and at most 24
runs a day. Turn it off or on again with:

```sh
omacontrib.py --auto-summary off
```

`skill/SKILL.md` teaches agents to register themselves. It is not part of the
installed plugin; link it into your agent skills directory, for example
`ln -s "$PWD/skill" ~/.agents/skills/omacontrib`. Background check output is
kept in `~/.cache/omacontrib/agent/`.

Only pacman packages are checked. AUR builds from source, npm, PyPI, crates.io
and similar registries are not inferred.

## Development

```sh
python3 -m unittest discover -s tests -v
omarchy plugin validate .
python3 tests/check_qml.py
python3 tests/check_ui.py
python3 tests/check_box.py   # the real panel in an OmaBox desktop: moves, drag & drop, exclude, dismiss
```

OmaContrib is derived from
[GitHub PR Status](https://github.com/victorhsb/omarchy-github-pr-status), whose
careful cache, pagination, resource-limit, and QML behavior remain covered by the
inherited test suite. MIT licensed; see `LICENSE`.
