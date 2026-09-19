#!/usr/bin/env python3
"""Regenerate the supervision cases from one scenario, so the four turns cannot drift apart.

The scenario is the failure this plugin's 2.0.0 rules exist to stop: a session supervising
background agents that wrote a structured report after every notification. One user request, four
review-and-fix agents, four notifications. Case N resumes the conversation as it stood just before
notification N (`history.jsonl`) and receives that notification as its prompt (`prompt.md` body).

    python3 plugins/response-format/evals/supervision/build_history.py

Writes `<case>/history.jsonl`, `<case>/prompt.md` and the `<case>/session-tools/` fixture plugin for
every case. Graders and `case.yaml` are hand-written and are not touched. Output is deterministic:
rerunning with no edits changes nothing.

`session-tools` is an eval-only plugin that declares an MCP server named `session`, which the
suite's `mocks/session/` answers — that is what gives a run a chapter tool to call (or to leave
alone). The harness requires a plugin shipped with a case to sit in a subdirectory of that case, so
each case carries its own copy. It is never installed and its server is never started.
"""
import json
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = "https://gitlab.example.com/shop/backend"
SESSION = "5e0f6c1a-0000-4000-8000-000000000001"
NAMESPACE = uuid.UUID("5e0f6c1a-0000-4000-8000-0000000000ff")


def mr(n):
    return f"{REPO}/-/merge_requests/{n}"


REQUEST = f"""Four backend MRs for the loyalty-points release are open in {REPO}:

- !241 ledger migration — {mr(241)}
- !242 points API — {mr(242)}
- !243 expiry job — {mr(243)}
- !244 admin report — {mr(244)}

Spec: https://docs.example.com/loyalty/spec

Start one review-and-fix agent per MR in the background: review it against the spec, fix what it
finds, push, report back. Hand back to me when all four are done — I'm in meetings until then."""

AGENTS = [
    (241, "ledger migration"),
    (242, "points API"),
    (243, "expiry job"),
    (244, "admin report"),
]

LAUNCHED = "Four review-and-fix agents are running in the background, one per MR."

# (MR, the agent's self-report, the one-line mid-flight reply that followed it)
NOTIFICATIONS = [
    (
        241,
        f"""Reviewed {mr(241)} against the spec, section "Ledger".
One issue: migration 0042 had no down-step. Added it in commit 9f3c2ab
({REPO}/-/commit/9f3c2ab), file db/migrations/0042_points_ledger.py.
Pipeline green on head 9f3c2ab: {REPO}/-/pipelines/88121.
Verdict: ready to merge.""",
        f"[!241 ledger migration]({mr(241)}) agent is done; three still running.",
    ),
    (
        242,
        f"""Reviewed {mr(242)} against the spec, section "Redemption rules". Two doubts.
1. POST /points/redeem was not idempotent: a double-submit redeemed twice. Fixed with an
   idempotency key in commit 4be7d10 ({REPO}/-/commit/4be7d10), src/points/api.py:88.
2. points_ledger has no index on user_id, so the balance query is a full scan. NOT fixed: out of
   this MR's scope. Left a review note proposing a follow-up issue: {mr(242)}#note_5512.
Pipeline green on head 4be7d10: {REPO}/-/pipelines/88133.
Verdict: ready to merge; doubt 2 stays open.""",
        f"[!242 points API]({mr(242)}) agent is done — two doubts, one fixed; two still running.",
    ),
    (
        243,
        f"""Reviewed {mr(243)} against the spec, section "Expiry".
One issue: the job compared against server-local time instead of UTC, so points expired up to a
day early. Fixed in commit c07aa51 ({REPO}/-/commit/c07aa51), jobs/expire_points.py:23.
Pushed. Pipeline {REPO}/-/pipelines/88140 was still running when I stopped; I did not see it pass.
Verdict: fix pushed, not verified.""",
        f"[!243 expiry job]({mr(243)}) agent is done; waiting on the last one.",
    ),
    (
        244,
        f"""Reviewed {mr(244)} against the spec, section "Reporting".
No issues found, no commits. Head 77d0e2f, pipeline green: {REPO}/-/pipelines/88102.
Verdict: ready to merge.
Note: merging is restricted to maintainers in this project, so none of the four MRs can be merged
by an agent.""",
        None,
    ),
]

CASES = [
    "1-first-notification",
    "2-second-notification",
    "3-third-notification",
    "4-hand-back",
]

