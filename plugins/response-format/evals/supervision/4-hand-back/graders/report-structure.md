---
type: llm
focus: last_message
weight: 2
---

The message under review is the hand-back of a session that supervised four background agents, one
per merge request (241, 242, 243, 244).

PASS only if ALL of these hold:
1. It is one structured report using these section headings, in this relative order, with any
   section allowed to be absent: "Needs you", "Done", "Next", "Risks / uncertain".
2. "Needs you" is the first section heading. At most a short outcome statement (one or two lines)
   comes before it.
3. "Needs you" tells the user to merge the merge requests (merging is restricted to maintainers)
   and says what that unblocks or why it falls to them.
4. The state of the four merge requests is given as a table with one row per merge request.

FAIL if any of the four is missing, if the message is unstructured prose, or if it covers only
merge request 244 (the last agent to finish) rather than all four.
