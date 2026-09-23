# Evals

Two suites, for two different harnesses: `supervision/` checks what the output style makes Claude
*write*; `trigger-eval.json` checks when the skill *loads*.

## Behaviour suite — `supervision/`

Replays the failure that 2.0.0 exists to stop: a session supervising background agents that wrote a
structured report after every notification, each covering only the increment and leaning on earlier
messages. One user request, four review-and-fix agents, four background-task notifications — three
while work is still running, then the one that completes the job.

| Case | Resumes from | Prompt | Expected |
|---|---|---|---|
| `supervision-1-first-notification` | request + agents launched | notification 1 of 4 | at most one short plain line; no sections, no emoji headings; chapter tool not called |
| `supervision-2-second-notification` | … + notification 1 and its one-line reply | notification 2 of 4 | same |
| `supervision-3-third-notification` | … + notification 2 and its reply | notification 3 of 4 | same |
| `supervision-4-hand-back` | … + notification 3 and its reply | notification 4 of 4 — job complete | chapter tool called exactly once, then ONE report: `Needs you` first, all four MRs in a table, every MR a full-URL markdown link and no bare `!242`, the open doubt restated rather than pointed at, MR 243's unseen pipeline and the agents' verdicts not rounded up into facts |

Run it with `claude plugin eval` ([docs](https://code.claude.com/docs/en/plugin-evals.md)) from the
plugin root. First regenerate the `session-tools/` fixture, which is not committed (see below), then
the cheap form — one run per case, plugin arm only, about $0.30:

```bash
python3 evals/supervision/build_history.py
claude plugin eval . --tag supervision --runs 1 --ablation none --no-publish
```

Drop `--runs 1` for the default three runs per case. There is no no-plugin baseline to compare
against: the harness runs replay cases single-arm, because the resumed history carries the plugin
into both arms. Add `--judge-model sonnet` if the three `llm` graders on the hand-back look flaky; the
other graders are regex and tool-call checks and cost nothing.

How it is put together, and why:

- **`build_history.py` is the single source of the scenario.** It writes every case's
  `history.jsonl` (the transcript the run resumes — `context.history_file` in `case.yaml`),
  `prompt.md` (the notification, which becomes the next user turn) and `session-tools/` fixture.
  Edit the scenario there and rerun it; do not hand-edit those outputs. Graders and `case.yaml` are
  hand-written. The three mid-flight cases carry identical graders on purpose.
- **The chapter tool is a mock.** An eval run loads nothing personal, so the desktop app's
  `mcp__ccd_session__mark_chapter` is not there. `<case>/session-tools/` is an eval-only plugin that
  declares an MCP server named `session`; `mocks/session/` answers its `mark_chapter` tool with the
  real tool's description and schema. The tool therefore appears as
  `mcp__plugin_session-tools_session__mark_chapter` — a different name from the real one, which is
  the point: the style's rule is tool-agnostic and has to work from the description alone. The
  harness requires a plugin shipped with a case to sit inside that case, hence four copies.
  **The fixture is gitignored**, not committed: it is a nested `.claude-plugin/plugin.json` plus a
  `.mcp.json`, and a marketplace install ships the whole plugin directory, so committing it would
  ship an MCP server declaration inside `response-format` itself. `build_history.py` writes it
  again in one run; the output is byte-identical each time.
- **`chapter-mark` is `arm: with-only`**, as the docs ask of any grader a no-plugin arm could never
  pass. Replay cases run single-arm, where it is scored like the rest.
- **Each case is one turn.** The harness sends one prompt per run, so "three quiet turns, then one
  report" is four cases over a shared, growing history rather than one four-turn run. The history
  of case 4 therefore contains *ideal* mid-flight replies, not whatever cases 1–3 produced.

Results go to `evals/results/`. A replay run also writes the resumed session's transcript
(`<session-id>.jsonl`) beside the `history.jsonl` it started from. Both are gitignored.

## Trigger eval set — `trigger-eval.json`

24 queries — 12 that should load `response-format:response-format`, 12 near-misses that share its
vocabulary but need something else — for `skill-creator`'s description optimiser:

```bash
REPO=/absolute/path/to/this/checkout   # e.g. the output of `pwd` at the repo root

cd /absolute/path/to/skill-creator/skills/skill-creator   # wherever that plugin is installed

python -m scripts.run_loop \
  --eval-set "$REPO/plugins/response-format/evals/trigger-eval.json" \
  --skill-path "$REPO/plugins/response-format/skills/response-format" \
  --model claude-sonnet-5 --max-iterations 4 --runs-per-query 3
```

`run_loop.py` imports `from scripts.generate_report import …`, so it must be launched with cwd set
to the skill-creator skill directory — but `--eval-set` and `--skill-path` are then resolved against
*that* cwd, not the repo root, so relative paths under `plugins/response-format/...` fail with
`Error: No SKILL.md found`. Pass both as absolute paths, as above.

Also note: `run_eval.find_project_root()` (imported by `run_loop.py`) walks up from cwd looking for
a `.claude` directory. Run from the skill-creator skill directory, that resolves to `/Users/semka` on
this machine (or whatever your home directory is) — not this repo — so the harness writes its
temporary command files into `~/.claude/commands/` and runs `claude -p` with your home directory as
project root. Surprising, and worth knowing before you run it.

It needs a working `claude -p`; the last attempt returned 0/3 on every query, positives included,
because the CLI's OAuth session had expired — an all-zero run means no query reached a model, not
that the description failed.
