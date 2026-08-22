#!/bin/bash
# Regression suite for worktree-cleanup. Run: bash tests/regression.sh
# Each case asserts on the fixture's final on-disk state, or on the remover's own
# output, because a report claiming a worktree was preserved proves nothing.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
S="$HERE/../scripts"
W="${TMPDIR:-/tmp}/wtc-tests"
# Start from nothing. $W used to survive between runs while plans were written to
# fixed names, so a scan that crashed left the previous run's plan in place and the
# assertions read that instead -- a green suite proving nothing about this revision.
chmod -R u+rwX "$W" 2>/dev/null
rm -rf "$W"
mkdir -p "$W"
T=$W/regress
PASS=0; FAIL=0

ok()   { PASS=$((PASS+1)); echo "  PASS  $1"; }
bad()  { FAIL=$((FAIL+1)); echo "  FAIL  $1"; }
chk()  { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (got '$2', want '$3')"; fi; }
has()  { if printf '%s' "$2" | grep -qF -- "$3"; then ok "$1"; else bad "$1 (no '$3' in: $(printf '%s' "$2" | tr '\n' '|'))"; fi; }

age() { # age a worktree so the recency gate does not fire
  local sb=$1 w=$2 r=webapp
  [ -d "$sb/api/.git/worktrees/$w" ] && r=api
  find "$sb/$r/.git/worktrees/$w" "$sb/$w" -exec touch -t 202001010000 {} \; 2>/dev/null
}

WTS="wt-feature-done wt-bugfix-shipped wt-review-072f6e wt-experiment wt-hotfix wt-spike wt-secrets wt-untracked"
fresh() { chmod -R u+rwX "$T" 2>/dev/null; rm -rf "$T"
          bash "$HERE/build_fixture.sh" "$T" >/dev/null 2>&1
          for w in $WTS; do age "$T" "$w"; done; }

# Every scan below lets stderr through on purpose: swallowing it hid crashes.
scan() { python3 $S/scan_worktrees.py --roots "$T" --maxdepth 2 --quiet "$@"; }

echo "== 1. classification =="
fresh
scan --assume-no-live-sessions --json "$W/r.json"
v() { python3 -c "
import json;d=json.load(open('$W/r.json'))
print(next((w['verdict'] for w in d['worktrees'] if w['path'].endswith('/$1')),'MISSING'))"; }
chk "clean+published  -> safe"       "$(v wt-feature-done)"    "safe"
chk "detached+published -> safe"     "$(v wt-review-072f6e)"   "safe"
chk "unpushed commits -> protected"  "$(v wt-experiment)"      "protected"
chk "uncommitted edits -> protected" "$(v wt-hotfix)"          "protected"
chk "missing dir -> stale"           "$(v wt-vanished)"        "stale"
chk "recently used -> protected"     "$(v wt-active-session)"  "protected"
chk "untracked only -> protected"    "$(v wt-untracked)"       "protected"

echo "== 1b. ignored files: data holds back, build output does not =="
# git status --porcelain never lists ignored files, so this whole class used to be
# invisible: a worktree holding the only copy of a .env classified as safe, was
# deleted, and the .env was never named in any report.
chk "ignored .env -> review"        "$(v wt-secrets)" "review"
ic() { python3 -c "
import json;d=json.load(open('$W/r.json'))
w=next(w for w in d['worktrees'] if w['path'].endswith('/$1'));print(w['$2'])"; }
chk "counts the .env"               "$(ic wt-secrets ignored_count)"     "1"
chk "names it in the report"        "$(ic wt-secrets ignored_sample)"    "['.env']"
chk "node_modules/dist not counted" "$(ic wt-feature-done ignored_count)" "0"
# Deliberately not --quiet: the table is the thing being asserted on here.
python3 $S/scan_worktrees.py --roots "$T" --maxdepth 2 --assume-no-live-sessions \
  --json "$W/r1b.json" > "$W/table.txt"
has "table names the ignored file" "$(cat "$W/table.txt")" "ignored data (1): .env"

echo "== 2. no liveness data => nothing is safe =="
scan --json "$W/r2.json"
chk "safe count without liveness" "$(python3 -c "
import json;print(sum(1 for w in json.load(open('$W/r2.json'))['worktrees'] if w['verdict']=='safe'))")" "0"

echo "== 3. remover refuses to execute blind =="
# Assert on the message, not just the exit code: argparse also exits 2 on a usage
# error, so a typo in this invocation would otherwise pass the test.
out=$(python3 $S/remove_worktrees.py "$W/r.json" --execute 2>&1); rc=$?
chk "exit code" "$rc" "2"
has "names the reason" "$out" "refusing to execute without liveness data"

echo "== 4. happy path removes safe, keeps the rest =="
fresh
scan --assume-no-live-sessions --json "$W/r3.json"
python3 $S/remove_worktrees.py "$W/r3.json" --include safe stale --assume-no-live-sessions --execute >/dev/null 2>&1
for w in wt-feature-done wt-bugfix-shipped wt-review-072f6e; do
  [ -d "$T/$w" ] && bad "$w removed" || ok "$w removed"; done
for w in wt-experiment wt-spike wt-hotfix wt-active-session wt-secrets wt-untracked; do
  [ -d "$T/$w" ] && ok "$w preserved" || bad "$w preserved"; done
chk "the .env survived" "$(cat "$T/wt-secrets/.env" 2>/dev/null)" "DB_PASSWORD=hunter2"
chk "unpushed commit reachable (webapp)" "$(/usr/bin/git -C "$T/webapp" log --all --format=%s | grep -c 'WIP: the only copy')" "1"
chk "unpushed commit reachable (api)"    "$(/usr/bin/git -C "$T/api"    log --all --format=%s | grep -c 'WIP: the only copy')" "1"
chk "stale registration pruned" "$(/usr/bin/git -C "$T/webapp" worktree list | grep -c wt-vanished)" "0"

echo "== 5. races are caught at removal time, by the gate that should catch them =="
# Survival alone would pass even if one always-firing gate held everything back, so
# each mode also asserts on the reason the remover printed.
for mode in dirty live headmove touch; do
  fresh
  scan --assume-no-live-sessions --json "$W/r4-$mode.json"
  case $mode in
    dirty)    echo "x" >> "$T/wt-feature-done/src/index.js"; want="uncommitted change" ;;
    live)     echo "$T/wt-feature-done" > "$W/live-$mode.txt";  want="in use — session cwd" ;;
    headmove) (cd "$T/wt-feature-done" && echo y > n.txt && /usr/bin/git add . && /usr/bin/git commit -qm late)
              want="HEAD moved since the scan" ;;
    touch)    touch "$T/wt-feature-done/src/index.js"; want="something is using it" ;;
  esac
  if [ $mode = live ]; then
    out=$(python3 $S/remove_worktrees.py "$W/r4-$mode.json" --include safe --live-paths-file "$W/live-$mode.txt" --execute 2>&1)
  else
    out=$(python3 $S/remove_worktrees.py "$W/r4-$mode.json" --include safe --assume-no-live-sessions --execute 2>&1)
  fi
  [ -d "$T/wt-feature-done" ] && ok "race '$mode' held back" || bad "race '$mode' held back"
  has "race '$mode' held back BY THE RIGHT GATE" "$out" "$want"
