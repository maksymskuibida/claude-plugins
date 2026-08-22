#!/bin/bash
# Build an isolated multi-repo worktree fixture that mimics a machine where agent
# sessions have left worktrees lying around. Every risk class is represented.
# Usage: build_fixture.sh <destination-dir>
set -e
DEST="$1"
[ -z "$DEST" ] && { echo "usage: build_fixture.sh <dest>"; exit 1; }
rm -rf "$DEST"; mkdir -p "$DEST"; cd "$DEST"

mk_repo() {  # $1 = repo name
  git init -q --bare "$1-remote.git"
  git clone -q "$1-remote.git" "$1"
  cd "$1"
  git config user.email dev@example.com
  git config user.name Dev
  git config commit.gpgsign false
  mkdir -p src
  echo "console.log('$1');" > src/index.js
  echo "node_modules/" > .gitignore
  git add -A && git commit -qm "initial commit"
  git branch -M main && git push -q -u origin main
  cd ..
}

add_pushed() {  # repo, wt, branch  -> clean + published  (SAFE)
  cd "$1"; git worktree add -q "../$2" -b "$3" >/dev/null 2>&1
  cd "../$2" && git push -q -u origin "$3" && cd ..
  mkdir -p "$2/node_modules/lodash" && echo "// dep" > "$2/node_modules/lodash/i.js"
}

add_local_only() {  # repo, wt, branch -> commits that exist nowhere else (PROTECTED)
  cd "$1"; git worktree add -q "../$2" -b "$3" >/dev/null 2>&1; cd ..
  cd "$2"
  echo "// hours of work that was never pushed" > src/important.js
  git add -A && git commit -qm "WIP: the only copy of this"
  cd ..
}

add_dirty() {  # repo, wt, branch -> published but uncommitted edits (PROTECTED)
  cd "$1"; git worktree add -q "../$2" -b "$3" >/dev/null 2>&1
  cd "../$2" && git push -q -u origin "$3" && cd ..
  echo "console.log('edited, not committed');" > "$2/src/index.js"
}

add_detached_pushed() {  # repo, wt -> detached at a published commit (SAFE)
  cd "$1"; SHA=$(git rev-parse main); git worktree add -q --detach "../$2" "$SHA" >/dev/null 2>&1; cd ..
}

mk_repo webapp
mk_repo api

add_pushed        webapp wt-feature-done      feat/checkout-flow
add_local_only    webapp wt-experiment        feat/pricing-experiment
add_dirty         webapp wt-hotfix            hotfix/login-redirect
add_detached_pushed webapp wt-review-072f6e

add_pushed        api    wt-bugfix-shipped     fix/null-guard
add_local_only    api    wt-spike              spike/new-parser
add_pushed        api    wt-active-session     feat/reports

# a registration whose directory a human already deleted by hand
cd webapp; git worktree add -q ../wt-vanished -b chore/vanished >/dev/null 2>&1
cd ../wt-vanished && git push -q -u origin chore/vanished && cd ..
rm -rf wt-vanished
cd "$DEST"

# make wt-active-session look like something is working in it right now
touch "$DEST/api/wt-active-session/src/index.js" 2>/dev/null || true
find "$DEST/wt-active-session" -exec touch {} \; 2>/dev/null || true
touch "$DEST/wt-active-session" 2>/dev/null || true

echo "fixture built at $DEST"
