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
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scan_worktrees import (  # noqa: E402
    Worktree, classify, git, human, match_live, newest_mtime, probe, read_lines,
    MAIN, PROTECTED, REVIEW, SAFE, STALE,
)

# How far into the future a liveness file's mtime may sit before it is refused
# outright, rather than merely aged. Genuine clock/filesystem skew -- a file
# collected onto a network mount, an archive restored with its original
# timestamps, a host whose clock is a few minutes fast -- is real and should not
# trip this. But "age" below is computed as (now - written), so a future mtime
# makes it *negative*, which is smaller than every max-age threshold and would
# sail through the "too old" check that exists right next to it. This is the
# *ceiling* on that slack, not the slack itself: the effective tolerance is
# min(this, --liveness-max-age), so the forward window can never be wider than
# the backward one. A user who tightens --liveness-max-age to 1 gets one minute
# of future slack too, and the two sides stay the same size at every flag value.
FUTURE_MTIME_TOLERANCE_MINUTES = 5.0


def positive_minutes(value):
    """argparse type= for --liveness-max-age: reject anything below 1 minute.

    A value < 1 makes ``future_slack`` (``min(FUTURE_MTIME_TOLERANCE_MINUTES,
    max_age_minutes)``) zero or negative, which then rejects an ordinary *past*
    mtime file with the nonsensical message "has a future mtime (-0 min ahead of
    now)". A bad flag value should be told, not silently clamped into something
    that behaves unpredictably.
    """
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer")
    if n < 1:
        raise argparse.ArgumentTypeError(
            f"--liveness-max-age must be at least 1 (got {n})")
    return n


def liveness_objections(files, plan, live_paths, live_names, max_age_minutes):
    """Reasons the supplied liveness data is not evidence about *now*. Empty = fresh.

    The old check was ``bool(live_paths or live_names)``, which enforces "a non-empty
    file exists", not "this data is current". Handing the remover the scan's own
    liveness file passed silently -- the exact mistake this tool exists to prevent,
    because a session that started after the scan and has so far only *read* files
    writes nothing git-visible, so the recency gate is blind to it and the liveness
    gate is the only one left that could catch it.

    Three ways supplied data fails to be about now:
      a) it is the very file the scan already consumed;
      b) it was written before the scan ran, or long enough ago that a session could
         have started since;
      c) it reproduces the plan's own lists exactly, which is what copying the scan's
         file looks like from here.

    (c) can also happen honestly on a quiet machine where genuinely nothing changed.
    That is why it is a refusal with an override rather than a silent pass:
    --assume-no-live-sessions is a claim a human makes, and the file cannot make it.
    """
    objections = []
    scanned_at = plan.get("scanned_at", 0.0)
    consumed = {plan.get("live_paths_file", ""), plan.get("live_names_file", "")} - {""}
    future_slack = min(FUTURE_MTIME_TOLERANCE_MINUTES, max_age_minutes)

    for label, path in files:
        if not path:
            continue
        real = os.path.realpath(os.path.expanduser(path))
        if real in consumed:
            objections.append(f"{label} {path} is the same file the scan read; "
                              "it cannot tell you what started since")
            continue
        try:
            written = os.path.getmtime(real)
        except OSError as exc:
            objections.append(f"{label} {path} cannot be read: {exc}")
            continue
        skew = (written - time.time()) / 60.0
        if skew > future_slack:
            objections.append(f"{label} {path} has a future mtime ({skew:.0f} min ahead "
                              "of now), which is not evidence collected just now -- it "
                              "cannot be aged, so it cannot be trusted")
            continue
        if scanned_at and written < scanned_at:
            age = (scanned_at - written) / 60.0
            objections.append(f"{label} {path} was written {age:.0f} min before the "
                              "scan ran, so it predates the plan it is meant to check")
            continue
        age = (time.time() - written) / 60.0
        if age > max_age_minutes:
            objections.append(f"{label} {path} was collected {age:.0f} min ago; "
                              f"re-collect it (limit {max_age_minutes} min)")

    if ((plan.get("live_paths") or plan.get("live_names"))
            and live_paths == plan.get("live_paths", [])
            and live_names == plan.get("live_names", [])):
        objections.append("the supplied liveness data is identical to the plan's own, "
                          "which is what re-using the scan's file looks like")
    return objections


