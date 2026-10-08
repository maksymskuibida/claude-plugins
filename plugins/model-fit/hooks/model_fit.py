#!/usr/bin/env python3
"""model-fit hook: SessionStart + UserPromptSubmit.

SessionStart (startup, resume, clear, compact) hands Claude the model-fit rule, written for the
client the user is in. UserPromptSubmit adds a one-line reminder to every prompt (the rule fades
in a long session) — or, when the last assistant message was a "## Switch to ...?" suggestion,
tells Claude the user's reply is their answer for that task.

Stateless: the answer is recognised from the transcript, so a new task simply gets a fresh check.
The hook input carries no model name and does not need one: Claude reads its own model from its
system prompt, and "the model selected when the user replies" is exactly the model that answers.

Never blocks and never fails the session: every error path exits 0 with no output.
"""
import json
import os
import re
import sys

SUGGESTION_HEADING = re.compile(r"^## Switch to \S", re.MULTILINE)
TAIL_BYTES = 512 * 1024


def switch_howto(entrypoint):
    """How the user changes the model in the client they are actually using."""
    if entrypoint == "cli":
        return "type `/model <alias>` (aliases: `haiku`, `sonnet`, `opus`)"
    if entrypoint == "claude-desktop":
        return "pick it in the model picker in the app's UI (there is no command for it here)"
    return ("pick it in the client's model picker, or in a terminal type `/model <alias>` "
            "(aliases: `haiku`, `sonnet`, `opus`)")


def rule_text(entrypoint):
    return f"""\
# Model fit (model-fit plugin)

Before EVERY coding task and every big non-coding task (research, writing, analysis, planning, \
review), and before doing any of the work, check whether the model you are running on (your \
system prompt names it) fits the job. Skip it for conversation, quick questions and follow-ups \
inside a task you already checked.

| Tier | Fits |
|---|---|
| haiku | Mechanical and well-specified: search, lookup, formatting, log triage, boilerplate, one obvious edit |
| sonnet | The default working tier: features, bug fixes, refactors, reviews, most coding and writing |
| opus / fable | Genuinely hard: novel architecture, subtle concurrency or data-corruption bugs, cross-system design, adversarial security reasoning — path unclear, being wrong is expensive |

- Too complicated for your model → suggest moving UP (a weak model quietly getting a hard design \
subtly wrong produces confident work that has to be redone).
- Too easy for your model → suggest moving DOWN (a strong model grinding through mechanical work \
wastes the user's limit).
- It fits → say nothing about it and get on with the work. Never announce a passing check.

When it does not fit, STOP before starting and send this as a plain chat message — NOT through \
AskUserQuestion, and with no report sections or other formatting. This overrides any other rule \
about how to ask, including "decisions go through AskUserQuestion". Reword it to fit; keep it short:

    ## Switch to <model>?
    <one line: why this task belongs on a different tier>

    Switch ({switch_howto(entrypoint)}) or stay on <current model> — either way, send `continue`. \
Whatever model is selected when you do counts as your answer.

Rules for that message:
- Name the model you would choose and give a one-line reason. Do no work and make no tool calls in that turn.
- Keep `## Switch to <model>?` as the heading exactly (the plugin recognises the answer by it).
- Make the `continue` instruction the last words of the message, nothing after, so the client can offer `continue` as the suggested reply (Tab).
- You cannot change the model yourself.
- Ask at most once per task: after the user answers, that stands for the whole task however it \
grows. A new, different task gets a fresh check — but do not re-suggest a move the user already \
declined for the same kind of work.
"""


REMINDER = (
    "model-fit: if this message starts a coding task or a big non-coding task, first check the "
    "model you are on fits it (too hard → suggest up, too easy → suggest down); if it does not, "
    "send the plain-chat `## Switch to <model>?` message per the session-start rule. "
    "Otherwise ignore this."
)

ANSWERED = """\
# Model fit (model-fit plugin)

The user's message answers your model suggestion. The model you are running on now (your system \
prompt names it) is the one they chose — it stands for this task. Do not ask again for it. If it \
differs from what you suggested, that is their call; do not re-argue it. Begin the work, opening \
with one short line such as "Confirmed on <model>."
"""


def read_event():
    try:
        return json.loads(sys.stdin.read() or "{}")
    except (ValueError, OSError):
        return None


def last_assistant_text(transcript_path):
    """Text of the most recent main-thread assistant message that has any, else ''."""
    try:
        with open(transcript_path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - TAIL_BYTES))
            tail = fh.read().decode("utf-8", "replace")
    except OSError:
        return ""
    for line in reversed(tail.splitlines()):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("type") != "assistant" or row.get("isSidechain"):
            continue
        content = (row.get("message") or {}).get("content")
        if isinstance(content, str):
            text = content
        else:
            text = "\n".join(b.get("text", "") for b in content or []
                             if isinstance(b, dict) and b.get("type") == "text")
        if text.strip():
            return text
    return ""


def emit(event_name, context):
    json.dump({"hookSpecificOutput": {"hookEventName": event_name,
                                      "additionalContext": context}}, sys.stdout)


def main():
    event = read_event()
    if not isinstance(event, dict):
        return
    name = event.get("hook_event_name")
    entrypoint = os.environ.get("CLAUDE_CODE_ENTRYPOINT", "")

    if name == "SessionStart":
        emit(name, rule_text(entrypoint))
    elif name == "UserPromptSubmit":
        text = last_assistant_text(event.get("transcript_path") or "")
        emit(name, ANSWERED if SUGGESTION_HEADING.search(text) else REMINDER)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # a hook must never break the session
        pass
    sys.exit(0)
