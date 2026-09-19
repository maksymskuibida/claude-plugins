# Response Format

Makes Claude tell you the state of the work in one message you can scan in five seconds — instead
of a work log you read end to end, or a dozen partial reports you scroll back through.

**One report, on two occasions only:** when the job is handed back to you, or when something is
needed from you. In order, omitting empty ones:

⏳ **Needs you** · ✅ **Done** · ▶️ **Next** · ⚠️ **Risks / uncertain**

While work is still running and nothing is needed — including every turn woken by a background-task
notification, a finished subagent or a message from another session — Claude writes at most one
plain line, or nothing.

The rules that do most of the work:

- **Asks come first and carry their consequence** — "`.env.local` missing → blocks
  [!24 checkout API](https://gitlab.example.com/shop/api/-/merge_requests/24) → blocks T5", not
  "waiting on the env file".
- **Self-contained.** A report may lean on exactly one earlier message, the previous report.
  Everything else is restated in a line or carried by a link — never "as I said above".
- **Links, always.** MRs, PRs, issues, review notes, commits: the full URL, never a bare `!123`.
  Files: a clickable path. Specs: the doc link plus the section.
- **A hand-back is cumulative** — everything since your last real message, outcome first, with a
  state table when several items share attributes (one row per MR: link, what, verdict, head).
- **Reports are pinned.** Right before a report Claude calls the session's chapter tool if there is
  one (`mcp__ccd_session__mark_chapter` in the desktop app's Code tab), so it shows up in the
  floating table of contents. No such tool → skipped silently.
- **Decisions go through the question UI**, not prose options you have to answer by retyping.
- **Nothing is claimed without verification.** Blocked, untested and uncertain get said out loud —
  and a subagent's self-report is repeated only as that agent's claim.

Small conversational turns skip the structure entirely; a direct question still gets a direct answer.

### What changed in 2.0.0

| | 1.x | 2.0.0 |
|---|---|---|
| When the structure fires | every turn that reports on work — done, running or blocked | hand-back or needs-you only; mid-flight is one plain line or nothing |
| Background notifications | each produced a report of the increment | explicitly mid-flight, not a hand-back |
| References to earlier messages | unrestricted | only the immediately previous report; everything else restated or linked |
| Links | not required (`!24` in the style's own example) | every outside reference is a markdown link; never a bare `!123` / `#123` |
| Scope of the final report | the last step | cumulative since your last real message, with a state table |
| Navigation | — | chapter mark right before each report |
| Length | under ~120 words, always | ~120 words for a needs-you ping; a hand-back gets the room its table needs |

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
| `output-styles/report-format.md` | The operative rules, ~1,100–1,200 tokens (a chars/4 estimate — ~1,190 for the whole file including frontmatter, ~1,125 for the body alone), in the system prompt every turn. Roughly double 1.x: the price of spelling out the mid-flight case. |
| `skills/response-format/` | The reasoning behind the format. Loaded on demand; installed plugin skills are namespaced `<plugin>:<skill>`, so this should list as `response-format:response-format`. |
| `evals/` | `supervision/` — a `claude plugin eval` suite replaying the failure 2.0.0 fixes (four background notifications, one hand-back). `trigger-eval.json` — skill-trigger queries for `skill-creator`. See [evals/README.md](evals/README.md). |

## How it activates, and what that costs you

The output style is marked `force-for-plugin: true`, so **it applies without anyone selecting it** —
no `/config` step and nothing to paste into a CLAUDE.md. That is deliberate: a house style nobody
has to remember to switch on is the only kind that gets used.

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
