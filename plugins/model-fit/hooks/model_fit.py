#!/usr/bin/env python3
"""model-fit hook: SessionStart + UserPromptSubmit.

SessionStart (startup, resume, clear, compact) hands Claude the model-fit rule, written for the
client the user is in. UserPromptSubmit nudges Claude on each prompt until the check has run
once; as soon as the transcript shows it (a "## Confirmed model ..." or "## Switch to ...?"
message) the session is marked checked and the hook goes quiet for good. When the last assistant
message was a suggestion, the user's reply is their answer and Claude is told so.

The check happens once per session, first, before heavy work: switching models throws away the
prompt cache, so the later it happens the more it costs. The hook passes a rough context size
(transcript bytes) so the assessment can count that cost.

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
CHECK_HEADING = re.compile(r"^## (?:Switch to \S|Confirmed model )", re.MULTILINE)
TAIL_BYTES = 512 * 1024


def switch_howto(entrypoint):
    """How the user changes the model in the client they are actually using."""
    if entrypoint == "cli":
        return "type `/model <alias>` (aliases: `haiku`, `sonnet`, `opus`)"
    if entrypoint == "claude-desktop":
        return "pick it in the model picker in the app's UI (there is no command for it here)"
    return ("pick it in the client's model picker, or in a terminal type `/model <alias>` "
            "(aliases: `haiku`, `sonnet`, `opus`)")


def size_line(size_bytes):
    if not size_bytes:
        return "Context so far: none recorded — treat it as a fresh session."
    return (f"Context so far: the session transcript is about {max(1, size_bytes // 1024)} KB "
            "(a rough proxy for what a switch would have to re-read).")


def rule_text(entrypoint, size_bytes):
    return f"""\
# Model fit (model-fit plugin)

Once per session — on the first coding task or big non-coding task (research, writing, analysis, \
planning, review) — check whether the model you are running on (your system prompt names it) \
fits the job. Conversation and quick questions do not count; the check waits for the first real \
task, and never repeats after that, even if the work changes.

Do it FIRST: before reading files, spawning agents, running builds or any other heavy work. \
Switching models throws away the prompt cache — everything already in context is re-read uncached \
at full price — so the earlier the switch, the cheaper it is. Judge from the user's message \
alone; do not explore to decide.

Count that re-cache cost in the assessment. {size_line(size_bytes)}
- Little context (a fresh session): a switch is nearly free — judge on fit alone.
- A lot of context: a switch re-reads all of it. Suggest only when the mismatch is large. For "too \
easy", prefer a cheap subagent (`model: "haiku"`) for the mechanical part, or a fresh session, \
over switching.

| Tier | Fits |
|---|---|
| haiku | Mechanical and well-specified: search, lookup, formatting, log triage, boilerplate, one obvious edit |
| sonnet | The default working tier: features, bug fixes, refactors, reviews, most coding and writing |
| opus / fable | Genuinely hard: novel architecture, subtle concurrency or data-corruption bugs, cross-system design, adversarial security reasoning — path unclear, being wrong is expensive |

- Too complicated for your model → suggest moving UP (a weak model quietly getting a hard design \
subtly wrong produces confident work that has to be redone).
- Too easy for your model → suggest moving DOWN (a strong model grinding through mechanical work \
wastes the user's limit).
- It fits → send exactly one line, `## Confirmed model `<model>` fits the task`, then get on with \
the work in the same turn. Nothing else about the check.

When it does not fit, STOP before starting and send this as a plain chat message — NOT through \
AskUserQuestion, and with no report sections or other formatting (the one-line "fits" message \
above is the same: plain, no report sections). This overrides any other rule about how to ask, \
including "decisions go through AskUserQuestion". Reword it to fit; keep it short:

    ## Switch to <model>?
    <one line: why this task belongs on a different tier — and the re-cache cost if it is not trivial>

    Switch ({switch_howto(entrypoint)}) or stay on <current model> — either way, send `continue`. \
Whatever model is selected when you do counts as your answer.

Rules for that message:
- Name the model you would choose and give a one-line reason. Do no work and make no tool calls in that turn.
- Keep `## Switch to <model>?` as the heading exactly (the plugin recognises the answer by it).
- Make the `continue` instruction the last words of the message, nothing after, so the client can offer `continue` as the suggested reply (Tab).
- You cannot change the model yourself.
"""


def reminder(size_bytes):
    so_far = (f"transcript about {max(1, size_bytes // 1024)} KB so far" if size_bytes
              else "fresh session")
    return (
        "model-fit: the once-per-session model check has not run yet. If this message starts a "
        "coding task or a big non-coding task, do it FIRST, before any heavy work — a switch "
        f"re-caches the whole context ({so_far}). Per the session-start rule: `## Switch to "
        "<model>?` if it does not fit, else one line `## Confirmed model `<model>` fits the "
        "task`. Pure conversation: ignore this."
    )


ALREADY_CHECKED = """\
# Model fit (model-fit plugin)

The once-per-session model check already ran in this session. Do not run it again and do not \
mention it.
"""

ANSWERED = """\
# Model fit (model-fit plugin)

The user's message answers your model suggestion. The model you are running on now (your system \
prompt names it) is the one they chose — it stands for the rest of the session and the model check \
is done. If it differs from what you suggested, that is their call; do not re-argue it. Begin the \
work, opening with the one line `## Confirmed model `<model>` for this task`.
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


def is_checked(session_id):
    try:
        with open(state_path(session_id)) as fh:
            return bool(json.load(fh).get("checked"))
    except (OSError, ValueError):
        return False


def mark_checked(session_id):
    path = state_path(session_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump({"checked": True}, fh)


def transcript_facts(transcript_path):
    """(size in bytes, main-thread assistant texts from the tail, oldest first)."""
    try:
        size = os.path.getsize(transcript_path)
        with open(transcript_path, "rb") as fh:
            fh.seek(max(0, size - TAIL_BYTES))
            tail = fh.read().decode("utf-8", "replace")
    except OSError:
        return 0, []
    texts = []
    for line in tail.splitlines():
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
            texts.append(text)
    return size, texts


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
    size, texts = transcript_facts(event.get("transcript_path") or "")

    if name == "SessionStart":
        emit(name, ALREADY_CHECKED if is_checked(session_id) else rule_text(entrypoint, size))
    elif name == "UserPromptSubmit":
        if is_checked(session_id):
            return
        if texts and SUGGESTION_HEADING.search(texts[-1]):
            mark_checked(session_id)
            emit(name, ANSWERED)
        elif any(CHECK_HEADING.search(t) for t in texts):
            mark_checked(session_id)  # the check ran (it fit, or was answered earlier)
        else:
            emit(name, reminder(size))


if __name__ == "__main__":
    try:
        main()
    except Exception:  # a hook must never break the session
        pass
    sys.exit(0)
