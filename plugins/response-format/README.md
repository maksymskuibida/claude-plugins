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

## What's inside

| Piece | Effect |
|---|---|
| `output-styles/report-format.md` | The operative rules. Applies automatically — see below. |
| `skills/response-format/` | The full reasoning behind the format. Loaded on demand, and when you want to change it. |

## Automatic vs opt-in

The output style is marked `force-for-plugin: true`, so **it activates as soon as the plugin is
enabled** — no `/config` step and nothing to paste into a CLAUDE.md. That is deliberate: a house
style nobody has to remember to switch on is the only kind that gets used.

The trade: while enabled, it overrides whatever output style you had selected. If you would rather
choose it yourself, delete the `force-for-plugin: true` line from `output-styles/report-format.md`
and pick "Report format" from `/config` → Output style.

It also sets `keep-coding-instructions: true`, so Claude Code's normal coding behaviour is untouched
— this changes how results are *reported*, not how work is done.

## Making it yours

The format is opinionated on purpose, but none of it is load-bearing. Edit
`output-styles/report-format.md` — different section names, a different length budget, no emoji.
Ask Claude to "load the response-format skill and change the format", and it will read the reasoning
behind each rule before editing.
