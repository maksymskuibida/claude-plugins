"""Regression tests for the model-fit hook. Run: python3 -m unittest discover plugins/model-fit/tests"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HOOK = os.path.join(os.path.dirname(__file__), "..", "hooks", "model_fit.py")
SUGGESTION = "## Switch to opus?\nHard design.\n\nSwitch or stay, then send `continue`."


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

    def run_hook(self, event, entrypoint="cli", raw=None):
        env = {**os.environ, "CLAUDE_PLUGIN_DATA": self.tmp.name}
        env.pop("CLAUDE_CODE_ENTRYPOINT", None)
        if entrypoint is not None:
            env["CLAUDE_CODE_ENTRYPOINT"] = entrypoint
        payload = raw if raw is not None else json.dumps({"session_id": "s1", "transcript_path": self.transcript, **event})
        proc = subprocess.run([sys.executable, HOOK], input=payload, capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"] if proc.stdout else None

    def start(self, **kw):
        return self.run_hook({"hook_event_name": "SessionStart", "source": "startup"}, **kw)

    def prompt(self):
        return self.run_hook({"hook_event_name": "UserPromptSubmit", "prompt": "continue"})

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

    def test_rule_forbids_ask_question_tool_and_asks_for_continue(self):
        ctx = self.start()
        self.assertIn("NOT through AskUserQuestion", ctx)
        self.assertIn("## Switch to <model>?", ctx)
        self.assertIn("send `continue`", ctx)

    def test_plain_prompt_is_silent(self):
        self.write_transcript([assistant("Here is the result.")])
        self.assertIsNone(self.prompt())

    def test_answer_to_suggestion_confirms_once_and_survives_compaction(self):
        self.write_transcript([assistant("thinking aloud"), assistant(SUGGESTION)])
        self.assertIn("counts as confirmed", self.prompt())
        self.write_transcript([assistant(SUGGESTION), assistant("Confirmed on opus. Starting.")])
        self.assertIsNone(self.prompt())  # confirmed state, not re-announced
        ctx = self.run_hook({"hook_event_name": "SessionStart", "source": "compact"})
        self.assertIn("already answered", ctx)
        self.assertNotIn("## Switch to <model>?", ctx)

    def test_suggestion_followed_by_other_text_is_not_an_open_question(self):
        self.write_transcript([assistant(SUGGESTION), assistant("Done, all good.")])
        self.assertIsNone(self.prompt())

    def test_suggestion_in_tool_turn_before_pure_tool_use_still_counts(self):
        tool_only = {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Bash"}]}}
        self.write_transcript([assistant(SUGGESTION), tool_only])
        self.assertIsNotNone(self.prompt())

    def test_sidechain_suggestion_is_ignored(self):
        self.write_transcript([assistant("main text"), assistant(SUGGESTION, isSidechain=True)])
        self.assertIsNone(self.prompt())

    def test_sessions_are_independent(self):
        self.write_transcript([assistant(SUGGESTION)])
        self.assertIsNotNone(self.prompt())
        ctx = self.run_hook({"hook_event_name": "SessionStart", "source": "startup", "session_id": "s2"})
        self.assertIn("## Switch to <model>?", ctx)

    def test_bad_input_never_fails(self):
        for raw in ("", "not json", "[]", json.dumps({"hook_event_name": "SessionStart"})):
            self.assertIsNone(self.run_hook({}, raw=raw))
        self.assertIsNone(self.run_hook({"hook_event_name": "UserPromptSubmit", "transcript_path": "/nonexistent"}))


if __name__ == "__main__":
    unittest.main()
