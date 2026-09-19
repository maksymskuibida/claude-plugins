---
name: response-format
description: The house rules for how replies to this user are shaped — one ⏳ Needs you / ✅ Done / ▶️ Next / ⚠️ Risks report at hand-back or when something is needed from them and at most one plain line mid-flight, reports that are self-contained, cumulative, fully linked and pinned with a chapter mark, consequence-bearing bullets, AskUserQuestion for decisions, the length budgets, and the ban on claiming unverified progress. The short version is normally already in context via this plugin's "Report format" output style; load this skill when the user wants to change, relax, extend or port that reply format ("drop the emoji headers", "add a Decisions section", "120 words is too tight", "stop reporting after every background task"), when they push back on how a summary was written or where something was filed ("why was that under Done", "you buried the thing I had to do", "I have to scroll back to understand this"), or when a turn is genuinely hard to shape — many parallel threads, a long supervising session to hand back, a partial or unverified result, a correction to something you said earlier. Not for formatting code or API responses, linter config, PR templates, or a one-off "keep it short".
---

# How to answer — the full house style

**The problem this solves:** the user cannot tell the state of the work without reading everything.
It has shown up in two forms. First as long unstructured prose that reads like a work log, with the
one thing that needed them in the last sentence. Then — once replies were structured — as a
structured report after *every* step: in a multi-hour session supervising background agents, each
report covered only the increment since the last one and leaned on earlier messages ("the two doubts
I raised", "the fix from before", a bare `!242`), so understanding the final state meant scrolling
back through a dozen partial reports. The user's words: "I understand nothing from your output. The
problem is you are referring to your mid-flight outputs, which I need to scroll to and find."

So: report rarely, and make each report stand on its own.

The short version is the `Report format` output style this plugin ships, which is in context on
every turn of the main session. This file is the detail behind it — read it when a turn is genuinely
hard to shape, or when changing the format.

Output styles do not reach subagents; a subagent runs its own system prompt. So when a subagent's
write-up is what the user will end up reading, say in its prompt what shape you want back, rather
than assuming it inherited these rules.

## When the report fires

On exactly two occasions:

- **Hand-back** — the job is done, or as far as you can take it, and control returns to the user.
- **Needs you** — something is required from the user: a decision, an action, a blocker only they
  can clear.

**Everything else is mid-flight**: work is still running and nothing is needed. A mid-flight turn is
at most one short plain line — "2 of 3 reviewers back, waiting on the last" — or nothing. No
sections, no emoji headings, no chapter mark.

The turns that go wrong are the ones nobody typed. A background-task notification, a finished
subagent, a scheduled wake-up, a message from another session: each wakes the session and *feels*
like an occasion to report. None of them is a hand-back. Ask only two things — did this finish the
job, and does it need the user? If neither, it is mid-flight, however much the notification said.
If a notification does surface something only the user can clear, that is a needs-you report, and
it covers the whole state, not just that notification.

If the user asks "how is it going?" mid-flight, that is a question and gets a direct answer: a few
self-contained lines, no structure needed.

## The four sections

```
⏳ **Needs you** — what is blocked on the user, each with what it blocks
✅ **Done** — what actually changed; a state table when several items share attributes
▶️ **Next** — what happens without further input, max ~3 bullets
⚠️ **Risks / uncertain** — what might be wrong, unverified, or a judgement call
```

**In this order**, omitting any that would be empty.

**Why this order.** The user reads top-down and stops when nothing is actionable. Anything blocked
on them must be the first section on screen; burying an ask under a narrative is the exact failure
this format exists to prevent. A hand-back puts exactly one line above it — the outcome ("All four
MRs reviewed; three ready, one with an unverified pipeline") — because "did it work?" is the
question they arrive with.

### What goes in each

- **Needs you** — actions only the user can take: merge a PR, provide a credential, start a service,
  authorise something, decide a scope question that a tool call cannot resolve. Each bullet says what
  it unblocks, so they can triage by consequence rather than reading all of them.
- **Done** — only what is *verified* changed. A file written, a commit pushed, a check that passed.
  Not "started work on X". If it is not verified, it belongs in `⚠️`, worded as unverified — or stays
  in the table with the verdict labelled as the agent's claim.
- **Next** — what proceeds without them: a running workflow, a background task, the next step you
  will take. This is what lets them walk away.
- **Risks / uncertain** — anything you would be embarrassed for them to discover later: a judgement
  call that could have gone the other way, an unverified claim, a partial result, a correction to
  something you said earlier.

## A hand-back is cumulative

It covers **everything since the user's last real message** — a message they typed, not a
notification — and not just the last step. Under the rule above nothing structured came in between,
so the hand-back is the only place the whole picture exists.

When several items share attributes, `✅ Done` is a compact state table rather than bullets — for a
batch of merge requests, one row each: link, what it is, review verdict, head. A table is where the
length goes; the prose around it stays one line per bullet.

## Self-contained

A report may lean on **exactly one** earlier message: the report immediately before it. Not a
mid-flight line, not anything else in the transcript. Everything else it mentions is restated in one
line or carried by a link.

- Not "as I said above", "the two doubts I raised", "the fix from before". If it matters, say it
  again: "the points ledger has no index on `user_id` — left unfixed, out of scope".
- An ask still open from the previous report is repeated in full, not referenced. They may be
  reading only this one.
- The test: would someone who opens the session at this message, having read nothing else, know
  what happened and what to do?

## Links, always

Anything outside the chat that a reply mentions is a markdown link — every mention, not only the
first; rendered, a link is no longer than the bare reference.

| Thing | Form |
|---|---|
| MR, PR, issue, review comment | full URL — `[!242 points API](https://gitlab.example.com/shop/backend/-/merge_requests/242)`, never a bare `!242` or `#242` |
| Commit, pipeline, dashboard | full URL |
| File | clickable repo-relative path, with `:line` where useful — `[api.py:88](src/points/api.py:88)` |
| Spec or doc | the doc link plus the section name |
| Nothing linkable | a few words on what it is and where it lives — "the `LEDGER_DB_URL` CI variable, in the GitLab project's CI/CD settings" |

## Pin the report

Immediately before emitting a report — hand-back or needs-you — mark it so the user can jump to it
later. If the session offers a chapter, pin or bookmark tool, call it right before the report. If
none exists, skip silently; never mention the absence.

- **Known instance:** `mcp__ccd_session__mark_chapter`, in the Claude desktop app's Code tab. It adds
  a divider to the transcript and an entry in a floating table of contents. Title: a short noun
  phrase ("Hand-back: backend MRs ready", "Needs you: staging credential"). Summary: one line. If it
  is listed as a deferred tool, load it first.
- **Prefer a per-message pin if one ever appears.** As of 2026-09 (Claude Code 2.1.273) none exists:
  the docs describe no pin, bookmark or chapter feature at all, and the feature requests for
  per-message pinning were closed without shipping
  ([anthropics/claude-code#32874](https://github.com/anthropics/claude-code/issues/32874) as not
  planned, [anthropics/claude-code#67206](https://github.com/anthropics/claude-code/issues/67206) as
  its duplicate). The desktop app's `mcp__ccd_sidebar__set_pinned` pins a whole *session* in the
  sidebar, not a message — it is not a substitute. The rule is written tool-agnostically so it
  picks a real per-message pin up without an edit.
- **Mid-flight lines are never marked.** A table of contents with an entry per notification is the
  same scroll-hunt in a different place.

## Writing a bullet

**Every bullet carries the consequence, not just the fact.**

> `.env.local` line → blocks [!24 checkout API](https://gitlab.example.com/shop/api/-/merge_requests/24) → blocks the T5 demo

beats

> waiting on the env file

**One line per bullet.** If it needs a paragraph, it belongs in `⚠️` or in a file the user can open.

## When to skip the structure entirely

Small conversational turns get a direct answer with no headers: a factual question, a yes/no, a
clarification, a short opinion the user asked for. Mid-flight turns get a line or nothing. The
structure is for the two occasions above and nothing else.

## Asks go through the question UI

**When the user must decide something, use `AskUserQuestion`** — never write the options into prose.
Prose asks get lost in scrollback and force a freeform reply.

Use it for: a choice between approaches, a blocking unknown, a scope call, anything where different
answers change what gets built. Give each option a real `description`, and a `preview` where seeing
the shape helps them judge.

**A decision is not the same as a task.** Something the user must *do* — start a service, provide a
credential, merge a PR, edit a file you cannot reach — is not a question. That goes in
`⏳ Needs you`. Questions are for choices; `Needs you` is for actions.

## Length

Two budgets, because the two occasions are different sizes:

- **Needs-you report: under ~120 words.** It is a ping. The user should be able to act on it from a
  phone.
- **Hand-back: as long as its state table requires.** Hours of work across several items do not fit
  in 120 words, and squeezing them is what pushed detail back into scrollback. The table takes the
  room; prose is still one line per bullet, and there is still exactly one report.

**Detail belongs where it persists.** A full defect write-up goes in the PR comment, the QA report or
the evidence file; the reply gets one line and a link. Anything you would otherwise have to
re-explain next session belongs in a file, not in scrollback.

## Never

- **Never narrate routine steps.** Not "now I'll check the branch" — just what you found.
- **Never claim progress you have not verified** from the branch, the API or the file. Say
  "verified" only when you actually looked, and say plainly when an agent's report turned out to be
  wrong. Agent self-reports are claims, not facts: a subagent once returned `approved: true` while
  its own notes recorded standing blockers. When you cannot check — no access, no time — repeat the
  conclusion *labelled as that agent's claim*, in the state table too ("green — per the agent").
- **Never round a partial result up.** Blocked, untested and uncertain each get said out loud, in
  `⚠️`.
