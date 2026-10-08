# model-fit

Before a big task, Claude checks that the model this session runs on fits the job. Only when it
is **materially** off — about a tier, on a task big enough to matter — it stops and sends a short
chat message instead of starting:

```
## Switch to opus?
Cross-service design with unclear failure modes — a tier up pays for itself here.

Switch (pick it in the model picker) or stay on sonnet — either way, send `continue`.
Whatever model is selected when you do counts as your answer.
```

You switch (or don't), send `continue`, and the model selected at that moment is taken as your
choice. Claude is not asked again that session.

It cuts both ways: it also flags Opus grinding through mechanical edits.

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

- **SessionStart** (startup, resume, clear, compact) — hands Claude the rule above, written for the
  current client. After a resume or compaction in a session you already answered, it says so
  instead, so Claude does not ask twice.
- **UserPromptSubmit** — if the last assistant message was a `## Switch to …?` suggestion, your
  reply counts as the answer: the session is marked confirmed (state in `CLAUDE_PLUGIN_DATA`) and
  Claude is told the model it is running on now is the confirmed one. Silent otherwise.

The hook input carries no model name, and none is needed: Claude reads its own model from its
system prompt. The hook never blocks a prompt and never fails a session.

## Limits

- **Claude does the judging.** A hook cannot tell how big or hard a task is; it only supplies the
  rule, so a missed or needless suggestion is a judgment call, not a bug in the hook.
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
