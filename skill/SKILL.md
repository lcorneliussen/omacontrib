---
name: omacontrib
description: >
  Follow an upstream contribution through to the user's machine with OmaContrib:
  register the current agent session against a GitHub issue, PR or discussion, declare what
  still has to happen before the fix is usable here (downstream PRs, package
  versions), and record a one-line status. Use when this conversation filed or
  diagnosed an upstream issue/PR/discussion, found a workaround, or the user says "follow
  this", "track this contribution", "register this session", "omacontrib",
  "remind me when this lands", or asks for the status of a followed item.
---

# Follow a contribution with OmaContrib

OmaContrib (an Omarchy bar widget) keeps followed issues, PRs and discussions
visible until the fix reaches this machine. Each item can carry the sessions it came from, so
the user can jump back into this conversation from the bar later, on this
computer or over SSH.

```bash
OC=~/.config/omarchy/plugins/community.omacontrib/bin/omacontrib.py
test -f "$OC" || echo "OmaContrib is not installed"
```

All commands print the settings JSON on success and change only local files
under `~/.config/omacontrib/`. They never touch GitHub.

## 1. Register this session

Run from the directory the work happened in. The agent, session id
(`CLAUDE_CODE_SESSION_ID` / `CODEX_THREAD_ID`), computer name and directory are
detected; this also starts following the item.

```bash
python3 "$OC" --session https://github.com/OWNER/REPO/pull/123
```

Use the URL of the upstream issue, PR or discussion this conversation is about. If the
conversation produced several (an issue plus the fix PR), register the one the
user will want to watch, usually the fix.

## 2. Declare what "landed" means

Only when the session established it. Re-running `--track` replaces the
requirements and keeps the status and sessions.

```bash
python3 "$OC" --track URL \
  --after https://github.com/DOWNSTREAM/REPO/pull/45 \
  --needs-package 'file>=5.49' \
  --workaround "what the user should remove once it lands" \
  --note "why it matters: the symptom the user actually saw"
```

- `--after`: an issue/PR/discussion that must close first, e.g. a dependency bump in the
  program the user actually runs. Repeatable.
- `--needs-package NAME` means newer than the version installed now; prefer
  `NAME>=VERSION` once the carrying release is known. pacman only.
- `--workaround`: name the concrete thing (path, fork, setting) — the bar shows
  it as "You can now drop: …" when everything has landed.

Ask before inventing requirements you did not verify in this session.

## 3. Record a status line

One line, under 200 characters: where it stands and the next thing that has to
happen. It replaces the previous line.

```bash
python3 "$OC" --status URL "Committed upstream 2026-10-03; waiting for file 5.49 in Arch"
```

## Other commands

| Command | Effect |
| --- | --- |
| `--ask-status URL` | Background, read-only `claude -p` check that records a new status line (what `s` does in the panel) |
| `--watch-status URL` | Follow the running check in a terminal, or show the last check's full report (what `w` does) |
| `--open-session URL` | Resume the newest registered session in herdr (or a terminal, over SSH for other computers), or start a new one primed to check the item (what `o` does) |
| `--untrack URL` | Stop following |

Do not run `--open-session` or `--ask-status` unless the user asks: they open
windows or spend tokens.