def plan_objections(plan, max_age_hours):
    """Reasons this plan should not be executed at all. Empty = usable.

    Path, repo and branch out of this JSON go straight into `git worktree remove`,
    `git worktree prune` and `git branch -D`. A plan from three days ago describes a
    machine that no longer exists; a plan from a *different* machine describes
    directories that here belong to something else entirely.
    """
    objections = []
    host = plan.get("hostname", "")
    if host and host != socket.gethostname():
        objections.append(f"plan was produced on {host}, not {socket.gethostname()}")
    scanned_at = plan.get("scanned_at", 0.0)
    if not scanned_at:
        objections.append("plan records no scan time, so its age cannot be checked")
    else:
        age = (time.time() - scanned_at) / 3600.0
        if age > max_age_hours:
            objections.append(f"plan is {age:.1f} h old (limit {max_age_hours} h); "
                              "re-scan rather than acting on it")
    return objections


def reverify(entry, live_paths, live_names, active_within, assume_no_live,
             scanned_at=0.0):
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

    # Recency needs care here. The scan itself ran `git status`, which rewrites the
    # index and bumps its mtime -- so a naive re-measure would report every worktree
    # as touched seconds ago and hold back the entire plan. The scan recorded the
    # mtime its own probing left behind, so a value newer than that is the only
    # evidence of a *third party*. Otherwise carry the scan's reading forward, aged
    # by however long the human spent deciding.
    current = newest_mtime(wt.path)
    if scanned_at and entry.get("post_probe_mtime"):
        # Strict comparison, no slack. The scan recorded this value with the same
        # code on the same filesystem, so any increase is somebody else's write --
        # and a tolerance window here is precisely a window in which a fast
        # scan-then-delete misses a session that started moments ago.
        if current > entry["post_probe_mtime"]:
            wt.idle_minutes = max(0.0, (time.time() - current) / 60.0)
        else:
            elapsed = max(0.0, (time.time() - scanned_at) / 60.0)
            wt.idle_minutes = max(0.0, entry.get("idle_minutes", -1)) + elapsed

    wt.live_reason = match_live(wt, live_paths, live_names)
    classify(wt, have_liveness=bool(live_paths or live_names) or assume_no_live,
             active_within=active_within)
    return wt.verdict, wt.reasons


