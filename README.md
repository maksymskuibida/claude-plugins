# claude-plugins

A small marketplace of Claude Code plugins.

## Install

```bash
/plugin marketplace add https://github.com/maksymskuibida/claude-plugins
```

```bash
/plugin install response-format@mskuibida-tooling
```

Then **start a new session** (or `/clear`). Plugin output styles are read once when a session
starts, so nothing changes in the session you installed from.

To hack on a checkout instead of the published repo, point the marketplace at a local path — the
same two commands otherwise:

```bash
/plugin marketplace add ~/claude-plugins
```

Both forms are verified working with the `claude` CLI (`claude plugin marketplace add …`,
`claude plugin install …`), which is the non-interactive equivalent of the slash commands.

## Plugins

| Plugin | What it does |
|---|---|
| [`dish-media`](plugins/dish-media) | Grade phone dish photos and loop turntable clips for restaurant menu tablets: grey-card calibration, one deterministic look per session, contact-sheet QA, no generative AI. |
| [`response-format`](plugins/response-format) | One self-contained, fully linked report — Needs you / Done / Next / Risks — at hand-back or when you are needed; one plain line mid-flight. Applies automatically on enable, from the next session. |
| [`worktree-cleanup`](plugins/worktree-cleanup) | Reclaim disk from orphaned git worktrees without deleting unpushed work or a worktree a session is using. |

## Adding another plugin

Create `plugins/<name>/.claude-plugin/plugin.json`, drop in `skills/`, `agents/`, `commands/`,
`hooks/` or `output-styles/` as needed, then add an entry to `.claude-plugin/marketplace.json`.
Run `/plugin marketplace update` to pick up changes.

Validate before pushing:

```bash
claude plugin validate . && claude plugin validate plugins/<name>
```

## Repo permissions

`.claude/settings.json` at the repo root grants two Bash permission allow rules so agent sessions
working in this repo can apply PR labels and merge approved PRs without the auto-mode classifier
stopping them:

- `Bash(gh pr edit *)`
- `Bash(gh pr merge *)`

That's it — no force-push variant (`git push --force`, `-f`, or otherwise) is included. A rewritten
public branch is exactly the class of action that should keep prompting, so that stays excluded on
purpose.

This is a **project-scoped, tracked** file rather than a rule in `~/.claude/settings.json`. A
user-level rule would let any session on this machine merge PRs in *any* repo, including unrelated
work repos; scoping it to this repo's checked-in settings keeps the blast radius to `claude-plugins`
and keeps the grant visible in git history and reviewable in a PR.

**It takes effect from your next session.** Project settings are read when a session starts, and
the settings watcher only watches directories that already contained a settings file at session
start — so a session already running in this repo (or in a worktree of it) when this merges will
not pick the rules up. It needs a restart or a new session.
