#!/bin/bash
# Regression suite for worktree-cleanup. Run: bash tests/regression.sh
# Each case asserts on the fixture's final
# on-disk state, because a report claiming a worktree was preserved proves nothing.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
S="$HERE/../scripts"
W="${TMPDIR:-/tmp}/wtc-tests"
mkdir -p "$W"
T=$W/regress
PASS=0; FAIL=0

ok()   { PASS=$((PASS+1)); echo "  PASS  $1"; }
bad()  { FAIL=$((FAIL+1)); echo "  FAIL  $1"; }
chk()  { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (got '$2', want '$3')"; fi; }

age() { # age a worktree so the recency gate does not fire
  local sb=$1 w=$2 r=webapp
  [ -d "$sb/api/.git/worktrees/$w" ] && r=api
  find "$sb/$r/.git/worktrees/$w" "$sb/$w" -exec touch -t 202001010000 {} \; 2>/dev/null
}

fresh() { rm -rf "$T"; bash "$HERE/build_fixture.sh" "$T" >/dev/null 2>&1
          for w in wt-feature-done wt-bugfix-shipped wt-review-072f6e wt-experiment wt-hotfix wt-spike; do age "$T" "$w"; done; }

echo "== 1. classification =="
fresh
python3 $S/scan_worktrees.py --roots "$T" --maxdepth 2 --assume-no-live-sessions --quiet --json "$W/r.json" 2>/dev/null
v() { python3 -c "
import json;d=json.load(open('$W/r.json'))
print(next((w['verdict'] for w in d['worktrees'] if w['path'].endswith('/$1')),'MISSING'))"; }
chk "clean+published  -> safe"       "$(v wt-feature-done)"    "safe"
chk "detached+published -> safe"     "$(v wt-review-072f6e)"   "safe"
chk "unpushed commits -> protected"  "$(v wt-experiment)"      "protected"
chk "uncommitted edits -> protected" "$(v wt-hotfix)"          "protected"
chk "missing dir -> stale"           "$(v wt-vanished)"        "stale"
chk "recently used -> protected"     "$(v wt-active-session)"  "protected"

echo "== 2. no liveness data => nothing is safe =="
python3 $S/scan_worktrees.py --roots "$T" --maxdepth 2 --quiet --json "$W/r2.json" 2>/dev/null
chk "safe count without liveness" "$(python3 -c "
import json;print(sum(1 for w in json.load(open('$W/r2.json'))['worktrees'] if w['verdict']=='safe'))")" "0"

echo "== 3. remover refuses to execute blind =="
python3 $S/remove_worktrees.py "$W/r.json" --execute >/dev/null 2>&1
chk "exit code" "$?" "2"

echo "== 4. happy path removes safe, keeps the rest =="
fresh
python3 $S/scan_worktrees.py --roots "$T" --maxdepth 2 --assume-no-live-sessions --quiet --json "$W/r3.json" 2>/dev/null
python3 $S/remove_worktrees.py "$W/r3.json" --include safe stale --assume-no-live-sessions --execute >/dev/null 2>&1
for w in wt-feature-done wt-bugfix-shipped wt-review-072f6e; do
  [ -d "$T/$w" ] && bad "$w removed" || ok "$w removed"; done
for w in wt-experiment wt-spike wt-hotfix wt-active-session; do
  [ -d "$T/$w" ] && ok "$w preserved" || bad "$w preserved"; done
chk "unpushed commit reachable (webapp)" "$(/usr/bin/git -C "$T/webapp" log --all --format=%s | grep -c 'WIP: the only copy')" "1"
chk "unpushed commit reachable (api)"    "$(/usr/bin/git -C "$T/api"    log --all --format=%s | grep -c 'WIP: the only copy')" "1"
chk "stale registration pruned" "$(/usr/bin/git -C "$T/webapp" worktree list | grep -c wt-vanished)" "0"

echo "== 5. races are caught at removal time =="
for mode in dirty live headmove touch; do
  fresh
  python3 $S/scan_worktrees.py --roots "$T" --maxdepth 2 --assume-no-live-sessions --quiet --json "$W/r4.json" 2>/dev/null
  case $mode in
    dirty)    echo "x" >> "$T/wt-feature-done/src/index.js" ;;
    live)     echo "$T/wt-feature-done" > "$W/live.txt" ;;
    headmove) (cd "$T/wt-feature-done" && echo y > n.txt && /usr/bin/git add . && /usr/bin/git commit -qm late) ;;
    touch)    touch "$T/wt-feature-done/src/index.js" ;;
  esac
  if [ $mode = live ]; then
    python3 $S/remove_worktrees.py "$W/r4.json" --include safe --live-paths-file "$W/live.txt" --execute >/dev/null 2>&1
  else
    python3 $S/remove_worktrees.py "$W/r4.json" --include safe --assume-no-live-sessions --execute >/dev/null 2>&1
  fi
  [ -d "$T/wt-feature-done" ] && ok "race '$mode' held back" || bad "race '$mode' held back"
done

echo "== 6. branch handling =="
fresh
python3 $S/scan_worktrees.py --roots "$T" --maxdepth 2 --assume-no-live-sessions --quiet --json "$W/r5.json" 2>/dev/null
python3 $S/remove_worktrees.py "$W/r5.json" --include safe --assume-no-live-sessions --only "$T/wt-feature-done" --execute >/dev/null 2>&1
chk "branch kept by default" "$(/usr/bin/git -C "$T/webapp" branch --list feat/checkout-flow | grep -c .)" "1"
fresh
python3 $S/scan_worktrees.py --roots "$T" --maxdepth 2 --assume-no-live-sessions --quiet --json "$W/r6.json" 2>/dev/null
python3 $S/remove_worktrees.py "$W/r6.json" --include safe --assume-no-live-sessions --only "$T/wt-feature-done" --delete-branch --execute >/dev/null 2>&1
chk "branch deleted with --delete-branch" "$(/usr/bin/git -C "$T/webapp" branch --list feat/checkout-flow | grep -c .)" "0"
chk "commits survive on remote" "$(/usr/bin/git -C "$T/webapp" branch -r | grep -c feat/checkout-flow)" "1"

echo ""
echo "======================================"
echo "  PASS: $PASS   FAIL: $FAIL"
rm -rf "$T" "$S/__pycache__"
[ $FAIL -eq 0 ] || exit 1