def remove_one(entry, execute, delete_branch=False):
    """Remove a worktree, escalating only as far as git actually forces us to.

    Three rungs, and the point of the first is that we should never need the others:

    1. ``git worktree remove`` -- plain. Gate 1 already established the tree is clean
       and published, so this should just work. Keeping it means git's own refusal for
       dirty or untracked worktrees still stands as an independent second opinion; the
       previous version passed --force up front and gave that up for nothing.
    2. ``--force`` -- only once git names untracked or modified content as its reason.
       That refusal is not new information here, so overriding it is correct.
    3. ``shutil.rmtree`` + ``git worktree prune`` -- only once git reports it could not
       finish clearing the directory. This is the most dangerous line in the plugin: it
       deletes a path read out of a JSON file. What fences it is everything upstream --
       reverify() re-derives the verdict, "/" and a nonexistent path both come back
       protected or stale, a main checkout is refused twice over, and rmtree itself
       raises rather than following a top-level symlink.

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

    # Rung 1: ask git plainly. Gate 1 already established the tree is clean and
    # published, so this should simply succeed -- which leaves git's own refusal for
    # dirty or untracked worktrees standing as a second opinion behind a gate bug.
    # Passing --force up front, as this did before, disables that check for free.
    rc, out = git(["worktree", "remove", path], cwd=repo)
    note = "removed"
    if rc != 0:
        low = out.lower()
        if "modified or untracked" in low or "contains modified" in low or "untracked files" in low:
            # Rung 2: git objects only to content it knows is disposable here.
            rc, out = git(["worktree", "remove", "--force", path], cwd=repo)
            low = out.lower()
            note = "removed (--force)"
    if rc != 0:
        if "not empty" in low or "contains modified" in low or "failed to delete" in low:
            # Rung 3: git could not finish clearing the directory. Recoverability is
            # already established, so doing the deletion ourselves and pruning the
            # now-dangling registration is the correct recovery.
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


def admission_blocks(plan, live_paths, live_names, args):
    """Every reason --execute would refuse this plan, as (heading, reasons, trailer).

    Empty means the plan is admissible. These three gates -- plan age and hostname,
    liveness data present at all, liveness data fresh -- used to be evaluated only
    under ``--execute``, so the same plan and the same flags printed "Would remove: 3"
    in dry-run and exited 2 with a refusal under --execute. That preview is exactly
    what an agent shows a human when it asks them to approve a deletion, so it has to
    describe what execution would actually do; a preview you must *run* to discover it
    was never going to run is worse than no preview at all.

    Dry-run still shows the table (previewing an old or foreign plan is genuinely
    useful, and nothing is deleted either way) -- it just says so first.
    """
    blocks = []

    stale_plan = plan_objections(plan, args.max_plan_age_hours)
    if stale_plan:
        blocks.append(("refusing to execute this plan:", stale_plan, None))

    if not (live_paths or live_names or args.assume_no_live_sessions):
        blocks.append(("refusing to execute without liveness data.", [],
                       "Re-check what is running right now and pass --live-paths-file "
                       "/ --live-names-file, or state explicitly that you checked with "
                       "--assume-no-live-sessions."))
    elif not args.assume_no_live_sessions:
        # --assume-no-live-sessions is the human saying "I looked, just now". It is
        # the deliberate override, so freshness is only demanded of files.
        stale_liveness = liveness_objections(
            [("--live-paths-file", args.live_paths_file),
             ("--live-names-file", args.live_names_file)],
            plan, live_paths, live_names, args.liveness_max_age)
        if stale_liveness:
            blocks.append(("refusing to execute on stale liveness data:", stale_liveness,
                           "Re-collect what is running right now, or state that you "
                           "checked with --assume-no-live-sessions."))
    return blocks


def print_admission(blocks, stream):
    for heading, reasons, trailer in blocks:
        print(heading, file=stream)
        for reason in reasons:
            print(f"  - {reason}", file=stream)
        if trailer:
            print(trailer, file=stream)


def print_admission_warning(blocks):
    """The dry-run banner: the same gates, the same reasons, printed as a warning.

    On stdout rather than stderr, and above the table rather than after it, because
    stdout is what a caller captures and quotes, and a caveat printed underneath a
    "Would remove: 3" line has already been read as a plan.
    """
    print("⚠️  THIS PLAN WOULD BE REFUSED AT EXECUTE TIME.")
    print("    Re-running this with --execute would delete nothing and exit 2:")
    for heading, reasons, trailer in blocks:
        print(f"      {heading}")
        for reason in reasons:
            print(f"        - {reason}")
        if trailer:
            print(f"      {trailer}")
    print("    The table below is what the plan asks for, not what --execute would do.")


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
    ap.add_argument("--delete-branch", action="store_true",
                    help="also delete each removed worktree's branch; safe only "
                         "because we verified HEAD is published, and it prevents "
                         "the leftover from blocking a later `git worktree add`")
    ap.add_argument("--active-within", type=int, default=None,
                    help="override the scan's idle threshold, in minutes")
    ap.add_argument("--liveness-max-age", type=positive_minutes, default=5,
                    help="how many minutes old the liveness files may be (default: 5, "
                         "minimum: 1). They must also have been written after the scan ran.")
    ap.add_argument("--max-plan-age-hours", type=float, default=24.0,
                    help="refuse a plan older than this many hours (default: 24)")
    ap.add_argument("--execute", action="store_true",
                    help="actually remove; without this nothing is deleted")
    args = ap.parse_args()

    try:
        with open(os.path.expanduser(args.plan)) as fh:
            plan = json.load(fh)
    except FileNotFoundError:
        print(f"plan file not found: {args.plan}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"plan file is not valid JSON: {args.plan} ({exc})", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"could not read plan file {args.plan}: {exc}", file=sys.stderr)
        return 2

    if not isinstance(plan, dict) or "worktrees" not in plan:
        print(f"plan file has an unexpected shape (expected an object with a "
              f"\"worktrees\" key): {args.plan}", file=sys.stderr)
        return 2

    active_within = (args.active_within if args.active_within is not None
                     else plan.get("active_within", 120))

    live_paths = read_lines(args.live_paths_file)
    live_names = read_lines(args.live_names_file)

    blocks = admission_blocks(plan, live_paths, live_names, args)
    if blocks:
        if args.execute:
            # Only the first block, as before. The gates are ordered, and dumping all
            # of them at a refusal buries the one that has to be fixed first.
            print_admission(blocks[:1], sys.stderr)
            return 2
        print_admission_warning(blocks)

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
            # The directory is already gone, so `git worktree remove` would fail with
            # "is not a working tree" -- reporting a failure for work that succeeded.
            # Pruning the registration is the whole job here.
            if not args.execute:
                removed.append((entry, "would prune stale registration"))
                continue
            if os.path.isdir(entry["path"]):
                # It came back between the scan and now; that is not ours to delete.
                held.append((entry, PROTECTED,
                             ["directory reappeared since the scan — something recreated it"]))
                continue
            # `git worktree prune` is repo-wide: it clears every registration whose
            # directory is currently missing, including one parked on a volume that
            # merely happens to be unmounted. Dropping .git/worktrees/<name>/ takes
            # that worktree's HEAD with it, which for a detached worktree can be the
            # last ref keeping its commits reachable. A missing *parent* is the signal
            # that "gone" might mean "not mounted", so refuse rather than guess.
            parent = os.path.dirname(entry["path"].rstrip("/"))
            if parent and not os.path.isdir(parent):
                held.append((entry, PROTECTED,
                             [f"parent directory {parent} does not exist — the volume may "
                              "simply be unmounted, and a repo-wide prune would drop every "
                              "registration on it"]))
                continue
            git(["worktree", "prune"], cwd=entry["repo"])
            # Porcelain and an exact compare: `entry["path"] in still` is a substring
            # test, so a surviving /a/bc registration made /a/b look un-pruned. And the
            # listing's own exit code has to be checked -- ignoring it meant a failed
            # `git worktree list` parsed as an empty `registered` set, so every stale
            # entry silently reported "pruned" whether or not the prune actually ran.
            rc, still = git(["worktree", "list", "--porcelain"], cwd=entry["repo"])
            if rc != 0:
                failed.append((entry, f"could not confirm prune: git worktree list "
                                       f"exited {rc}"))
                continue
            registered = {l[len("worktree "):] for l in still.splitlines()
                          if l.startswith("worktree ")}
            if entry["path"] in registered:
                failed.append((entry, "prune did not clear the registration"))
            else:
                removed.append((entry, "stale registration pruned (freed no disk)"))
            continue

        verdict, reasons = reverify(entry, live_paths, live_names, active_within,
                                    args.assume_no_live_sessions,
                                    plan.get('scanned_at', 0.0))
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
        if blocks:
            print("\n(dry run — nothing was deleted, and --execute would refuse this "
                  "plan outright; see the warning above)")
        else:
            print("\n(dry run — nothing was deleted; re-run with --execute)")

    if failed:
        return 1
    # A caller could not previously tell "removed everything" from "removed nothing
    # because every entry was held back". Both were 0.
    if held and not removed:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
