---
type: regex
target: last_message
pattern: '⏳|✅|▶|⚠|Needs you|Risks / uncertain'
match: not_contains
weight: 2
---

Mid-flight: work is still running and nothing is needed from the user, so the turn must carry no
report sections and no emoji headings.
