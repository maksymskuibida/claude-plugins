"""Regression tests for the model-fit hook. Run: python3 -m unittest discover plugins/model-fit/tests"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HOOK = os.path.join(os.path.dirname(__file__), "..", "hooks", "model_fit.py")
SUGGESTION = "## Switch to opus?\nHard design.\n\nSwitch or stay, then send `continue`."
FITS = "## Confirmed model `sonnet` fits the task"


def assistant(text, **extra):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}, **extra}


class HookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.transcript = os.path.join(self.tmp.name, "t.jsonl")

    def write_transcript(self, rows):
        with open(self.transcript, "w") as fh:
            fh.write("\n".join(json.dumps(r) for r in rows) + "\n")

    def run_hook(self, event, entrypoint="cli", raw=None, session="s1"):
        env = {**os.environ, "CLAUDE_PLUGIN_DATA": self.tmp.name}
        env.pop("CLAUDE_CODE_ENTRYPOINT", None)
        if entrypoint is not None:
            env["CLAUDE_CODE_ENTRYPOINT"] = entrypoint
        payload = raw if raw is not None else json.dumps(
            {"session_id": session, "transcript_path": self.transcript, **event})
        proc = subprocess.run([sys.executable, HOOK], input=payload, capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"] if proc.stdout else None

    def start(self, source="startup", **kw):
        return self.run_hook({"hook_event_name": "SessionStart", "source": source}, **kw)

    def prompt(self, **kw):
        return self.run_hook({"hook_event_name": "UserPromptSubmit", "prompt": "continue"}, **kw)

    # --- the rule -----------------------------------------------------------------------
    def test_cli_gets_model_command(self):
        ctx = self.start(entrypoint="cli")
        self.assertIn("/model <alias>", ctx)
        self.assertNotIn("model picker", ctx)

    def test_desktop_gets_model_picker(self):
        ctx = self.start(entrypoint="claude-desktop")
        self.assertIn("model picker", ctx)
        self.assertNotIn("type `/model", ctx)

    def test_unknown_client_gets_both(self):
        for entrypoint in ("claude-vscode", None):
            ctx = self.start(entrypoint=entrypoint)
            self.assertIn("model picker", ctx)
            self.assertIn("/model <alias>", ctx)

    def test_rule_is_plain_chat_and_asks_for_continue(self):
        ctx = self.start()
        self.assertIn("NOT through AskUserQuestion", ctx)
        self.assertIn("## Switch to <model>?", ctx)
        self.assertIn("send `continue`", ctx)

    def test_rule_covers_both_directions_and_the_fits_line(self):
        ctx = self.start()
        self.assertIn("suggest moving UP", ctx)
        self.assertIn("suggest moving DOWN", ctx)
        self.assertIn("## Confirmed model `<model>` fits the task", ctx)

    def test_rule_is_once_per_session_and_first(self):
        ctx = self.start()
        self.assertIn("Once per session", ctx)
        self.assertIn("never repeats", ctx)
        self.assertIn("Do it FIRST", ctx)
        self.assertIn("before reading files", ctx)

    def test_rule_counts_the_recache_cost_and_reports_context_size(self):
        self.write_transcript([assistant("x" * 300_000)])
        ctx = self.start(source="resume")
        self.assertIn("re-cache", ctx)
        self.assertIn("A lot of context", ctx)
        self.assertRegex(ctx, r"about \d{3} KB")

    def test_no_transcript_reads_as_a_fresh_session(self):
        self.assertIn("none recorded", self.start())

    def test_injected_text_has_no_stray_backslashes(self):
        for ctx in (self.start(), self.prompt()):
            self.assertNotIn("\\", ctx)

    # --- per-prompt behaviour -----------------------------------------------------------
    def test_unchecked_session_gets_a_short_reminder_with_size(self):
        self.write_transcript([assistant("Here is the result.")])
        ctx = self.prompt()
        self.assertIn("has not run yet", ctx)
        self.assertIn("FIRST", ctx)
        self.assertRegex(ctx, r"about \d+ KB")
        self.assertLess(len(ctx), 700)

    def test_fits_line_in_transcript_ends_the_check_for_good(self):
        self.write_transcript([assistant(FITS)])
        self.assertIsNone(self.prompt())  # marks checked, says nothing
        self.write_transcript([assistant("plain reply, FITS line now out of the tail")])
        self.assertIsNone(self.prompt())  # state remembers: still silent
        self.assertIn("already ran", self.start(source="compact"))

    def test_answer_to_suggestion_is_recognised_then_never_again(self):
        self.write_transcript([assistant("thinking aloud"), assistant(SUGGESTION)])
        answered = self.prompt()
        self.assertIn("answers your model suggestion", answered)
        self.assertNotIn("\\", answered)
        self.assertIn("## Confirmed model `<model>` for this task", answered)
        self.write_transcript([assistant(SUGGESTION), assistant("Starting.")])
        self.assertIsNone(self.prompt())
        ctx = self.start(source="resume")
        self.assertIn("already ran", ctx)
        self.assertNotIn("## Switch to <model>?", ctx)

    def test_suggestion_followed_by_other_text_counts_as_checked_not_answered(self):
        self.write_transcript([assistant(SUGGESTION), assistant("Done, all good.")])
        self.assertIsNone(self.prompt())

    def test_suggestion_before_pure_tool_use_still_counts(self):
        tool_only = {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Bash"}]}}
        self.write_transcript([assistant(SUGGESTION), tool_only])
        self.assertIn("answers your model suggestion", self.prompt())

    def test_sidechain_messages_are_ignored(self):
        self.write_transcript([assistant("main text"), assistant(SUGGESTION, isSidechain=True), assistant(FITS, isSidechain=True)])
        self.assertIn("has not run yet", self.prompt())

    def test_sessions_are_independent(self):
        self.write_transcript([assistant(FITS)])
        self.assertIsNone(self.prompt())
        ctx = self.start(session="s2")
        self.assertIn("Once per session", ctx)

    def test_bad_input_never_fails(self):
        for raw in ("", "not json", "[]", "{}"):
            self.assertIsNone(self.run_hook({}, raw=raw))
        ctx = self.run_hook({"hook_event_name": "UserPromptSubmit", "transcript_path": "/nonexistent"})
        self.assertIn("fresh session", ctx)  # unreadable transcript: plain reminder, never a crash


if __name__ == "__main__":
    unittest.main()