done

echo "== 6. branch handling =="
fresh
scan --assume-no-live-sessions --json "$W/r5.json"
python3 $S/remove_worktrees.py "$W/r5.json" --include safe --assume-no-live-sessions --only "$T/wt-feature-done" --execute >/dev/null 2>&1
chk "branch kept by default" "$(/usr/bin/git -C "$T/webapp" branch --list feat/checkout-flow | grep -c .)" "1"
fresh
scan --assume-no-live-sessions --json "$W/r6.json"
python3 $S/remove_worktrees.py "$W/r6.json" --include safe --assume-no-live-sessions --only "$T/wt-feature-done" --delete-branch --execute >/dev/null 2>&1
chk "branch deleted with --delete-branch" "$(/usr/bin/git -C "$T/webapp" branch --list feat/checkout-flow | grep -c .)" "0"
chk "commits survive on remote" "$(/usr/bin/git -C "$T/webapp" branch -r | grep -c feat/checkout-flow)" "1"

echo "== 7. liveness data must be FRESH, not merely non-empty =="
# The old gate was bool(live_paths or live_names): handing the remover the scan's
# own liveness file passed silently, which is precisely the incident this exists to
# prevent -- a session that started after the scan and has so far only read files
# is invisible to the recency gate, leaving liveness as the only gate that could see it.
fresh
echo "/nonexistent/scan-time" > "$W/live-scan.txt"
scan --live-paths-file "$W/live-scan.txt" --json "$W/r7.json"
R() { python3 $S/remove_worktrees.py "$W/r7.json" --include safe --execute "$@" 2>&1; }

