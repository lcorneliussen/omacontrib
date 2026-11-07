# AGENTS.md

Guidance for AI agents working in this repository.

## Deploy model: edit here, install into the plugin dir

This repository is the single source of truth. The **live** Omarchy plugin is a
*copy* at `~/.config/omarchy/plugins/community.omacontrib/`, produced by:

```sh
python3 install.py --enable   # validate, copy files, rescan shell, enable in the right bar section
```

The plugin directory **must not be a symlink** — Omarchy rejects symlinked
plugin dirs (enforced in `install.py`). So the only way to update the running
plugin is to re-run `install.py`; editing the repo alone does not change what
runs. The agent skill in `skill/` *may* be symlinked into an agent skills
directory, since that restriction applies only to the plugin dir.

## Local git hooks that auto-redeploy

This checkout can carry two **local** git hooks that keep the live plugin in
sync with the repo automatically, so you never run with a stale copy:

- `.git/hooks/post-merge` — re-runs `install.py --enable` after a pull/merge.
- `.git/hooks/post-checkout` — re-runs it on real branch switches only (the
  flag argument is `1`), not single-file checkouts.

Both are best-effort (`|| true`) and never block the git operation.

**These hooks are not version-controlled** (they live under `.git/`), so they do
**not** come with a fresh `git clone`, and installing them is persistence that
runs on every pull and branch switch. Whether to install them is the user's
decision.

### What an agent must do about the hooks

1. **Check first** whether they are already installed, and that they actually
   call `install.py` (not some unrelated hook):

   ```sh
   ls -l .git/hooks/post-merge .git/hooks/post-checkout 2>/dev/null
   grep -l install.py .git/hooks/post-merge .git/hooks/post-checkout 2>/dev/null
   ```

2. **If they are absent, ask the user** whether to install them. Do **not**
   install them unprompted.

3. **If the user agrees**, create the two files below and `chmod +x` them.

   `.git/hooks/post-merge`:
   ```bash
   #!/usr/bin/env bash
   # Keep the live Omarchy plugin in sync with this checkout after a pull/merge.
   # Best-effort: never block the git operation.
   cd "$(git rev-parse --show-toplevel)" || exit 0
   python3 install.py --enable || true
   ```

   `.git/hooks/post-checkout`:
   ```bash
   #!/usr/bin/env bash
   # Re-sync the live plugin only on real branch switches (flag=1), not file checkouts.
   [ "$3" = "1" ] || exit 0
   [ "$1" = "$2" ] && exit 0
   cd "$(git rev-parse --show-toplevel)" || exit 0
   python3 install.py --enable || true
   ```

   Then: `chmod +x .git/hooks/post-merge .git/hooks/post-checkout`

If the hooks are **not** installed, remind the user to run
`python3 install.py --enable` after pulling so the live plugin reflects the repo.
