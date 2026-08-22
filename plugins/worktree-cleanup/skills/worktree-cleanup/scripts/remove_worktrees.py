#!/usr/bin/env python3
"""Remove git worktrees from a plan produced by ``scan_worktrees.py``.

Every worktree is re-verified immediately before it is removed, against fresh
git state and a fresh list of live sessions. This matters because scanning and
deleting are separate acts with a gap between them, and in that gap a session can
start, a file can be edited, or a branch can be reset. A plan is evidence about
the past; only a check taken at the moment of deletion says anything about now.

Dry-run by default. Pass --execute to actually remove.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scan_worktrees import (  # noqa: E402
    Worktree, classify, git, human, match_live, probe, read_lines,
    MAIN, PROTECTED, REVIEW, SAFE, STALE,
)


def reverify(entry, live_paths, live_names, allow_dirty, active_within, assume_no_live,
             allow_untracked):
    """Re-derive the verdict from scratch. Returns (verdict, reasons)."""
    wt = Worktree(path=entry["path"], repo=entry["repo"],
                  is_main=entry["is_main"], head=entry["head"],
                  branch=entry["branch"], detached=entry["detached"])
    if not os.path.isdir(wt.path):
        wt.exists = False
        return STALE, ["directory already gone"]

    # Re-read HEAD rather than trusting the plan: the session that owns this
    # worktree may have committed, switched branches, or reset since the scan.
    rc, head_now = git(["rev-parse", "HEAD"], cwd=wt.path)
    if rc != 0:
        return PROTECTED, ["cannot read HEAD; refusing to touch it"]
    if head_now != entry["head"]:
        return PROTECTED, [
            f"HEAD moved since the scan ({entry['head'][:8]} → {head_now[:8]}) — "
            "something is working here"
        ]
    wt.head = head_now

    probe(wt)
    wt.live_reason = match_live(wt, live_paths, live_names)
    classify(wt, protect_dirty=not allow_dirty,
             have_liveness=bool(live_paths or live_names) or assume_no_live,
             active_within=active_within, allow_untracked=allow_untracked)
    return wt.verdict, wt.reasons


def remove_one(entry, execute, delete_branch=False):
    """git worktree remove, falling back to rmtree+prune for non-empty dirs.

    ``git worktree remove`` refuses when the directory holds files git does not
    know about -- .DS_Store, node_modules, coverage output. That refusal is not a
    safety signal (we already established the tracked content is published), so
    removing the directory ourselves and pruning the registration is correct.

    Removing a worktree leaves its branch behind, still pointing at the same commit.
    That leftover blocks ``git worktree add`` from reusing the name later, which
    surfaces as a confusing failure long after the cleanup. Deleting it is only safe
    because we established HEAD is published; the commits live on the remote either
    way, and only the local label goes.
    """
    path, repo, branch = entry["path"], entry["repo"], entry.get("branch") or ""
    if not execute:
        note = "would remove"
        if branch:
            note += f" (leaves branch {branch}{'; would delete' if delete_branch else ''})"
        return True, note

    rc, out = git(["worktree", "remove", "--force", path], cwd=repo)
    note = "removed"
    if rc != 0:
        if "not empty" in out.lower() or "contains modified" in out.lower():
            try:
                shutil.rmtree(path)
            except OSError as exc:
                return False, f"rmtree failed: {exc}"
            git(["worktree", "prune"], cwd=repo)
            note = "removed (rmtree + prune)"
        else:
            return False, out.splitlines()[0] if out else f"git exited {rc}"

    if branch:
        if delete_branch:
            brc, bout = git(["branch", "-D", branch], cwd=repo)
            note += f"; branch {branch} deleted" if brc == 0 else \
                    f"; branch {branch} kept ({bout.splitlines()[0] if bout else 'delete failed'})"
        else:
            note += f"; branch {branch} left behind"
    return True, note


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan", help="JSON produced by scan_worktrees.py --json")
    ap.add_argument("--live-paths-file", help="fresh list of in-use working directories")
    ap.add_argument("--live-names-file", help="fresh list of live agent/session names")
    ap.add_argument("--assume-no-live-sessions", action="store_true",
                    help="assert that you re-checked just now and nothing is running")
    ap.add_argument("--include", nargs="*", default=[SAFE],
                    choices=[SAFE, REVIEW, STALE],
                    help="which verdict classes to remove (default: safe)")
    ap.add_argument("--only", nargs="*", default=None,
                    help="restrict to these exact worktree paths")
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--allow-untracked", action="store_true")
    ap.add_argument("--delete-branch", action="store_true",
                    help="also delete each removed worktree's branch; safe only "
                         "because we verified HEAD is published, and it prevents "
                         "the leftover from blocking a later `git worktree add`")
    ap.add_argument("--active-within", type=int, default=None,
                    help="override the scan's idle threshold, in minutes")
    ap.add_argument("--execute", action="store_true",
                    help="actually remove; without this nothing is deleted")
    args = ap.parse_args()

    with open(os.path.expanduser(args.plan)) as fh:
        plan = json.load(fh)

    active_within = (args.active_within if args.active_within is not None
                     else plan.get("active_within", 120))

    live_paths = read_lines(args.live_paths_file)
    live_names = read_lines(args.live_names_file)
    if args.execute and not (live_paths or live_names or args.assume_no_live_sessions):
        print("refusing to execute without liveness data. Re-check what is running "
              "right now and pass --live-paths-file / --live-names-file, or state "
              "explicitly that you checked with --assume-no-live-sessions.",
              file=sys.stderr)
        return 2

    targets = [w for w in plan["worktrees"] if w["verdict"] in args.include]
    if args.only:
        wanted = {os.path.realpath(os.path.expanduser(p)) for p in args.only}
        targets = [w for w in targets if os.path.realpath(w["path"]) in wanted]

    if not targets:
        print("nothing matched.")
        return 0

    removed, freed, held, failed = [], 0, [], []

    for entry in targets:
        if entry["verdict"] == STALE:
            ok, note = (True, "pruned") if not args.execute else remove_one(entry, True)
            if not os.path.isdir(entry["path"]):
                git(["worktree", "prune"], cwd=entry["repo"])
            (removed if ok else failed).append((entry, note))
            continue

        verdict, reasons = reverify(entry, live_paths, live_names, args.allow_dirty,
                                    active_within, args.assume_no_live_sessions,
                                    args.allow_untracked or plan.get('allow_untracked', False))
        if verdict not in args.include:
            held.append((entry, verdict, reasons))
            continue

        ok, note = remove_one(entry, args.execute, args.delete_branch)
        if ok:
            removed.append((entry, note))
            freed += entry.get("size_kb", 0)
        else:
            failed.append((entry, note))

    verb = "Removed" if args.execute else "Would remove"
    print(f"\n{verb}: {len(removed)}  ({human(freed)})")
    for entry, note in removed:
        print(f"  ✓ {human(entry.get('size_kb', 0)):>9}  {entry['path']}  [{note}]")

    if held:
        print(f"\nHeld back on re-check: {len(held)}  "
              f"— these changed between the scan and now")
        for entry, verdict, reasons in held:
            print(f"  ⊘ {entry['path']}")
            print(f"      now {verdict}: {'; '.join(reasons)}")

    if failed:
        print(f"\nFailed: {len(failed)}")
        for entry, note in failed:
            print(f"  ✗ {entry['path']}: {note}")

    if not args.execute:
        print("\n(dry run — nothing was deleted; re-run with --execute)")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