out=$(R --live-paths-file "$W/live-scan.txt"); rc=$?
chk "reusing the scan's own file: exit" "$rc" "2"
has "reusing the scan's own file: why" "$out" "is the same file the scan read"
[ -d "$T/wt-feature-done" ] && ok "reusing the scan's own file: nothing deleted" || bad "reusing the scan's own file: nothing deleted"

cp "$W/live-scan.txt" "$W/live-copy.txt"      # different path, fresh mtime, same content
out=$(R --live-paths-file "$W/live-copy.txt"); rc=$?
chk "a copy of it: exit" "$rc" "2"
has "a copy of it: why" "$out" "identical to the plan's own"

echo "/nonexistent/other" > "$W/live-old.txt"; touch -t 202001010000 "$W/live-old.txt"
out=$(R --live-paths-file "$W/live-old.txt"); rc=$?
chk "collected before the scan: exit" "$rc" "2"
has "collected before the scan: why" "$out" "predates the plan"

echo "/nonexistent/other" > "$W/live-fresh.txt"
out=$(R --live-paths-file "$W/live-fresh.txt"); rc=$?
chk "genuinely fresh data: accepted" "$rc" "0"
[ -d "$T/wt-feature-done" ] && bad "genuinely fresh data: removal proceeds" || ok "genuinely fresh data: removal proceeds"

# A file whose mtime is in the *future* is neither "before the scan" nor "too old" --
# (now - written) goes negative, which used to sail under every max-age check. That
# let a liveness file with three-day-stale contents pass simply by being touched
# with a future timestamp, restoring exactly the blind trust this gate exists to end.
fresh
scan --live-paths-file "$W/live-scan.txt" --json "$W/r7c.json"
echo "/nonexistent/other" > "$W/live-future.txt"; touch -t 209901010000 "$W/live-future.txt"
out=$(python3 $S/remove_worktrees.py "$W/r7c.json" --include safe --execute \
        --live-paths-file "$W/live-future.txt" 2>&1); rc=$?
chk "future-mtime liveness file: exit" "$rc" "2"
has "future-mtime liveness file: why" "$out" "future mtime"
[ -d "$T/wt-feature-done" ] && ok "future-mtime liveness file: nothing deleted" || bad "future-mtime liveness file: nothing deleted"

fresh
scan --live-paths-file "$W/live-scan.txt" --json "$W/r7b.json"
python3 $S/remove_worktrees.py "$W/r7b.json" --include safe --assume-no-live-sessions --execute >/dev/null 2>&1
[ -d "$T/wt-feature-done" ] && bad "--assume-no-live-sessions still overrides" || ok "--assume-no-live-sessions still overrides"

echo "== 8. a plan is not executed forever, or on another machine =="
fresh
scan --assume-no-live-sessions --json "$W/r8.json"
python3 - "$W/r8.json" "$W/r8-old.json" "$W/r8-host.json" <<'PY'
import json, sys, time
plan = json.load(open(sys.argv[1]))
old = dict(plan, scanned_at=time.time() - 72 * 3600)
json.dump(old, open(sys.argv[2], "w"))
json.dump(dict(plan, hostname="some-other-laptop"), open(sys.argv[3], "w"))
PY
out=$(python3 $S/remove_worktrees.py "$W/r8-old.json" --include safe --assume-no-live-sessions --execute 2>&1); rc=$?
chk "three-day-old plan: exit" "$rc" "2"
has "three-day-old plan: why" "$out" "re-scan rather than acting on it"
out=$(python3 $S/remove_worktrees.py "$W/r8-host.json" --include safe --assume-no-live-sessions --execute 2>&1)
has "plan from another machine refused" "$out" "was produced on some-other-laptop"
[ -d "$T/wt-feature-done" ] && ok "neither plan deleted anything" || bad "neither plan deleted anything"

echo "== 9. the rmtree fallback — the single most dangerous line =="
# `git worktree remove` failing part-way is not reproducible on demand from the
# filesystem (anything that stops git from unlinking stops shutil.rmtree too), so
# the failure is injected with a git shim. What is under test is our fallback, not
# git's deletion: the branch taken, the note reported, and the final on-disk state.
mkdir -p "$W/shim"
cat > "$W/shim/git" <<'SHIM'
#!/bin/sh
if [ "$1" = "worktree" ] && [ "$2" = "remove" ]; then
  echo "error: failed to delete '$3': Directory not empty" >&2
  exit 255
