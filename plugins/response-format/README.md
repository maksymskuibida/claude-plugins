# Response Format

Makes Claude answer in a structure you can scan in five seconds instead of reading end to end.

Every turn that reports on work ends with, in order and omitting empty ones:

⏳ **Needs you** · ✅ **Done** · ▶️ **Next** · ⚠️ **Risks / uncertain**

Plus three rules that do most of the work:

- **Asks come first and carry their consequence** — "`.env.local` missing → blocks !24 → blocks T5",
  not "waiting on the env file".
- **Decisions go through the question UI**, not prose options you have to answer by retyping.
- **Nothing is claimed without verification.** Blocked, untested and uncertain get said out loud —
  including when a subagent's self-report turns out not to match what actually happened.

Conversational turns skip the structure entirely; a direct question still gets a direct answer.

## Install

```bash
/plugin marketplace add https://github.com/maksymskuibida/claude-plugins
```

```bash
/plugin install response-format@mskuibida-tooling
```

## What's inside

| Piece | Effect |
|---|---|
| `output-styles/report-format.md` | The operative rules, ~550 tokens, in the system prompt every turn. |
| `skills/response-format/` | The reasoning behind the format. Loaded on demand, as `response-format:response-format`. |

## How it activates, and what that costs you

The output style is marked `force-for-plugin: true`, so **it applies as soon as the plugin is
enabled** — no `/config` step and nothing to paste into a CLAUDE.md. That is deliberate: a house
style nobody has to remember to switch on is the only kind that gets used.

Three things follow from that, and none of them are worked around:

- **It takes effect from your next session.** Output styles are read once at session start, so
  after installing you need `/clear` or a fresh session. The session you installed from is unchanged.
- **It overrides the output style you had selected**, for as long as the plugin is enabled. If two
  enabled plugins both set `force-for-plugin`, the first one loaded wins.
- **It does not reach subagents.** Subagents run their own system prompt, so a report written by a
  subagent is not shaped by this style. Only the main session's replies are.

If you would rather choose it yourself, delete the `force-for-plugin: true` line from
`output-styles/report-format.md` and pick "Report format" under `/config` → Output style.

It also sets `keep-coding-instructions: true`, which keeps Claude Code's built-in software
engineering instructions in place — this changes how results are *reported*, not how work is done.
Removing that line strips them, which is almost never what you want.

**If you already carry these rules somewhere else** — a `~/.claude/CLAUDE.md` section, or a personal
`~/.claude/skills/response-format/` — delete it when you install the plugin. Otherwise the rules sit
in the system prompt twice and two near-identical skills compete to trigger.

## Making it yours

The format is opinionated on purpose, but none of it is load-bearing. Edit
`output-styles/report-format.md` — different section names, a different length budget, no emoji.
Ask Claude to "load the response-format skill and change the format", and it will read the reasoning
behind each rule before editing.
