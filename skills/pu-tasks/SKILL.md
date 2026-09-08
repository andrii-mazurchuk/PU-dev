---
name: pu-tasks
description: Read and write the shared task queue held by the pu processing unit — wayfinder maps and their decision tickets, mirrored issues, and work waiting for an agent. Use when asked what is on the queue, what is next, what a map has decided so far, or when creating, claiming or resolving a ticket. Use as the issue-tracker reference when a skill asks for one, including wayfinding operations. Also worth consulting before starting substantial work, to see whether a ticket already covers it.
---

# The pu task queue

There is **one** queue for everything: wayfinder maps and their decision
tickets, mirrored issues, requests pushed at the unit. All of it is the
same record, held in Taskwarrior by the **pu** processing unit.

One queue is why this document is installed once rather than copied into
each repository — there is no per-repo tracker to describe.

## Two interfaces, and which to use

**`task`, the CLI**, for anything Taskwarrior models: creating tickets,
blocking, the frontier, claiming, resolving, reading. Works whether or not
pu is running.

**pu's HTTP API**, for long-form bodies only — a map's sections, a
ticket's question. Those are documents; Taskwarrior holds a one-line
description and append-only annotations.

The rule: **`task` for the task graph, HTTP for prose.**

## Setup

Every command needs pu's schema, or `kind:` and `parent_uuid:` do not
exist and your writes are rejected. pu writes the file and prints the path
on start — as the *binary* reads it, which under WSL is not the path the
unit wrote to:

```bash
export TASKRC=/mnt/c/.../state/taskrc      # whatever pu printed
export PU=http://127.0.0.1:9001
```

Check it: `task rc:$TASKRC _unique kind` should not error.

## The record

| field | meaning |
|---|---|
| `kind` | `map`, `research`, `prototype`, `grilling`, `task`, `execution`, `intake` |
| `project` | the effort, dotted; filtering matches nested scopes |
| `parent_uuid` | the map a ticket belongs to |
| `depends` | blocking; a blocked task leaves the frontier |
| `repo` | **required for `kind:execution`** — the checkout pu runs the session in. A path the *unit* sees. Without it the task is skipped. |
| `url` | optional; set when something else owns the text (a GitHub issue). Absent means this record is the authority. |
| `+afk` | an agent may resolve this alone |
| other tags | each names a **mandatory procedure**: `curl -s $PU/sops` |

Address tasks by **uuid**, never the short id — ids are renumbered as work
completes, so one held across two commands points somewhere else.

## Conventions

- **Create**: `task add kind:<kind> project:<scope> +tag -- "title"`.
  Always put the description after `--`, or a title containing `:` or a
  leading `+` is reparsed as an attribute or a tag.
- **Read**: `task <uuid> export`, `task status:pending export`.
- **Comment**: `task <uuid> annotate -- "text"`. Annotations hold
  multi-line text and are the record of *what happened* to a task.
- **Close**: `task <uuid> done`.
- **Never** put multi-line text in a `key:value` argument. It exits 0,
  discards the value, and overwrites the description.

## Bodies

A body is the *statement* of a thing — a map's sections, a ticket's
question. An annotation is *what happened* to it. Keep them apart.

```bash
curl -s $PU/tasks/<uuid>/body > map.md
curl -s -X POST --data-binary @map.md \
     -H "Content-Type: text/markdown" $PU/tasks/<uuid>/body
```

Raw markdown both ways: no JSON, no escaping, no `jq`. A body is replaced,
not appended.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a single task; its tickets are tasks
pointing at it. Both are ordinary records — a map is not a special object.

- **Map**: a task with `kind:map`, whose body holds Destination, Notes,
  Decisions-so-far, Not-yet-specified and Out-of-scope.
  ```bash
  task add kind:map project:<effort> -- "<destination>"
  task +LATEST export                      # take the uuid
  curl -s -X POST --data-binary @map.md \
       -H "Content-Type: text/markdown" $PU/tasks/<map-uuid>/body
  ```

- **Child ticket**: a task carrying `parent_uuid:<map-uuid>` and a `kind:`
  of `research`, `prototype`, `grilling` or `task`. The question goes in
  the body. Consult `curl -s $PU/sops` before choosing tags — an invented
  tag carries no procedure and nobody finds out.
  ```bash
  task add kind:research parent_uuid:<map-uuid> project:<effort> +afk \
       -- "what does it cost"
  ```

- **Blocking**: Taskwarrior's native dependencies, wired in a second pass
  once the tickets have uuids.
  ```bash
  task <uuid> modify depends:<blocker-uuid>,<blocker-uuid>
  ```

- **Frontier query**: open, unblocked, unclaimed children of the map, most
  urgent first.
  ```bash
  task +READY -ACTIVE parent_uuid:<map-uuid> export
  ```
  This deliberately includes human-in-the-loop tickets: you need to see
  that the next thing is a grilling ticket, or the map looks finished when
  it is not.

- **Claim**: `task <uuid> start` — the session's first write, before any
  work, so a concurrent session skips it. `stop` releases it.

- **Resolve**: post the answer, close, then append a one-line gist to the
  map's Decisions-so-far and POST the map body back.
  ```bash
  task <uuid> annotate -- "$(cat answer.md)"
  task <uuid> done
  ```

### Tickets an agent resolves for you

A ticket tagged `+afk` whose kind is `research` may be picked up by pu and
resolved unattended, between your sessions. Nothing is different for you:
it writes the answer as an **annotation** and closes the ticket, exactly
where you would have. Next time you work the map, `task <uuid> export` has
the answer.

So a closed ticket reads the same whether you resolved it or pu did — and
reading one needs only `task`, not pu.

## Two things to be careful about

**`+afk` on a `kind:execution` task means pu will run a coding session
against that repository, unattended.** Leave it off and let a person
decide.

**`grilling` and `prototype` are human-in-the-loop by definition** — an
agent must never resolve one, and must never mark one `+afk`. If the next
thing on a map is a grilling ticket, say so and stop. That is the signal a
person is needed, not an obstacle to route around.