PROMPT_FRONTMATTER = {
    "mid-flight": """---
description: "Background-task notification {n} of 4 — work still running, nothing needed from the user"
tags: [supervision, mid-flight]
max_turns: 6
allowed_tools: []
expected_outcome: "At most one short plain line, no sections, no emoji headings, no chapter mark"
---
""",
    "hand-back": """---
description: "Notification 4 of 4 completes the job — the one turn that is a hand-back"
tags: [supervision, hand-back]
max_turns: 8
allowed_tools: []
expected_outcome: "A chapter mark, then ONE cumulative, self-contained, fully linked report"
---
""",
}


FIXTURE_PLUGIN = {
    ".claude-plugin/plugin.json": {
        "name": "session-tools",
        "version": "0.0.1",
        "description": "Eval-only fixture: declares the MCP server the suite's mocks answer, so a "
        "run has a chapter tool to call. Never installed, never started.",
    },
    ".mcp.json": {"mcpServers": {"session": {"command": "false"}}},
}


def task_id(n):
    return f"a{n:03d}f00d{n:03d}beef"


def notification(n, report):
    running = [m for m, _ in AGENTS if m > n]
    left = f"{len(running)} background agent(s) still running." if running else "No background agents are still running."
    return f"""[SYSTEM NOTIFICATION - NOT USER INPUT]
This is an automated background-task event, NOT a message from the user.

<task-notification>
<task-id>{task_id(n)}</task-id>
<status>completed</status>
<summary>Agent "Review and fix !{n}" finished. {left}</summary>
<result>
{report}
</result>
</task-notification>"""


class Transcript:
    def __init__(self):
        self.lines = []
        self.parent = None
        self.tick = 0

    def _base(self, kind):
        self.tick += 1
        uid = str(uuid.uuid5(NAMESPACE, f"{kind}-{self.tick}"))
        record = {
            "parentUuid": self.parent,
            "isSidechain": False,
            "type": kind,
            "uuid": uid,
            "timestamp": f"2026-09-01T09:{self.tick:02d}:00.000Z",
            "userType": "external",
            "cwd": "/workspace",
            "sessionId": SESSION,
            "version": "2.1.273",
            "gitBranch": "",
        }
        self.parent = uid
        return record

    def user(self, content):
        record = self._base("user")
        record["message"] = {"role": "user", "content": content}
        self.lines.append(record)

    def assistant(self, content):
        record = self._base("assistant")
        record["message"] = {
            "id": f"msg_{self.tick:04d}",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5",
            "content": content,
            "stop_reason": "tool_use" if any(b["type"] == "tool_use" for b in content) else "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
        self.lines.append(record)

    def dump(self):
        return "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in self.lines)


def opening():
    t = Transcript()
    t.user(REQUEST)
    t.assistant(
        [
            {
                "type": "tool_use",
                "id": f"toolu_launch{n}",
                "name": "Agent",
                "input": {
                    "description": f"Review and fix !{n}",
                    "prompt": f"Review {mr(n)} ({what}) against https://docs.example.com/loyalty/spec, "
                    "fix what you find, push, and report the verdict, the head commit and the pipeline.",
                    "run_in_background": True,
                },
            }
            for n, what in AGENTS
        ]
    )
    t.user(
        [
            {
                "type": "tool_result",
                "tool_use_id": f"toolu_launch{n}",
                "content": f"Async agent launched successfully. task-id: {task_id(n)}. "
                "You will be notified when it completes.",
            }
            for n, _ in AGENTS
        ]
    )
    t.assistant([{"type": "text", "text": LAUNCHED}])
    return t


def main():
    for index, case in enumerate(CASES):
        t = opening()
        for n, report, reply in NOTIFICATIONS[:index]:
            t.user(notification(n, report))
            t.assistant([{"type": "text", "text": reply}])
        n, report, _ = NOTIFICATIONS[index]
        kind = "hand-back" if index == len(CASES) - 1 else "mid-flight"
        case_dir = HERE / case
        case_dir.mkdir(exist_ok=True)
        (case_dir / "history.jsonl").write_text(t.dump(), encoding="utf-8")
        (case_dir / "prompt.md").write_text(
            PROMPT_FRONTMATTER[kind].format(n=index + 1) + "\n" + notification(n, report) + "\n",
            encoding="utf-8",
        )
        for name, body in FIXTURE_PLUGIN.items():
            target = case_dir / "session-tools" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {case}/history.jsonl ({len(t.lines)} records) and {case}/prompt.md")


if __name__ == "__main__":
    main()
