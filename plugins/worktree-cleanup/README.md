# worktree-cleanup

Reclaims disk space from orphaned git worktrees across many local repos, without
destroying unpushed work or deleting a worktree out from under a running session.

Agent-driven development leaves worktrees everywhere, each carrying its own
`node_modules`. Tens of gigabytes accumulate quickly. The hard part is not finding
them — it is telling apart the ones that are genuinely disposable from the ones
holding the only copy of some commits, or the one a session started using ten minutes
ago.

## Three gates

A worktree is only removable when all three agree, and each catches things the others
miss:

1. **Recoverability** — is HEAD reachable from a remote branch, and is the tree clean?
   The only gate that protects against permanent loss. `git status` alone does not
   answer this, and upstream/ahead counts miss detached HEADs entirely.
2. **Reported liveness** — is a session or agent working here? Session listings and
   agent listings each have a blind spot, so both are used.
3. **Observed recency** — when was the directory last touched? Depends on nothing
   being reported correctly, which is why it catches what the first two miss.

On a real machine during development, gate 1 held back 25 worktrees, gate 2 caught 5
more that gate 1 called safe, and gate 3 caught 2 that both others missed. Any single
gate alone would have destroyed something.

## The ordering matters more than the checks

Scanning and deleting are separate acts. A worktree idle at scan time can be someone's
working directory by the time you delete it. So `remove_worktrees.py` re-derives every
verdict from current git state and current liveness at the moment of removal, and
holds back anything that changed — a tree gone dirty, a session that claimed it, a
HEAD that moved.

Dry-run is the default, and the remover refuses to execute at all without fresh
liveness data.

## Usage

Claude drives this through the bundled skill; the scripts are also usable directly:

```bash
python3 scan_worktrees.py --roots ~/projects \
  --live-paths-file /tmp/live.txt --json /tmp/plan.json

python3 remove_worktrees.py /tmp/plan.json \
  --live-paths-file /tmp/live-fresh.txt --execute
```

Both are Python 3 stdlib only, no dependencies.

## Install

```bash
/plugin marketplace add ~/claude-plugins
/plugin install worktree-cleanup@mskuibida-tooling
```
