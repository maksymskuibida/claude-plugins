---
type: regex
target: last_message
pattern: 'as (i|we) (said|mentioned|noted|flagged|described|reported|explained)|(i|we) (raised|mentioned|flagged|noted|reported|described|explained) (earlier|above|before|previously)|from before|earlier (message|update|report)s?|previous (message|update)s?|mid-flight'
flags: i
match: not_contains
---

Self-contained: the report never sends the reader back into the transcript. First-person pointers
only — "see above" inside one report points at the report itself and is fine.
