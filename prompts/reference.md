# pu — reference

What this unit accepts, what it refuses, and why.

## The record

| field | source | notes |
|---|---|---|
| `uuid` | store | the only identity. Never use the short `id`. |
| `description` | caller | one line |
| `body` | body store | optional markdown, keyed by uuid |
| `url` | caller | external authority; absent means we are it |
| `kind` | caller | closed list, see below |
| `parent` | caller | what it is part of |
| `project` | caller | dotted scope, prefix-matched on read |
| `tags` | caller | each names a mandatory procedure; `afk` is the run opt-in |
| `depends` | caller | native blocking |
| `urgency` | computed | ordering across every source |

A record's long form lives in exactly one of three places: **external**
(`url` set), **local** (a body blob), or **absent** (one line was enough).

## Kinds, and which are runnable

| kind | runnable by an agent | session type |
|---|---|---|
| `execution` | yes, with `afk` | `execution` |
| `research` | yes, with `afk` | `research` |
| `intake` | yes, with `afk` | `intake` |
| `task` | no | — |
| `prototype` | **never** | — |
| `grilling` | **never** | — |
| `map` | no | — |

`grilling` and `prototype` are human-in-the-loop by definition: the agent
never stands in for the human's side of them. They are unrunnable
permanently, not pending.

Selection is **default-deny on both counts**: a task must carry `afk`
*and* have a kind with a session type behind it. A kind nobody has built a
session type for is inert rather than handed to whichever type sorts
first.

## What it refuses

- An unknown `kind`. The store's own closed value list rejects it — exit
  2, nothing written — so this is not a check that can be bypassed.
- A write that is not one of the typed operations. There is no
  create-arbitrary-task endpoint, by design.
- Addressing anything by numeric id.
- A body for a uuid that is not a uuid.

## What degrades rather than failing

An unreachable or uninitialised store reports zero tasks. A filter
matching nothing returns an empty list. A task that does not exist reads
as `null`, not an error. Absence is normal throughout.

The exception is the list above: those are errors a caller sees, because
degrading there would let bad records into a queue that agents act on.

## Ordering

There is no priority ladder in code. Ordering across every source is
Taskwarrior's urgency, with per-kind coefficients in the unit's schema.
Changing what PU works on next is a configuration change, and the ordering
is inspectable by hand.

## Two things that are deliberately not here

**PU does not edit a map's Decisions-so-far when a ticket resolves.**
Choosing the one-line gist is judgement. PU records the answer and closes
the ticket; the caller rewrites the map body with `set_body`.

**PU does not create tasks on a peer's say-so.** A peer's request becomes
an `intake` record. A session converts it, or closes it saying what was
missing.
