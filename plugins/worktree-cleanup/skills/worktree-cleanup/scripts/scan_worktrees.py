#!/usr/bin/env python3
"""Discover git worktrees across many repos and classify them by deletion risk.

Emits a human-readable table on stdout and a machine-readable plan as JSON
(``--json``) that ``remove_worktrees.py`` consumes.

The classification is deliberately conservative: a worktree is only ever called
``safe`` when its HEAD commit is reachable from some remote-tracking branch AND
its working tree is clean AND nothing suggests a session is using it. Anything
we cannot prove is recoverable gets held back for a human to look at.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict

GIT = shutil.which("git") or "/usr/bin/git"
DU = shutil.which("du") or "/usr/bin/du"

# Verdicts, ordered from most to least dangerous to act on.
STALE = "stale"          # registration points at a directory that no longer exists
MAIN = "main"            # the repo's own checkout; never a cleanup candidate
PROTECTED = "protected"  # deleting this could destroy work or break a live session
REVIEW = "review"        # recoverable, but something suggests it is still wanted
SAFE = "safe"            # clean, pushed, unused


def run(args, cwd=None, timeout=120):
    """Run a command, returning (rc, output). Never raises on non-zero exit.

    On success the output is stdout alone, so callers can parse it. On failure it
    is stdout plus stderr, because git reports *why* it failed on stderr and every
    caller that inspects failed output wants the reason -- notably the
    "Directory not empty" that selects the rmtree fallback in remove_worktrees.py,
    which was previously matched against stdout and so could never fire.
    """
    try:
        p = subprocess.run(
            args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, text=True, timeout=timeout,
        )
        out = p.stdout.strip()
        if p.returncode != 0:
            out = "\n".join(part for part in (out, p.stderr.strip()) if part)
        return p.returncode, out
    except (subprocess.TimeoutExpired, OSError) as exc:
        return 1, f"__error__ {exc}"


def git(args, cwd, timeout=120):
    return run([GIT] + args, cwd=cwd, timeout=timeout)


@dataclass
class Worktree:
    path: str
    repo: str
    is_main: bool = False
    exists: bool = True
    head: str = ""
    branch: str = ""          # empty means detached HEAD
    detached: bool = False
    dirty_count: int = 0        # tracked files modified/staged/deleted
    untracked_count: int = 0    # files git does not know about and does not ignore
    ignored_count: int = 0      # ignored entries that are NOT recognisable build output
    ignored_sample: list = field(default_factory=list)
    dirty_sample: list = field(default_factory=list)
    on_remote: list = field(default_factory=list)   # remote refs containing HEAD
    upstream: str = ""
    ahead: int = 0
    size_kb: int = 0
    idle_minutes: float = -1  # minutes since anything last touched it; -1 = unknown
    post_probe_mtime: float = 0.0  # mtime our own probing left behind, so a later
                                   # re-check can tell our footprint from someone else's
    live_reason: str = ""     # non-empty when a session/agent appears to be using it
    verdict: str = SAFE
    reasons: list = field(default_factory=list)


def find_repos(roots, maxdepth):
    """Return main checkouts under ``roots``.

    A main checkout has ``.git`` as a *directory*; a linked worktree has ``.git``
    as a *file* pointing back into the main repo. Enumerating only main checkouts
    and then asking git for its worktrees is both faster and more complete than
    globbing for worktree directories -- it finds worktrees parked anywhere,
    including siblings of the repo and paths under /tmp.
    """
    repos = []
    for root in roots:
        root = os.path.abspath(os.path.expanduser(root))
        if not os.path.isdir(root):
            continue
        base_depth = root.rstrip("/").count("/")
        for dirpath, dirnames, _ in os.walk(root):
            depth = dirpath.rstrip("/").count("/") - base_depth
            # Recognise the repo BEFORE applying the depth cut-off. Testing depth
            # first skipped a repo sitting at exactly --maxdepth, which the flag's
            # own help text promises to reach.
            if ".git" in dirnames:
                repos.append(dirpath)
                dirnames[:] = []  # do not recurse into a repo looking for more repos
                continue
            if depth >= maxdepth:
                dirnames[:] = []
                continue
            # Never descend into these; they are big and never contain repos we want.
            dirnames[:] = [
                d for d in dirnames
                if d not in {"node_modules", ".venv", "venv", "Library", ".Trash",
                             "vendor", "target", "build", "dist", ".next", ".cache"}
            ]
    return sorted(set(repos))


def parse_worktree_list(repo):
    """Parse ``git worktree list --porcelain`` into Worktree records."""
    rc, out = git(["worktree", "list", "--porcelain"], cwd=repo)
    if rc != 0:
        return []
    items, cur = [], None
    for line in out.splitlines():
        if line.startswith("worktree "):
            if cur:
                items.append(cur)
            cur = Worktree(path=line[len("worktree "):], repo=repo)
        elif cur is None:
            continue
        elif line.startswith("HEAD "):
            cur.head = line[len("HEAD "):]
        elif line.startswith("branch "):
            cur.branch = line[len("branch "):].replace("refs/heads/", "", 1)
        elif line == "detached":
            cur.detached = True
        elif line == "prunable" or line.startswith("prunable "):
            cur.exists = False
    if cur:
        items.append(cur)
    if items:
        items[0].is_main = True
    return items


def measure(path):
    rc, out = run([DU, "-sk", path], timeout=300)
    if rc != 0:
        return 0
    try:
        return int(out.split()[0])
    except (ValueError, IndexError):
        return 0


SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "dist", "build", "target",
             ".next", ".cache", "coverage", "__pycache__", ".pytest_cache"}


def newest_mtime(wt_path, depth=3):
    """Newest mtime across a worktree's git metadata and its source files.

    Git metadata alone is not enough. A bare `git status` does not rewrite an index
    that is already fresh, and editing a nested file does not bump the root
    directory's mtime -- so a worktree someone is actively editing can look
    untouched. Walking a few levels of source catches the thing that actually
    matters, which is somebody changing files. Heavy generated directories are
    skipped: they are large, they churn for reasons unrelated to human work, and
    walking them would dominate the cost.
    """
    newest = 0.0

    candidates = [wt_path]
    dotgit = os.path.join(wt_path, ".git")
    try:
        if os.path.isfile(dotgit):
            candidates.append(dotgit)
            with open(dotgit) as fh:
                content = fh.read().strip()
            if content.startswith("gitdir:"):
                gitdir = content[len("gitdir:"):].strip()
                candidates += [os.path.join(gitdir, n) for n in ("index", "HEAD", "logs/HEAD")]
    except OSError:
        pass
    for c in candidates:
        try:
            newest = max(newest, os.path.getmtime(c))
        except OSError:
            continue

    base = wt_path.rstrip("/").count("/")
    try:
        for dirpath, dirnames, filenames in os.walk(wt_path):
            # Stat the skipped directories themselves -- one syscall each, no walk.
            # A worktree mid-`npm install` or mid-build writes *only* into these, and
            # is git-clean while it does it because they are ignored. Without this the
            # recency gate reads a directory with a build running in it as idle.
            for d in dirnames:
                if d in SKIP_DIRS:
                    try:
                        newest = max(newest, os.path.getmtime(os.path.join(dirpath, d)))
                    except OSError:
                        continue
            if dirpath.rstrip("/").count("/") - base >= depth:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                try:
                    newest = max(newest, os.path.getmtime(os.path.join(dirpath, name)))
                except OSError:
                    continue
    except OSError:
        pass

    return newest


def idle_minutes(wt_path):
    """Minutes since anything last touched this worktree, or -1 if unknowable.

    Session and agent listings can both miss a user: a worktree may be driven by a
    process whose name matches the repo rather than the directory, or recreated by
    tooling moments after deletion. Recency of use is an independent signal that
    does not depend on any of that being reported correctly -- git touches the
    per-worktree index and HEAD on essentially every operation, so a fresh mtime
    there means something is working in this directory right now.
    """
    newest = newest_mtime(wt_path)
    if newest == 0.0:
        return -1
    return max(0.0, (time.time() - newest) / 60.0)


# Ignored content that is reproducible by re-running a build or an installer.
# Reclaiming exactly this is the point of the tool, so it must not hold anything
# back. Everything else that git ignores -- .env, local database dumps,
# machine-specific config -- is the one class of file that worktree removal
# destroys irreversibly, because it exists nowhere but this directory.
BUILD_OUTPUT_DIRS = {"node_modules", "dist", "build", ".venv", "venv", "target",
                     ".next", "coverage", "__pycache__", ".pytest_cache", ".cache",
                     ".gradle", ".tox", ".mypy_cache", ".ruff_cache", ".turbo"}


def is_build_output(rel_path):
    """True when an ignored entry is regenerable build output rather than data."""
    parts = [p for p in rel_path.strip("/").split("/") if p]
    return any(p in BUILD_OUTPUT_DIRS for p in parts)


def parse_status(lines):
    """Split `git status --porcelain --ignored=matching` into (dirty, untracked, ignored).

    Ignored entries carry the `!!` prefix. git collapses a wholly-ignored directory
    into a single entry (`!! node_modules/`) rather than listing its contents, so
    asking for ignored files costs one line per ignore rule, not one per file.
    """
    dirty, untracked, ignored = [], [], []
    for line in lines:
        if line.startswith("!!"):
            rel = line[2:].strip()
            if not is_build_output(rel):
                ignored.append(rel)
        elif line.startswith("??"):
            untracked.append(line)
        else:
            dirty.append(line)
    return dirty, untracked, ignored


def probe(wt: Worktree) -> Worktree:
    """Fill in the facts we need to judge whether removing ``wt`` is safe."""
    if not os.path.isdir(wt.path):
        wt.exists = False
        return wt

    # Read idle time FIRST. `git status` refreshes and rewrites the index, which
    # bumps its mtime to now -- so probing in the other order makes every worktree
    # look like it was touched a moment ago, and the recency gate would be measuring
    # nothing but its own footprint.
    wt.idle_minutes = idle_minutes(wt.path)

    # `--ignored=matching` is not optional decoration. A plain `git status
    # --porcelain` never lists ignored files, so without it a worktree holding a
    # .env full of production credentials is indistinguishable from an empty one
    # and classifies as safe -- while a stray untracked .DS_Store is enough to
    # protect a worktree. Removal destroys the .env and nothing ever mentions it.
    rc, out = git(["status", "--porcelain", "--ignored=matching"], cwd=wt.path)
    if rc == 0 and out:
        dirty, untracked, ignored = parse_status(out.splitlines())
        wt.untracked_count = len(untracked)
        wt.dirty_count = len(dirty)
        wt.dirty_sample = (dirty + untracked)[:5]
        wt.ignored_count = len(ignored)
        wt.ignored_sample = ignored[:5]
    elif rc != 0:
        # A repo we cannot interrogate is a repo we must not delete.
        wt.dirty_count = -1

    # The decisive question: does this commit exist anywhere other than this disk?
    # `--contains` walks history, so a HEAD contained in a remote branch means every
    # ancestor is published too. Detached HEADs have no upstream, which is exactly
    # why an upstream/ahead check alone is not enough to protect them.
    rc, out = git(["branch", "-r", "--contains", wt.head or "HEAD"], cwd=wt.path)
    if rc == 0 and out:
        # Drop symbolic entries like "origin/HEAD -> origin/main"; the branch they
        # point at is listed separately and reads far better in a report.
        wt.on_remote = [l.strip() for l in out.splitlines()
                        if l.strip() and "->" not in l]

    if not wt.detached:
        rc, up = git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=wt.path)
        if rc == 0 and up:
            wt.upstream = up
            rc, cnt = git(["rev-list", "--count", f"{up}..HEAD"], cwd=wt.path)
            if rc == 0 and cnt.isdigit():
                wt.ahead = int(cnt)

    wt.size_kb = measure(wt.path)
    wt.post_probe_mtime = newest_mtime(wt.path)
    return wt


def match_live(wt: Worktree, live_paths, live_names):
    """Decide whether a session or agent appears to be using this worktree.

    Two signals, because neither alone is complete. Session listings give exact
    working directories but lag behind sessions that just started; agent listings
    are current but identify agents by name, and those names are derived from the
    directory basename with a short suffix. Matching both ways costs nothing and
    each covers the other's blind spot.
    """
    real = os.path.realpath(wt.path)
    for p in live_paths:
        pr = os.path.realpath(os.path.expanduser(p))
        if pr == real or pr.startswith(real + os.sep):
            return f"session cwd: {p}"

    base = os.path.basename(wt.path.rstrip("/"))
    for n in live_names:
        # Agent names look like "<dirname>-<suffix>"; require the basename to be a
        # real prefix so that "unit-a" does not swallow "unit-app-something".
        if n == base or n.startswith(base + "-"):
            return f"agent name: {n}"
    return ""


def classify(wt: Worktree, have_liveness=True, active_within=120):
    """Assign a verdict. Uncommitted work is never something this tool resolves.

    There is deliberately no --allow-dirty / --allow-untracked escape hatch. Both
    used to exist, and both amounted to the tool deciding on a human's behalf that
    some unsaved work was disposable -- a decision it has no way to make correctly,
    because "untracked" covers node_modules and the only copy of a migration script
    equally. A worktree with modified tracked files or untracked files is protected,
    the reason says what to resolve, and the human commits, stashes or deletes it
    and re-scans. That keeps every deletion this tool performs recoverable from a
    remote, which is the property the whole design rests on.
    """
    reasons, soft = [], []

    if not wt.exists:
        wt.verdict = STALE
        wt.reasons = ["directory is gone; only stale git metadata remains"]
        return wt

    if wt.is_main:
        wt.verdict = MAIN
        wt.reasons = ["main checkout of the repository"]
        return wt

    if wt.live_reason:
        reasons.append(f"in use — {wt.live_reason}")

    if 0 <= wt.idle_minutes < active_within:
        reasons.append(f"touched {wt.idle_minutes:.0f} min ago — something is using it")

    if wt.dirty_count < 0:
        reasons.append("could not read git status; treating as unsafe")
    elif wt.dirty_count > 0:
        reasons.append(f"{wt.dirty_count} uncommitted change(s) to tracked files — "
                       "commit, stash or discard them, then re-scan")

    if wt.untracked_count > 0:
        # Untracked files are usually node_modules and .DS_Store, but "usually" is not
        # a basis for deleting them, and nothing here can tell those apart from the
        # only copy of something. Resolving it is the human's call, not a flag's.
        reasons.append(f"{wt.untracked_count} untracked file(s) — build output, or "
                       "unsaved work; clean or commit them, then re-scan")

    if wt.ignored_count > 0:
        shown = ", ".join(wt.ignored_sample[:3])
        more = f" (+{wt.ignored_count - 3} more)" if wt.ignored_count > 3 else ""
        # Deliberately soft, not protective. Protecting on all ignored content would
        # make nothing ever safe, and reclaiming node_modules is the entire point --
        # so recognisable build output is subtracted first (see BUILD_OUTPUT_DIRS)
        # and only what is left, which is data, gets a human's attention.
        soft.append(f"{wt.ignored_count} git-ignored file(s) not recognised as build "
                    f"output — deleted permanently and recoverable from nowhere: "
                    f"{shown}{more}")

    if not wt.on_remote:
        where = "detached HEAD" if wt.detached else f"branch {wt.branch}"
        reasons.append(f"HEAD ({where}) is on no remote branch — deleting loses these commits")

    if reasons:
        wt.verdict = PROTECTED
        # Soft reasons ride along rather than being dropped. They are facts about
        # this worktree, not a verdict -- and the ignored-data one especially is
        # the thing a human needs before approving any deletion here. The table
        # prints it from ignored_count either way, but a consumer reading
        # `reasons` straight out of the plan JSON used to lose it entirely.
        wt.reasons = reasons + soft
        return wt

    # Prefer the remote ref matching this worktree's own branch; when several refs
    # contain HEAD, naming an unrelated one reads as a mistake even when it is true.
    own = [r for r in wt.on_remote if wt.branch and r.endswith("/" + wt.branch)]
    published = ("no tracked-file changes; HEAD reachable from local ref "
                 f"{(own or wt.on_remote)[0]} (not re-fetched)")

    if wt.ahead > 0:
        soft.append(f"{wt.ahead} commit(s) ahead of {wt.upstream} "
                    "(reachable elsewhere, but unpushed on this branch)")

    if soft:
        wt.verdict = REVIEW
        wt.reasons = [published] + soft
        return wt

    # Without liveness data we know this worktree is *recoverable*, but not that it
    # is *unused* -- and a worktree deleted out from under a running session breaks
    # that session even though every commit survives. Refusing to say "safe" here is
    # what keeps a scan run in a hurry from reading as permission to delete.
    if not have_liveness:
        wt.verdict = REVIEW
        wt.reasons = [published,
                      "no liveness data supplied — cannot rule out an active session"]
        return wt

    wt.verdict = SAFE
    wt.reasons = [published]
    return wt


def read_lines(path):
    if not path:
        return []
    with open(os.path.expanduser(path)) as fh:
        return [l.strip() for l in fh if l.strip() and not l.startswith("#")]


def human(kb):
    mb = kb / 1024
    return f"{mb/1024:.2f} GB" if mb >= 1024 else f"{mb:.0f} MB"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--roots", nargs="*", default=[os.path.expanduser("~")],
                    help="directories to search for git repos (default: $HOME)")
    ap.add_argument("--maxdepth", type=int, default=4,
                    help="how deep under each root to look for repos (default: 4)")
    ap.add_argument("--live-paths-file",
                    help="file of working directories currently in use, one per line")
    ap.add_argument("--live-names-file",
                    help="file of live agent/session names, one per line")
    ap.add_argument("--assume-no-live-sessions", action="store_true",
                    help="assert that you checked and nothing is running; without this "
                         "or a liveness file, nothing is ever classified safe")
    ap.add_argument("--active-within", type=int, default=120,
                    help="protect worktrees touched within this many minutes (default: 120)")
    ap.add_argument("--json", help="write the full classified plan here")
    ap.add_argument("--quiet", action="store_true", help="suppress the table")
    args = ap.parse_args()

    live_paths = read_lines(args.live_paths_file)
    live_names = read_lines(args.live_names_file)

    repos = find_repos(args.roots, args.maxdepth)
    if not args.quiet:
        print(f"Scanning {len(repos)} repo(s) under {', '.join(args.roots)}…", file=sys.stderr)

    worktrees = []
    for repo in repos:
        worktrees.extend(parse_worktree_list(repo))

    with ThreadPoolExecutor(max_workers=8) as pool:
        worktrees = list(pool.map(probe, worktrees))

    # An empty or missing file means "I did not look", which is not the same claim as
    # "I looked and nothing is running" -- the second needs saying out loud.
    have_liveness = bool(live_paths or live_names) or args.assume_no_live_sessions
    for wt in worktrees:
        wt.live_reason = match_live(wt, live_paths, live_names)
        classify(wt, have_liveness=have_liveness, active_within=args.active_within)

    order = {STALE: 0, SAFE: 1, REVIEW: 2, PROTECTED: 3, MAIN: 4}
    worktrees.sort(key=lambda w: (order[w.verdict], -w.size_kb))

    if not args.quiet:
        if not have_liveness:
            print("\n!! No liveness data was supplied, so nothing is classified as safe.",
                  file=sys.stderr)
            print("   Collect the in-use directories and agent names first, then re-run",
                  file=sys.stderr)
            print("   with --live-paths-file / --live-names-file.\n", file=sys.stderr)

        for verdict, title in ((STALE, "STALE — registration only, directory already gone"),
                               (SAFE, "SAFE — clean, published, and unused; removable"),
                               (REVIEW, "REVIEW — recoverable but possibly still wanted"),
                               (PROTECTED, "PROTECTED — do not delete")):
            group = [w for w in worktrees if w.verdict == verdict]
            if not group:
                continue
            total = sum(w.size_kb for w in group)
            print(f"\n{title}  ({len(group)}, {human(total)})")
            print("-" * 100)
            for w in group:
                print(f"  {human(w.size_kb):>9}  {w.path}")
                print(f"             {'; '.join(w.reasons)}")
                # Printed for every verdict, including protected, because "what
                # irreplaceable thing is sitting in here" is the question a user
                # approving a deletion actually needs answered.
                if w.ignored_count:
                    print(f"             ignored data ({w.ignored_count}): "
                          f"{', '.join(w.ignored_sample)}"
                          + (" …" if w.ignored_count > len(w.ignored_sample) else ""))

        print("\n" + "=" * 100)
        for verdict in (STALE, SAFE, REVIEW, PROTECTED, MAIN):
            group = [w for w in worktrees if w.verdict == verdict]
            if group:
                label = "main checkout(s), not candidates" if verdict == MAIN else "worktree(s)"
                print(f"  {verdict:<10} {len(group):>3} {label:<32} "
                      f"{human(sum(w.size_kb for w in group)):>10}")

    if args.json:
        payload = {
            "roots": args.roots,
            "scanned_at": time.time(),
            "active_within": args.active_within,
            "assume_no_live_sessions": args.assume_no_live_sessions,
            "hostname": socket.gethostname(),
            # The remover needs these to tell fresh liveness data from the very
            # file this scan already consumed.
            "live_paths_file": (os.path.realpath(os.path.expanduser(args.live_paths_file))
                                if args.live_paths_file else ""),
            "live_names_file": (os.path.realpath(os.path.expanduser(args.live_names_file))
                                if args.live_names_file else ""),
            "live_paths": live_paths,
            "live_names": live_names,
            "worktrees": [asdict(w) for w in worktrees],
        }
        with open(os.path.expanduser(args.json), "w") as fh:
            json.dump(payload, fh, indent=2)
        if not args.quiet:
            print(f"\nPlan written to {args.json}")


if __name__ == "__main__":
    main()
