---
name: pu-tasks
description: Read and write the shared task queue held by the pu processing unit — wayfinder maps and their decision tickets, mirrored issues, and work waiting for an agent. Use when asked what is on the queue, what is next, what a map has decided so far, or when creating or resolving a ticket. Also use before starting substantial work, to check whether a ticket already covers it.
---

# The pu task queue

Every task in this system — a wayfinder decision ticket, a mirrored GitHub
issue, a request someone pushed at the unit — is the same record, held in
Taskwarrior by the **pu** processing unit.

## Two interfaces, and which to use

**`task`, the CLI**, for anything Taskwarrior models: creating tickets,
blocking, the frontier, claiming, resolving, reading. Works whether or not
pu is running.

**pu's HTTP API**, for long-form bodies only — a map's five sections, a
ticket's question. Those are documents; Taskwarrior holds one-line
descriptions and annotations.

The full operation reference lives in this repo's issue-tracker doc
(`docs/agents/issue-tracker.md`). Read it before writing anything.

## First, the schema

Bare `task` does not know this unit's fields. pu writes a config file and
prints its path on start:

```bash
export TASKRC=<pu>/state/taskrc
export PU=http://127.0.0.1:9001
```

Without it, `kind:` and `parent_uuid:` are rejected and your writes fail.

## The record

| field | meaning |
|---|---|
| `kind` | `map`, `research`, `prototype`, `grilling`, `task`, `execution`, `intake` |
| `project` | the effort, dotted and hierarchical; filtering matches nested scopes |
| `parent_uuid` | the map a ticket belongs to |
| `depends` | blocking; a blocked task leaves the frontier |
| `+afk` | an agent may resolve this alone |
| tags | each names a **mandatory procedure**; see `curl -s $PU/sops` |

Address tasks by **uuid**, never the short id — ids are renumbered as work
completes.

## Reading

```bash
task status:pending export                       # everything open
task +READY -ACTIVE export                       # takeable now
task +READY -ACTIVE parent_uuid:<map> export     # one map's frontier
task <uuid> export                               # one task, with its answers
curl -s $PU/tasks/<uuid>/body                    # its long form
```

A closed ticket's answer is an **annotation** on it, whether a person or
an agent wrote it. `task <uuid> export` is all you need to read a
resolution.

## Writing

```bash
task add kind:research parent_uuid:<map> project:<effort> +afk -- "the question"
task <uuid> modify depends:<blocker-uuid>
task <uuid> start                                # claim, before any work
task <uuid> annotate -- "the answer"
task <uuid> done
```

Put the description after `--`, always: a title containing `:` or a
leading `+` is otherwise reparsed as an attribute or a tag.

**Never put multi-line text in a `key:value` argument.** It exits 0,
discards the value, and overwrites the description. Long text goes in an
annotation or a body.

## Two things to be careful about

**`+afk` on a `kind:execution` task means pu will run a coding session
against that repository, unattended.** Do not add it casually; leave it
off and let a person decide.

**`grilling` and `prototype` tickets are human-in-the-loop by definition**
— an agent must never resolve one, and must never mark one `+afk`. If the
next thing on a map is a grilling ticket, say so and stop; that is the
signal a person is needed, not an obstacle to route around.
