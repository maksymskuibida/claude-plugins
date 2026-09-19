---
type: regex
target: last_message
pattern: '![0-9]+(?![^\[\]]*\]\()'
match: not_contains
---

No bare `!242`: a merge-request number may appear only inside the text of a markdown link. The
lookahead lets `[!242 points API](https://…)` through and catches `!242` standing alone.
