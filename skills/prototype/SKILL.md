---
name: prototype
description: Build a throwaway prototype to answer a design question. Use when the user wants to sanity-check whether a state model or logic feels right, or explore what a UI should look like.
---

# Prototype

A prototype is **throwaway code that answers a question**. The question
decides the shape.

## Before anything: is there somewhere for it to live?

A prototype is code, so it needs a repository — one with a real
implementation nearby, or at least somewhere a throwaway branch can exist.

**If there is no repository, stop and say so.** Do not invent a home for
it. During a specification effort there is often no implementation yet,
and that is a real signal: a question that can only be answered by
building something, asked before there is anywhere to build, is a question
arriving too early. Say that, and leave the ticket open for whoever holds
the map to re-scope or re-order.

This is a human-in-the-loop skill: it never runs unattended, so there is
always someone to tell.

## Pick a branch

Identify which question is being answered, from the prompt, the
surrounding code, or by asking:

- **"Does this logic / state model feel right?"** → [LOGIC.md](LOGIC.md).
  A single shareable HTML file — free-play buttons plus tabbed guided
  walkthroughs — that pushes the state machine through cases that are hard
  to reason about on paper, drivable by a non-developer.
- **"What should this look like?"** → [UI.md](UI.md). Several radically
  different UI variations on a single route, switchable via a URL search
  param and a floating bottom bar.

The two branches produce very different artifacts, so getting this wrong
wastes the whole prototype. If it is genuinely ambiguous and the user is
unreachable, default to whichever matches the surrounding code — a backend
module → logic, a page or component → UI — and state the assumption at the
top of the prototype.

## Rules that apply to both

1. **Throwaway from day one, and clearly marked.** Put it close to where
   it will actually be used so context is obvious, but name it so a casual
   reader can see it is a prototype. For throwaway UI routes, obey the
   project's existing routing convention; don't invent a new top-level
   structure.
2. **Trivial to run.** A UI prototype starts from one command in the
   project's task runner. A logic demo is a single HTML file the user
   double-clicks. No thinking required to start it.
3. **No persistence by default.** State lives in memory. Persistence is
   the thing the prototype is *checking*, not something it should depend
   on. If the question genuinely involves a database, use a scratch one
   with an obvious "PROTOTYPE, wipe me" name.
4. **Skip the polish.** No tests, no error handling beyond what makes it
   runnable, no abstractions. The point is to learn something fast.
5. **Surface the state.** After every action, or on every variant switch,
   render the full relevant state so the user can see what changed.

## Capturing it when done

Three separate things happen, and only the first is the point:

**The decision** folds into the real code, on the branch that work belongs
on.

**The prototype itself** goes to a throwaway branch — `prototype/<name>` —
out of main. It is a primary source: the next person asking "why is it
like this?" can run the thing that settled it. Variant components and
switchers left in main rot fast and confuse the next reader.

**The answer** is recorded as the ticket's resolution, and the ticket is
closed:

```bash
task <uuid> annotate -- "$(cat verdict.md)"
task <uuid> done
```

The verdict says which option won, **why**, and what the prototype ruled
*out* — the discarded options are half of what was learned. Link the
throwaway branch from it rather than describing the code.

Then append a one-line gist to the map's Decisions-so-far and POST the map
body back, the same as any other resolved ticket.

---

*Adapted from `prototype` in [mattpocock/skills](https://github.com/mattpocock/skills)
(MIT). Changed: refuses to run without a repository to live in, and the
verdict is recorded as the ticket's resolution rather than on a
tracker-specific implementation issue.*
