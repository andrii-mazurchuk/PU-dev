---
name: domain-modeling
description: Build and sharpen a project's domain model. Use when discussing codebase terminology, or writing or editing a CONTEXT.md glossary.
---

# Domain Modeling

Actively build and sharpen the project's domain model as you design. This
is the *active* discipline: challenging terms, inventing edge-case
scenarios, and writing the glossary down the moment a term crystallises.
Merely *reading* `CONTEXT.md` for vocabulary is not this skill — that is a
one-line habit any skill can do. This is for when you are **changing** the
model.

It runs alongside `grilling`, not after it: grilling drives the question
tree, this keeps the words honest while it does.

## Why this matters more here than in an ordinary codebase

A specification in this system is a graph of derived claims — intent to
behaviour to architecture to spec — and every layer inherits the words of
the one above. A behaviour reading *"the system archives an account"* is
worthless if "account" and "archive" each mean two things, and the
ambiguity propagates silently into everything derived from it.

**Vocabulary drift is the cheapest way to produce a graph that passes
every gate and means nothing.** This skill is what catches it at the
moment a word is first used loosely, which is the only cheap moment.

## Where the glossary lives

`CONTEXT.md` at the root of the project repository. One file, one context.

**Not in the spec store.** That graph holds *claims*; a glossary holds
*definitions*. They are complementary and kept apart deliberately —
duplicating one into the other gives two statements of the same thing,
and two statements drift. The glossary is the vocabulary the spec entries
are *written in*, which is why a term resolved here is a term those
entries must then use.

Create the file lazily: only when the first term is resolved.

`CONTEXT.md` is a glossary and nothing else. It carries no implementation
detail, no spec, no scratch notes.

## During the session

### Challenge against the glossary

When a term conflicts with the existing language, call it out
immediately. "Your glossary defines 'cancellation' as X, but you seem to
mean Y. Which is it?"

### Sharpen fuzzy language

When a term is vague or overloaded, propose a precise canonical one.
"You're saying 'account': do you mean the Customer or the User? Those are
different things."

### Discuss concrete scenarios

When domain relationships are being discussed, stress-test them with
specific scenarios. Invent ones that probe edge cases and force precision
about the boundaries between concepts.

### Cross-reference with code

When the user states how something works, check whether the code agrees.
If you find a contradiction, surface it: "Your code cancels entire Orders,
but you just said partial cancellation is possible. Which is right?"

### Update CONTEXT.md inline

When a term is resolved, write it down right there. Don't batch these up;
capture them as they happen. Use the format in
[CONTEXT-FORMAT.md](./CONTEXT-FORMAT.md).

---

*Adapted from `domain-modeling` in [mattpocock/skills](https://github.com/mattpocock/skills)
(MIT). Changed: ADRs removed — a resolved ticket already records the
reasoning behind a decision, and a second decision log drifts from the
first. Multi-context `CONTEXT-MAP.md` layouts removed as unused. Added why
vocabulary discipline is load-bearing for a derived-claim specification,
and where the glossary sits relative to it.*
