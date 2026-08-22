---
name: worktree-cleanup
description: Safely find and remove orphaned git worktrees across many local repos to reclaim disk space, without destroying unpushed work or deleting a worktree out from under a running session. Use this whenever worktrees are piling up, someone asks which worktrees are safe to delete, wants to prune or clean up worktrees, is low on disk space and suspects stale checkouts, or asks what worktrees exist across their projects — including vaguer forms like "clean up my repos", "what are all these old checkouts", or "reclaim the disk my worktrees are eating". Do NOT use it for general disk pressure that mentions no worktrees or checkouts — that is usually Docker images, DerivedData or Downloads, and a false trigger here starts a slow $HOME scan that writes to the git index of worktrees other live sessions are using. Use it too before any bulk `git worktree remove` or `rm -rf` over worktree directories, even a small one, because the ordering it enforces — re-verifying at the moment of deletion rather than trusting an earlier scan — is the part that is easy to get wrong and expensive to get wrong.
---

# Worktree cleanup

Agent-driven development leaves worktrees everywhere. They accumulate quietly, each
carrying a full `node_modules`, and a machine can easily hold tens of gigabytes of
them. Reclaiming that space is genuinely useful. It is also one of the few cleanup
tasks that can destroy work that exists nowhere else, so the value is entirely in
doing it precisely.

## The two ways this goes wrong

**Deleting unpushed work.** A worktree with a clean `git status` looks disposable and
often is not. Clean means "no edits since the last commit" — it says nothing about
whether those commits exist anywhere but this disk. Delete the worktree and the
branch it held becomes unreachable. `git status` alone cannot tell you this.

**Deleting a worktree someone is using.** Sessions and agents start and stop
constantly. A worktree that was idle when you scanned can be someone's working
directory ten minutes later. If you scan, present a list, wait for approval, and then
delete, you are acting on a claim about the past.

The second failure is what makes this skill exist. A scan-then-delete run with a
long gap in the middle deleted a worktree belonging to a session that had started in
between. The commits survived, so nothing was permanently lost, but only by luck —
the session had pushed. Ordering, not care, is what prevents it.

## Three independent gates

Each gate catches things the others miss, which is why all three are worth running
rather than picking a favourite.

1. **Recoverability** — is HEAD reachable from any remote-tracking branch, is the
   working tree clean, and what non-git content is in there?
   `git branch -r --contains HEAD` is the real test for commits; upstream/ahead counts
   miss detached HEADs entirely, and detached HEADs are common in agent worktrees.

   Be precise about what this gate is for. Removing a worktree never deletes commits —
   objects live in the common `.git/objects` and survive — so a wrong verdict about
   *commits* costs you a label, not work. What removal destroys irreversibly is the
   directory's non-git content, and the class most likely to be irreplaceable is the
   one git hides: **ignored** files. `git status --porcelain` never lists them, so the
   scanner asks for `--ignored=matching`, subtracts recognisable build output
   (`node_modules/`, `dist/`, `.venv/`, `target/`, `.next/`, `coverage/`, …) and treats
   what remains — `.env`, local dumps, machine-specific config — as data. Data
   downgrades a worktree to `review` and is named in the report under every verdict.

2. **Reported liveness** — is a session or agent working here? Two listings are
   needed because each has a blind spot. Session listings give exact working
   directories but lag behind sessions that just started. Agent listings are current
   but name agents after a directory basename with a suffix appended, so matching is
   by prefix.

3. **Observed recency** — when was this directory last touched? Gates 1 and 2 both
   depend on something being reported correctly, and both can miss a worktree driven
   by a process named after the repo rather than the directory, or one that tooling
   recreates seconds after deletion. Filesystem mtimes depend on nothing being
   reported at all.

   Know what this gate does and does not see. It detects *changes* — files written,
   commits made, branches switched — by looking at git metadata and a few levels of
   source. It does not detect pure inspection: someone reading files, or running
   `git status` against an already-fresh index, leaves no trace. That is a reasonable
   line, since reading a worktree is not a reason to keep it, but do not read a large
   idle time as proof that nobody has the directory open.

## Workflow

### 1. Collect liveness, then scan

Gather what is running *first*, because the scan needs it. Two sources:

- **Working directories** of non-archived sessions — whatever session-listing tool is
  available (`mcp__ccd_session_mgmt__list_sessions` in Claude Code desktop; check what
  the harness offers). Write each `cwd` to a file, one per line.
- **Live agent names** — `ListAgents`. Write each name to a second file.

Then scan:

```bash
python3 scripts/scan_worktrees.py \
  --roots ~/projects ~/work \
  --live-paths-file /tmp/live-paths.txt \
  --live-names-file /tmp/live-names.txt \
  --json /tmp/plan.json
```

Without at least one liveness file the scanner refuses to call anything `safe` — a
scan run in a hurry should not read as permission to delete. If you have genuinely
confirmed nothing is running, say so explicitly with `--assume-no-live-sessions`.

`--roots` defaults to `$HOME`, which is usually too broad and slow; name the
directories that actually hold repos. The scanner finds main checkouts (a `.git`
*directory*) and asks git for their worktrees, so it picks up worktrees parked outside
the repo — siblings, `/tmp`, anywhere — which globbing for `.claude/worktrees` misses.

Verdicts:

| Verdict | Meaning |
|---|---|
| `stale` | Registration whose directory is already gone. Prune freely; there is nothing to lose. |
| `safe` | Clean, published, and nothing suggests it is in use. |
| `review` | Recoverable, but something wants a human eye — ignored files that are not build output, untracked files, unpushed commits on the branch, or no liveness data. |
| `protected` | Would destroy work or break a live session. |
| `main` | The repo's own checkout. Never a candidate. |

### 2. Show the user what you found, grouped by why