fi
exec /usr/bin/git "$@"
SHIM
chmod +x "$W/shim/git"
fresh
scan --assume-no-live-sessions --json "$W/r9.json"
out=$(PATH="$W/shim:$PATH" python3 $S/remove_worktrees.py "$W/r9.json" --include safe \
        --assume-no-live-sessions --only "$T/wt-feature-done" --execute 2>&1)
has "fallback branch was taken" "$out" "removed (rmtree + prune)"
[ -d "$T/wt-feature-done" ] && bad "fallback actually removed the directory" || ok "fallback actually removed the directory"
chk "fallback pruned the registration" "$(/usr/bin/git -C "$T/webapp" worktree list | grep -c wt-feature-done)" "0"

# The other half of that branch: rmtree itself failing must report, not pretend.
# The failure is a top-level symlink, which shutil.rmtree refuses by design -- a
# permission-based failure would not reproduce under a root CI runner, and this is
# also the exact case that stops a doctored plan pointing the fallback at a symlink
# into somewhere else.
fresh
ln -s "$T/wt-bugfix-shipped" "$T/wt-symlink"
out=$(PATH="$W/shim:$PATH" python3 -c "
import sys; sys.path.insert(0, '$S')
from remove_worktrees import remove_one
print(remove_one({'path': '$T/wt-symlink', 'repo': '$T/api', 'branch': ''}, True))" 2>&1)
has "rmtree failure is reported, not swallowed" "$out" "rmtree failed"
[ -d "$T/wt-bugfix-shipped" ] && ok "rmtree failure leaves the target in place" || bad "rmtree failure leaves the target in place"
[ -L "$T/wt-symlink" ] && ok "rmtree failure leaves the symlink in place" || bad "rmtree failure leaves the symlink in place"

echo "== 10. units that a fixture cannot reach =="
out=$(python3 -c "
import sys; sys.path.insert(0, '$S')
from scan_worktrees import Worktree, match_live, is_build_output
wt = Worktree(path='/x/unit-a', repo='/x')
print('swallow' if match_live(wt, [], ['unit-app-something']) else 'no-swallow')
print('match' if match_live(wt, [], ['unit-a-4f21']) else 'no-match')
print('nested' if is_build_output('packages/web/node_modules/x/y.js') else 'missed')
print('data' if not is_build_output('config/local.sqlite') else 'wrong')")
chk "agent name unit-a does not swallow unit-app-something" "$(echo "$out" | sed -n 1p)" "no-swallow"
chk "agent name unit-a-4f21 does match"                     "$(echo "$out" | sed -n 2p)" "match"
chk "nested node_modules recognised as build output"        "$(echo "$out" | sed -n 3p)" "nested"
chk "an ignored sqlite file is data, not build output"      "$(echo "$out" | sed -n 4p)" "data"

fresh
scan --assume-no-live-sessions --allow-untracked --json "$W/r10.json"
chk "--allow-untracked downgrades to review" "$(python3 -c "
import json;d=json.load(open('$W/r10.json'))
print(next(w['verdict'] for w in d['worktrees'] if w['path'].endswith('/wt-untracked')))")" "review"
chk "--allow-untracked does not touch dirty tracked files" "$(python3 -c "
import json;d=json.load(open('$W/r10.json'))
print(next(w['verdict'] for w in d['worktrees'] if w['path'].endswith('/wt-hotfix')))")" "protected"

echo "== 11. a repo sitting at exactly --maxdepth is found =="
mkdir -p "$W/deep/a/b"; ( cd "$W/deep/a/b" && /usr/bin/git init -q r && cd r && \
  /usr/bin/git config user.email d@e.com && /usr/bin/git config user.name D && \
  /usr/bin/git config commit.gpgsign false && echo x > f && /usr/bin/git add . && /usr/bin/git commit -qm i )
chk "repo at depth 3 with --maxdepth 3" "$(python3 -c "
import sys; sys.path.insert(0, '$S')
from scan_worktrees import find_repos
print(len(find_repos(['$W/deep'], 3)))")" "1"

echo ""
echo "======================================"
echo "  PASS: $PASS   FAIL: $FAIL"
chmod -R u+rwX "$T" 2>/dev/null; rm -rf "$T" "$S/__pycache__"
[ $FAIL -eq 0 ] || exit 1
