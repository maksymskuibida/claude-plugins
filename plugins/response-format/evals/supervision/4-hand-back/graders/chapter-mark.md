---
type: tool_used
tool: mcp__plugin_session-tools_session__mark_chapter
min: 1
max: 1
arm: with-only
---

The report is pinned exactly once, by a chapter mark. The call necessarily precedes the final
message, so "called once" is "called right before the report". `with-only` because the no-plugin
arm has no such tool and could never pass; replay cases run single-arm, where it is scored.
