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

1. **Recoverability** — is HEAD reachable from a remote branch, is the tree clean, and
   what non-git content is in there? `git status` alone answers none of it: it misses
   detached HEADs (as do upstream/ahead counts), and it never lists **ignored** files.
   That last omission matters most. Removing a worktree never deletes commits — they
   live in the common `.git/objects` — so ignored files are the only class that
   removal destroys irreversibly. The scanner asks for them explicitly, subtracts
   recognisable build output (`node_modules/`, `dist/`, `build/`, `.venv/`, `target/`,
   `coverage/`, …) and holds back what remains, naming it in the report.
2. **Reported liveness** — is a session or agent working here? Session listings and
   agent listings each have a blind spot, so both are used.
3. **Observed recency** — when was the directory last touched? Depends on nothing
   being reported correctly, which is why it catches what the first two miss.

## The ordering matters more than the checks

Scanning and deleting are separate acts. A worktree idle at scan time can be someone's
working directory by the time you delete it. So `remove_worktrees.py` re-derives the
recoverability and liveness verdicts from current git state and newly supplied
liveness at the moment of removal, and holds back anything that changed — a tree gone
dirty, a session that claimed it, a HEAD that moved. (Recency is re-derived only when
a *third party* has written since the scan; otherwise the scan's reading is carried
forward and aged, because the scan's own `git status` bumps the index mtime and a
naive re-measure would hold back everything.)

Dry-run is the default, and "fresh liveness data" is enforced rather than assumed: the
remover refuses a liveness file that is the one the scan read, that predates the scan,
or that is more than `--liveness-max-age` minutes old, and refuses a plan older than
`--max-plan-age-hours` or produced on another machine. `--assume-no-live-sessions` is
the explicit override — a claim a human makes, which a file cannot make for them.

## Usage

Claude drives this through the bundled skill; the scripts are also usable directly:

```bash
python3 scan_worktrees.py --roots ~/projects \
  --live-paths-file /tmp/live.txt --json /tmp/plan.json

python3 remove_worktrees.py /tmp/plan.json \
  --live-paths-file /tmp/live-fresh.txt --execute
```

Both are Python 3 stdlib only, no dependencies.

## Tests

```bash
bash tests/regression.sh
```

104 assertions over a throwaway two-repo fixture covering every risk class:
classification, ignored data held back while build output is not, the refusal to act
without liveness data, the happy path, four race conditions injected between scan and
removal (each asserting *which* gate caught it, not merely that something did), stale
and foreign plans, liveness files that are stale, copied, dated into the future (both
the boundary the tolerance shrinks to and the ceiling it can never grow past), or
dated with an out-of-range `--liveness-max-age` (rejected by argparse, not silently
clamped), both branch-handling paths, both branches of the `rmtree` fallback, a prune
whose confirming `git worktree list` fails, the refusal of any `--allow-dirty` /
`--allow-untracked` override on both scripts, an older plan (carrying the retired
`allow_*` keys and a stale `review` verdict) degrading safely rather than being
trusted, a missing or malformed plan file failing cleanly instead of with a
traceback, and units a fixture cannot reach.

Each asserts on the fixture's final on-disk state or on the remover's own output,
rather than on what a summary claims. Two caveats stated plainly, because the previous
revision of this file claimed coverage the suite did not have:

- The `rmtree` fallback's **success** path is reached by injecting a `git worktree
  remove` failure with a shim on `PATH`. Nothing that stops git from clearing a
  directory leaves `shutil.rmtree` able to clear it either, so the condition cannot be
  staged from the filesystem. What the test covers is our fallback — the branch taken,
  the note reported, the directory and registration gone — not git's deletion. The
  **failure** path (`rmtree` raising) is tested against a top-level symlink, which
  `shutil.rmtree` refuses to follow by design. That was chosen over an unreadable
  directory because a permissions-based failure does not reproduce under a root CI
  runner, where every directory is readable regardless of its mode bits.
- The suite runs in CI on every push and PR (`.github/workflows/regression.yml`), but
  `main` has no branch protection, so a passing run is not currently required before a
  merge can happen — it reports, it does not gate.

## Install

```bash
/plugin marketplace add ~/claude-plugins
/plugin install worktree-cleanup@mskuibida-tooling
```
