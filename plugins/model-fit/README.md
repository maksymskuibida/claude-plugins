# model-fit

Before **every coding task and every big non-coding task**, Claude checks that the model this
session runs on fits the job — in both directions:

- **too complicated** for the current model → suggests moving **up**;
- **too easy** → suggests moving **down** (a strong model grinding through mechanical work wastes
  your limit);
- **fits** → says nothing and gets on with it.

Conversation, quick questions and follow-ups inside a task it already checked are skipped. When
it does not fit, Claude stops and sends a short chat message instead of starting:

```
## Switch to opus?
Cross-service design with unclear failure modes — a tier up pays for itself here.

Switch (pick it in the model picker) or stay on sonnet — either way, send `continue`.
Whatever model is selected when you do counts as your answer.
```

You switch (or don't), send `continue`, and the model selected at that moment is taken as your
choice for **that task**. Claude doesn't ask again until a new, different task starts.

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
  for the current client.
- **UserPromptSubmit** — adds a one-line reminder to every prompt, because a rule given once at
  session start fades in a long session. If instead the last assistant message was a
  `## Switch to …?` suggestion, your reply is the answer: Claude is told the model it is running
  on now is the one you chose for this task.

The hooks are stateless — the answer is recognised from the transcript — so a new task naturally
gets a fresh check. The hook input carries no model name, and none is needed: Claude reads its own
model from its system prompt. The hooks never block a prompt and never fail a session.

## Limits

- **Claude does the judging.** A hook cannot tell how big or hard a task is; it only supplies the
  rule and the per-prompt nudge, so a missed or needless suggestion is a judgment call, not a bug
  in the hook.
- **The reminder costs a little.** About 50 tokens added to every prompt; that is the price of
  the check happening before every task rather than only early in a session.
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
