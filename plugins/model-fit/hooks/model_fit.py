#!/usr/bin/env python3
"""model-fit hook: SessionStart + UserPromptSubmit.

SessionStart (startup, resume, clear, compact) hands Claude the model-fit rule, written for the
client the user is in. UserPromptSubmit watches for the user answering a suggestion: when the
last assistant message was a "## Switch to ...?" suggestion, the user's reply counts as their
answer, the session is marked confirmed, and Claude is told not to ask again.

The hook input carries no model name and does not need one: Claude reads its own model from its
system prompt, and "the model selected when the user replies" is exactly the model that answers.

Never blocks and never fails the session: every error path exits 0 with no output.
"""
import json
import os
import re
import sys
import tempfile

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

When a task's shape becomes clear, and BEFORE doing the substantive work, judge whether the model \
you are running on (your system prompt names it) fits the job. Raise it only when the mismatch is \
material: about a tier off AND the task large enough to matter. Never for a quick question, a \
single obvious edit or a conversational turn.

| Tier | Fits |
|---|---|
| haiku | Mechanical and well-specified: search, lookup, formatting, log triage, boilerplate, one obvious edit |
| sonnet | The default working tier: features, bug fixes, refactors, reviews, most coding and writing |
| opus / fable | Genuinely hard: novel architecture, subtle concurrency or data-corruption bugs, cross-system design, adversarial security reasoning — path unclear, being wrong is expensive |

It cuts both ways: a strong model grinding through mechanical edits wastes limit; a weak model \
quietly getting a hard design subtly wrong costs a redo.

If it is materially off, STOP before starting and send this as a plain chat message — NOT through \
AskUserQuestion, and with no report sections or other formatting. This overrides any other rule \
about how to ask, including "decisions go through AskUserQuestion". Reword it to fit; keep it short:

    ## Switch to <model>?
    <one line: why this task needs a different tier>

    Switch ({switch_howto(entrypoint)}) or stay on <current model> — either way, send `continue`. \
Whatever model is selected when you do counts as your answer.

Rules for that message:
- Name the model you would choose and give a one-line reason. Do no work and make no tool calls in that turn.
- Keep `## Switch to <model>?` as the heading exactly (the plugin recognises the answer by it).
- Make the `continue` instruction the last words of the message, nothing after, so the client can offer `continue` as the suggested reply (Tab).
- You cannot change the model yourself. Ask at most once per task; once the user has answered, \
that decision stands for the whole session.
"""


CONFIRMED_AT_START = """\
# Model fit (model-fit plugin)

The user already answered a model-fit suggestion earlier in this session and the model they were \
on at that moment is confirmed. Do not raise the model question again.
"""

ANSWERED = """\
# Model fit (model-fit plugin)

The user's message answers your model suggestion. The model you are running on now (your system \
prompt names it) is the one they chose — it counts as confirmed for the rest of the session. Do \
not ask again. If it differs from what you suggested, that is their call; do not re-argue it. \
Begin the work, opening with one short line such as "Confirmed on <model>."
"""


def read_event():
    try:
        return json.loads(sys.stdin.read() or "{}")
    except (ValueError, OSError):
        return None


def state_path(session_id):
    base = os.environ.get("CLAUDE_PLUGIN_DATA") or os.path.join(tempfile.gettempdir(), "model-fit")
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)[:128]
    return os.path.join(base, "sessions", safe + ".json")


def is_confirmed(session_id):
    try:
        with open(state_path(session_id)) as fh:
            return bool(json.load(fh).get("confirmed"))
    except (OSError, ValueError):
        return False


def mark_confirmed(session_id):
    path = state_path(session_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump({"confirmed": True}, fh)


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
    session_id = event.get("session_id") or ""
    if not session_id:
        return
    entrypoint = os.environ.get("CLAUDE_CODE_ENTRYPOINT", "")

    if name == "SessionStart":
        emit(name, CONFIRMED_AT_START if is_confirmed(session_id) else rule_text(entrypoint))
    elif name == "UserPromptSubmit":
        if is_confirmed(session_id):
            return
        text = last_assistant_text(event.get("transcript_path") or "")
        if SUGGESTION_HEADING.search(text):
            mark_confirmed(session_id)
            emit(name, ANSWERED)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # a hook must never break the session
        pass
    sys.exit(0)
