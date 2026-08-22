# Trigger eval set

20 queries — 10 that should load `response-format:response-format`, 10 near-misses that share its
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
