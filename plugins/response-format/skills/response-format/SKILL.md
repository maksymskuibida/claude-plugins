---
name: response-format
description: The house rules for how replies to this user are shaped — the ⏳ Needs you / ✅ Done / ▶️ Next / ⚠️ Risks structure, consequence-bearing bullets, AskUserQuestion for decisions, the length budget, and the ban on claiming unverified progress. The short version is normally already in context via this plugin's "Report format" output style; load this skill when the user wants to change, relax, extend or port that reply format ("drop the emoji headers", "add a Decisions section", "120 words is too tight"), when they push back on how a summary was written or where something was filed ("why was that under Done", "you buried the thing I had to do"), or when a turn is genuinely hard to shape — many parallel threads, a partial or unverified result, a correction to something you said earlier. Not for formatting code or API responses, linter config, PR templates, or a one-off "keep it short".
---

# How to answer — the full house style

**The problem this solves:** long unstructured prose that reads like a work log, which the user has
to read end-to-end to find the one thing that needs them.

The short version is the `Report format` output style this plugin ships, which is in context on
every turn of the main session. This file is the detail behind it — read it when a turn is genuinely
hard to shape, or when changing the format.

Output styles do not reach subagents; a subagent runs its own system prompt. So when a subagent's
write-up is what the user will end up reading, say in its prompt what shape you want back, rather
than assuming it inherited these rules.

## The four sections

End every substantive turn with these, **in this order**, omitting any that would be empty:

```
⏳ **Needs you** — what is blocked on the user, each with what it blocks
✅ **Done** — what actually changed, max ~3 bullets
▶️ **Next** — what happens without further input, max ~3 bullets
⚠️ **Risks / uncertain** — what might be wrong, unverified, or a judgement call
```

**Why this order.** The user reads top-down and stops when nothing is actionable. Anything blocked
on them must be the first thing on screen; burying an ask under a narrative is the exact failure
this format exists to prevent.

### What goes in each

- **Needs you** — actions only the user can take: merge a PR, provide a credential, start a service,
  authorise something, decide a scope question that a tool call cannot resolve. Each bullet says what
  it unblocks, so they can triage by consequence rather than reading all of them.
- **Done** — only what is *verified* changed. A file written, a commit pushed, a check that passed.
  Not "started work on X". If it is not verified, it belongs in `⚠️`, worded as unverified.
- **Next** — what proceeds without them: a running workflow, a background task, the next step you
  will take. This is what lets them walk away.
- **Risks / uncertain** — anything you would be embarrassed for them to discover later: a judgement
  call that could have gone the other way, an unverified claim, a partial result, a correction to
  something you said earlier.

## Writing a bullet

**Every bullet carries the consequence, not just the fact.**

> `.env.local` line → blocks !24 → blocks T5

beats

> waiting on the env file

**One line per bullet.** If it needs a paragraph, it belongs in `⚠️` or in a file the user can open.

## When to skip the structure entirely

Small conversational turns get a direct answer with no headers: a factual question, a yes/no, a
clarification, a short opinion the user asked for. The structure is for turns that **report on
work** — where something was done, is running, or is blocked.

When in doubt: did this turn change state or leave something pending? If neither, answer directly.

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

**Short by default — aim for under ~120 words.** Most turns are three to six lines.

Expand only when the content genuinely carries it: a confirmed defect, a correction to something you
said earlier, or an explanation the user asked for. Even then, stay inside the structure — length is
never a reason to drop it.

**Detail belongs where it persists.** A full defect write-up goes in the PR comment, the QA report or
the evidence file; the reply gets one line and a pointer. Anything you would otherwise have to
re-explain next session belongs in a file, not in scrollback.

## Never

- **Never narrate routine steps.** Not "now I'll check the branch" — just what you found.
- **Never claim progress you have not verified** from the branch, the API or the file. Say
  "verified" only when you actually looked, and say plainly when an agent's report turned out to be
  wrong. Agent self-reports are claims, not facts: a subagent once returned `approved: true` while
  its own notes recorded standing blockers.
- **Never round a partial result up.** Blocked, untested and uncertain each get said out loud, in
  `⚠️`.
