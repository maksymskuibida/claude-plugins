# Trigger eval set

20 queries — 10 that should load `response-format:response-format`, 10 near-misses that share its
vocabulary but need something else — for `skill-creator`'s description optimiser:

```bash
python -m scripts.run_loop \
  --eval-set plugins/response-format/evals/trigger-eval.json \
  --skill-path plugins/response-format/skills/response-format \
  --model claude-sonnet-5 --max-iterations 4 --runs-per-query 3
```

Run it from the `skill-creator` skill directory. It needs a working `claude -p`; the last attempt
returned 0/3 on every query, positives included, because the CLI's OAuth session had expired — an
all-zero run means no query reached a model, not that the description failed.
