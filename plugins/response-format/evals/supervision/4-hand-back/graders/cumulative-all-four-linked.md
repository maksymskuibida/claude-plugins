---
type: regex
target: last_message
pattern: '^(?=[\s\S]*\]\(https://gitlab\.example\.com/shop/backend/-/merge_requests/241)(?=[\s\S]*\]\(https://gitlab\.example\.com/shop/backend/-/merge_requests/242)(?=[\s\S]*\]\(https://gitlab\.example\.com/shop/backend/-/merge_requests/243)(?=[\s\S]*\]\(https://gitlab\.example\.com/shop/backend/-/merge_requests/244)'
weight: 2
---

Cumulative and linked: all four merge requests appear, each as a markdown link to its full URL —
not only the one whose agent finished last.
