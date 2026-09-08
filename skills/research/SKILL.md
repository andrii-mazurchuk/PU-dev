---
name: research
description: Investigate a question against high-trust primary sources and record the findings as the resolution of the ticket that asked it. Use when the user wants a topic researched, docs or API facts gathered, or reading legwork delegated to a background agent.
---

# Research

Spin up a **background agent** to do the research, so you keep working
while it reads.

Its job:

1. Investigate the question against **primary sources** — official docs,
   source code, specs, first-party APIs — not a secondary write-up of
   them. Follow every claim back to the source that owns it.
2. Cite each claim's source.
3. **Record the findings as the resolution of the ticket**, then close it.

## Where the answer goes

The answer belongs on the ticket that asked the question, as its
resolution:

```bash
task <uuid> annotate -- "$(cat answer.md)"
task <uuid> done
```

Not a file the reader has to go and find. Two things resolve research
tickets in this system — you, in a session, and the processing unit,
unattended between sessions — and a ticket has to read the same either
way. The unit has no ability to write files at all, so the resolution is
the only representation both can produce.

Write the answer in full there:

1. **The answer**, stated plainly, at the top. One or two sentences
   someone can act on without reading the rest.
2. **What it rests on** — sources, with links or exact references. A claim
   with no citation is a guess wearing a suit.
3. **What you could not establish.** Not a failure to hide: a decision
   made in the knowledge of a gap is safer than one made without it.

If the investigation produced a genuine artifact — a scratch script, a
captured dataset, a comparison table too large to read inline — keep it,
put it somewhere sensible in the repo, and **link it from the answer**.
Linked, not pasted; the resolution stays readable.

## Answer the question you were asked

The ticket names one question. Resolve that one. Reading will open others,
and that is normal — list them at the end under **what this raises**, and
stop. They are other tickets, and charting them is not yours to do.

If the question is unanswerable as asked, say precisely why and what would
make it answerable. That is a real resolution, not a failure.

## Do not decide

You establish facts; you do not choose between options. If you catch
yourself writing "we should…", stop: a recommendation is a decision, and
the decision belongs to whoever holds the map. State the trade-offs and
let them choose.

The exception is a fact that settles a choice by itself — "the API does
not support it" is an answer, not an opinion. Report it as the fact it is.

---

*Adapted from `research` in [mattpocock/skills](https://github.com/mattpocock/skills)
(MIT). Changed: findings are recorded as the ticket's resolution rather
than as a Markdown file placed by repo convention.*
