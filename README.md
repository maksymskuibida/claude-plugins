# claude-plugins

A small marketplace of Claude Code plugins.

## Install

```bash
/plugin marketplace add <this-repo-url>
/plugin install response-format@mskuibida-tooling
```

Replace `<this-repo-url>` with the git URL once this is pushed, or a local path to try it first:

```bash
/plugin marketplace add ~/claude-plugins
```

## Plugins

| Plugin | What it does |
|---|---|
| [`response-format`](plugins/response-format) | Structured, ask-first replies — Needs you / Done / Next / Risks. Applies automatically on enable. |
| [`worktree-cleanup`](plugins/worktree-cleanup) | Reclaim disk from orphaned git worktrees without deleting unpushed work or a worktree a session is using. |

## Adding another plugin

Create `plugins/<name>/.claude-plugin/plugin.json`, drop in `skills/`, `agents/`, `commands/`,
`hooks/` or `output-styles/` as needed, then add an entry to `.claude-plugin/marketplace.json`.
Run `/plugin marketplace update` to pick up changes.