Report by *reason*, not just size. "12 GB across 38 worktrees, all clean and pushed"
is a decision someone can make. A flat list of 60 paths is not. Always state what is
being held back and why — the protected set is the most interesting part of the
report, because it tells them where their unpushed work actually is.

**Read out the `ignored data` lines verbatim.** They are the only part of the report
describing something that no remote, no reflog and no `git objects` can bring back. A
user approving a `safe` group has no other way to learn that one of those directories
holds their only `.env`.

Deleting worktrees is destructive and outward-facing enough to need explicit
confirmation. Get it before executing, and get it again if the plan changes.

### 3. Re-verify and remove

```bash
python3 scripts/remove_worktrees.py /tmp/plan.json \
  --live-paths-file /tmp/live-paths-fresh.txt \
  --live-names-file /tmp/live-names-fresh.txt \
  --execute
```

**Re-collect liveness immediately before this step.** Not the file from the scan — a
fresh one, and the remover now enforces that rather than trusting you: it refuses a
liveness file that is the one the scan read, that was written before the scan ran, or
that is older than `--liveness-max-age` (default 5 minutes). It also refuses a plan
older than `--max-plan-age-hours` (default 24) or produced on a different machine.
`--assume-no-live-sessions` remains the explicit override — it is a claim a human
makes, which a file cannot make on their behalf.

Gates 1 and 2 are re-derived from scratch: HEAD is re-read and compared, `git status`
and `git branch -r --contains` run again, and liveness comes from the newly supplied
files. Gate 3 is re-derived only when somebody *else* has written to the directory
since the scan; otherwise the scan's reading is carried forward and aged, because the
scan's own `git status` rewrote the index and a naive re-measure would report every
worktree as touched seconds ago. The failure direction is conservative — a spurious
write holds a worktree back — but it is carried, not recomputed, and worth knowing.

Held-back items are reported with their reason rather than failing the whole run.
Dry-run is the default. Nothing is deleted without `--execute`. Exit codes: `0`
removed something, `1` a removal failed, `2` refused to start, `3` everything was
held back on re-check.

### 4. Report honestly

Report reclaimed space from what was actually removed, not what was planned. If items
were held back, say so and say why — a held-back worktree is a signal that something
was in flight, and the user usually wants to know. If a stale registration was pruned,
note that it freed no space; pruning metadata is not disk cleanup, and conflating the
two overstates the result.

## Useful flags

- `--allow-untracked` — treat worktrees whose only changes are untracked files as
  `review` rather than `protected`. Most such files are `node_modules`, `.DS_Store`,
  and coverage output. It never weakens protection for modified tracked files.
- `--active-within N` — minutes of idleness required before a worktree is considered
  unused (default 120). Lower it on a quiet machine; raise it when many agents run.
- `--allow-dirty` — stop protecting worktrees for having uncommitted changes. Rarely
  correct; it disables the gate that protects unsaved work.
- `--include review stale` on the remover — act on more than just `safe`. Every
  included item is still re-verified, so this widens scope without weakening checks.
- `--only PATH...` — restrict to specific worktrees, for when the user picks from the
  list rather than approving a class.
- `--delete-branch` — also delete the branch each removed worktree held, instead of
  leaving it orphaned.
- `--liveness-max-age N` (remover) — how many minutes old the liveness files may be,
  default 5. They must also post-date the scan.
- `--max-plan-age-hours N` (remover) — refuse a plan older than this, default 24.

## Things that will bite you

**Ignored files are invisible in every ordinary git command you would reach for.**
`git status`, `git status --porcelain` and `git diff` all stay silent about them, so a
worktree holding production credentials in `.env`, a local `*.sqlite`, or a
`settings.local.json` looks exactly as empty as a pristine one. Removal deletes them
and no remote has a copy. The scanner counts them (minus build output) and names them,
but the allowlist is a heuristic: a project that writes real data into a directory
called `build/` or `coverage/` will have it silently subtracted. When a `safe` or
`review` group is about to be deleted and the user cares about a particular worktree,
`ls -a` it before agreeing.

**`git worktree remove` refuses on "Directory not empty."** Untracked build output
blocks it. This is not a safety signal — recoverability was already established — so
the remover falls back to deleting the directory and pruning the registration.

**A deleted worktree can come back.** Tooling that owns a worktree may recreate it
within seconds. If a path reappears after removal, leave it alone and tell the user;
it means something is actively managing it.

**Removing a worktree leaves its branch behind.** The branch keeps pointing at the
same commit, and it will later block `git worktree add` from reusing that name — a
failure that surfaces long after the cleanup, looking unrelated to it. The remover
reports every branch it leaves; `--delete-branch` removes them too, which is safe here
only because the commits were verified published before anything was deleted.

**Sibling worktrees are easy to miss.** Not every worktree lives under
`.claude/worktrees/`. Plenty sit beside the repo with ordinary names and look exactly
like independent clones. Enumerate via `git worktree list`, never by directory naming.

**Remote-tracking refs can be stale.** `--contains` trusts local remote refs, so a
branch deleted on the remote but still present locally reads as published — which is
why the report says "reachable from local ref … (not re-fetched)" rather than
"published". Real loss needs that plus a later `git remote prune` and a `gc`, but when
it matters, `git fetch --prune` first.

**`git worktree prune` is repo-wide, not per-entry.** It clears every registration in
the repo whose directory is currently missing — including one parked on a volume that
merely happens to be unmounted, whose `.git/worktrees/<name>/HEAD` may be the last ref
keeping a detached worktree's commits reachable. The remover refuses to prune a stale
entry whose *parent* directory does not exist, on the grounds that "gone" and "not
mounted" look identical from here.

**Main checkouts are not candidates.** They are reported separately and never
removable, no matter which classes are included.
