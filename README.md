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
| [`response-format`](plugins/response-format) | Structured, ask-first replies — Needs you / Done / Next / Risks. Applies automatically on enable, from the next session. |

## Adding another plugin

Create `plugins/<name>/.claude-plugin/plugin.json`, drop in `skills/`, `agents/`, `commands/`,
`hooks/` or `output-styles/` as needed, then add an entry to `.claude-plugin/marketplace.json`.
Run `/plugin marketplace update` to pick up changes.

Validate before pushing:

```bash
claude plugin validate . && claude plugin validate plugins/<name>
```
