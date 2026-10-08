# model-fit

**Once per session**, on the first coding task or big non-coding task, Claude checks that the model
this session runs on fits the job — in both directions:

- **too complicated** for the current model → suggests moving **up**;
- **too easy** → suggests moving **down** (a strong model grinding through mechanical work wastes
  your limit);
- **fits** → one line, `## Confirmed model `<model>` fits the task`, and gets on with it.

Conversation and quick questions don't count; the check waits for the first real task, then never
repeats in that session — not even if the work changes.

**It runs first, before any heavy work.** Switching models throws away the prompt cache: whatever
is already in context is re-read uncached. So the check happens before Claude reads files, spawns
agents or runs builds, and it counts that cost in the assessment — with little context a switch is
nearly free, with a lot of context Claude suggests one only when the mismatch is large (and for
"too easy" prefers a cheap subagent or a fresh session). The hook tells it roughly how big the
context is (transcript size). When the check finds a mismatch, it stops with a short chat message:

```
## Switch to opus?
Cross-service design with unclear failure modes — a tier up pays for itself here.

Switch (pick it in the model picker) or stay on sonnet — either way, send `continue`.
Whatever model is selected when you do counts as your answer.
```

You switch (or don't), send `continue`, and the model selected at that moment is taken as your
choice for the rest of the session.

## How the switch is described

The hook reads `CLAUDE_CODE_ENTRYPOINT` and tells Claude what to write:

| Client | Instruction in the message |
|---|---|
| CLI (`cli`) | type `/model <alias>` |
| Desktop app (`claude-desktop`) | pick it in the model picker in the UI |
| anything else | model picker, or `/model <alias>` in a terminal |

## Plain message, not a question dialog

The suggestion is an ordinary chat turn, never `AskUserQuestion` — the dialog is clumsy for a
one-line "switch or stay". The injected rule says so explicitly and overrides other "decisions go
through AskUserQuestion" rules.

## What the hooks do

- **SessionStart** (startup, resume, clear, compact) — hands Claude the full rule above, written
  for the current client, with the transcript size. If this session was already checked (resume,
  compaction), it says so instead and the rule isn't repeated.
- **UserPromptSubmit** — until the check has run, adds a one-line reminder to each prompt, because
  a rule given once at session start fades. The moment the transcript shows the check (a
  `## Confirmed model …` or `## Switch to …?` message) the session is marked checked
  (state in `CLAUDE_PLUGIN_DATA`) and the hook goes silent for good. If the last assistant message
  was a `## Switch to …?` suggestion, your reply is the answer: Claude is told the model it is
  running on now is the one you chose.

The hook input carries no model name, and none is needed: Claude reads its own model from its
system prompt. The hooks never block a prompt and never fail a session.

## Limits

- **Claude does the judging.** A hook cannot tell how big or hard a task is; it only supplies the
  rule and the per-prompt nudge, so a missed or needless suggestion is a judgment call, not a bug
  in the hook.
- **The reminder costs a little, briefly.** About 100 tokens per prompt until the check has run,
  then nothing.
- **One check, then trust.** If the session later shifts to very different work, nothing re-checks
  it; start a fresh session or switch by hand.
- **Context size is a proxy.** Transcript bytes are not tokens; they only tell Claude whether the
  context is small or large.
- **`continue` on Tab is best effort.** The message is written to end on the `continue`
  instruction so the client's suggested reply can pick it up; no hook can set that suggestion.
- **Hooks load at session start.** Install, then start a new session.
- **Replaces a hand-written rule.** If your `CLAUDE.md` already carries a "check the session
  model fits" section, remove it — two copies of the rule will disagree (especially on
  `AskUserQuestion`).

## Tests

```bash
python3 -m unittest discover plugins/model-fit/tests
```
