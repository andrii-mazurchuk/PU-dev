# Architecture

## What pu is

A unit in a holonic-node harness. It holds the task queue for the whole
system, and it is the only unit that launches `claude -p` sessions.

Two sentences carry most of the design:

> **Every task is the same record, whatever it came from.** A wayfinder
> decision ticket, a mirrored GitHub issue, a message a peer pushed at the
> inbox — one store, one shape. The differences are fields.

> **pu executes ratified work; it never decides what work exists.**
> Ratification is an act a *session* performs against a written procedure,
> never a side effect of an HTTP call.

Everything below is consequence.

## What pu is not

- **Not a specification store.** Claims and decisions live in the spec
  unit; a task here is a pointer to work plus enough to rank it.
- **Not a glossary.** `CONTEXT.md` lives in the project repository.
- **Not a memory or log store.** Session logs go to whatever peer declares
  the `log_write` capability (`pu/logs_client.py`). What stays local is
  only what that entry deliberately does not carry.
- **Not a judge of its own work.** `/stats` reports mechanical counts.
  "The queue is unhealthy" is an analytical unit's call; computing it here
  would be pu grading itself.
- **Not a CLI.** There is deliberately no `pu` command. A session outside
  this unit uses the `task` binary for the task graph and two `curl` calls
  for long-form bodies. `task` for structure, HTTP for prose.

## The enclosure boundary

A unit is its own repository, its own process, its own private storage,
reachable only through its local HTTP API. That is the standard, and it is
enforced here by construction rather than by convention:

```
                      ┌──────────────────────────────────┐
   gateway ──────────▶│  HTTP  :9001                     │
   (start, poll,      │   ├─ /health /stats /prompts     │
    poke /trigger)    │   ├─ /tools                      │
                      │   ├─ reads: open to every peer   │
   peers ────────────▶│   └─ writes: two typed doors     │
   (inbox, reads)     ├──────────────────────────────────┤
                      │  service.py   (no HTTP)          │
                      │  pipeline.py  (one tick)         │
                      ├──────────────────────────────────┤
   a session ────────▶│  taskstore.py ──▶ `task` binary  │
   outside pu, via    │  bodies.py    ──▶ state/bodies/  │
   the `task` CLI     │  sessions.py  ──▶ state/sessions/│
   and two curls      └──────────────────────────────────┘
                                    │
                      spawns        ▼
                      claude -p, cwd = a session type dir
                      (or the target repo, for `execution`)
                      no route back in
```

Two things cross that boundary and are worth naming precisely:

1. **The Taskwarrior store is shared with a human or an outside session.**
   That is intentional — it is what makes a `/wayfinder` session, which
   must run interactively and therefore cannot be hosted here, able to
   write. It has one consequence that shaped `taskstore.py`: **pu is not
   the only writer**, so anything that assumed it was (recovering a uuid
   by re-reading "the most recent task") is a race, not a shortcut. See
   `DECISIONS.md` §uuid-on-import.

2. **Sessions pu spawns have no route back in.** Context is assembled
   going in (`pipeline.build_prompt`, `pu/pipeline.py:117`) and the result
   parsed coming out. A session cannot call this unit's API, cannot write
   to the store, and is not given a token that would let it. This is what
   makes intake safe.

## The two write doors

Reading is open to every peer. Writing is not, and **the shape of the
write is the permission** — there is no endpoint that accepts an arbitrary
task.

### Door 1 — the wayfinding operations

`create_map`, `create_ticket`, `wire_blocking`, `claim_task`,
`resolve_task`, `set_body`. Typed, in `pu/service.py:148`–`:256`.

They exist for a wayfinder session, which runs *outside* pu — it is
user-invoked and human-in-the-loop, so it can never be a `claude -p`
session this unit hosts — and which, under enclosure, still has to write.

What that door does **not** grant: it cannot invent an execution task
outside a map, cannot set urgency, and cannot mark anything agent-runnable
by default. `afk` on `create_ticket` is an explicit opt-in defaulting to
false (`pu/service.py:164`), so a ticket type nobody has thought about
cannot become agent-runnable by accident.

### Door 2 — the inbox

`POST /inbox` (`pu/service.py:262`). Everything else: a peer unit, a
person, a message forwarded from a chat.

It does not create a task. It mints **exactly one `kind:intake` record**
and nothing else. An intake *session* is what converts that into real work
or bounces it saying what was missing.

This is the mechanism behind "pu never decides what work exists". A peer
can cause work to be *considered*; it cannot cause work to exist.

## Frontier and queue are two different questions

Both read the same store. They must never be merged.

| | `/frontier` | `/queue` |
|---|---|---|
| for | a **person** working a map | **pu's own** selection |
| filter | `+READY -ACTIVE` | the same, **and** `Task.runnable` |
| includes human-in-the-loop tickets | **yes** | never |
| implemented | `taskstore.ready`, `pu/taskstore.py:289` | `taskstore.runnable`, `pu/taskstore.py:304` |

The frontier includes a `grilling` ticket because a person working a map
has to see that the next thing is a conversation — hiding it would make
the map look finished when it is not.

The queue must not, because a session picking up that ticket would be an
agent standing in for the human's side of an exchange, which produces
nothing. Runnability is a **conjunction and default-deny on both counts**:
an explicit `afk` tag *and* a kind that has a session type behind it
(`pu/records.py:129`). A `grilling` ticket marked `afk` is still inert.

## Module map

Dependencies point downward only. Nothing below imports anything above it.

```
  server.py        routing, the tool manifest. The only module knowing HTTP
  main.py          entrypoint, env config, taskrc rendering, warm thread
      │
  pipeline.py      one tick: gate → select → claim → run → record
      │
  ┌───┴──────────────┬──────────────┬─────────────┬──────────────┐
  service.py      intake.py     guards.py    repo_setup.py   sessions.py
  the operations  proposal →    the two      preparing a     run artifacts,
  as functions    tasks, or a   breakers     target repo     /stats aggregates
                  bounce
  │               │             │                            │
  └───────┬───────┴─────────────┘                        runner.py
          │                                              claude -p + parsing
  ┌───────┴────────┬──────────┬───────────┐
  taskstore.py   bodies.py  sops.py   session_types.py   logs_client.py
  the only        markdown   tag →     dir with a        best-effort
  module that     blob per   procedure CLAUDE.md         session_run
  runs `task`     uuid                                   entries
          │
  records.py      the universal record. Depends on nothing.
```

| module | owns | never does |
|---|---|---|
| `records` | the record, the closed vocabularies, runnability, export parsing | touch the filesystem or a subprocess |
| `taskstore` | every invocation of the `task` binary | know why a task exists |
| `bodies` | one optional markdown blob per uuid | version or diff anything |
| `sops` | resolving a tag to a mandatory procedure | rank or choose between them |
| `session_types` | which types exist and what each may reach | decide which one runs |
| `runner` | building argv, running `claude -p`, parsing the stream | know what a task is |
| `sessions` | local run artifacts, derived aggregates | emit a verdict |
| `logs_client` | `session_run` entries to the logs peer | raise, ever |
| `intake` | validating a proposal, writing it all-or-none | decide what the message meant |
| `repo_setup` | preparing a target repo non-destructively | overwrite or merge anything |
| `guards` | the repeat-dispatch breaker, the stale-claim reaper | delete or close a task |
| `service` | the operations as plain functions | know about HTTP |
| `server` | routing and the hand-written tool manifest | contain business logic |

Two deliberate non-abstractions, both standing decisions:

- **The four standard endpoints are reimplemented per unit**, not
  inherited from a shared base class. Revisit only when a third new unit
  makes the copy-paste cost concrete.
- **`TOOLS` in `pu/server.py:26` is hand-written**, never generated from
  the routing table, because every entry needs a description written for a
  model to read.

## The session layer

**A session type is a directory containing a `CLAUDE.md`.** That is the
whole definition, and discovery is a filesystem walk
(`session_types.discover`, `pu/session_types.py:102`). Adding a type is
dropping a directory in.

Three exist: `intake`, `research`, `execution`.

What is *not* derived from the tree is the **tool grant**, which lives in
code (`ALLOWED_TOOLS`, `pu/session_types.py:51`) because what a session
can reach is a security decision and belongs where it gets reviewed. The
prose in a `CLAUDE.md` is advisory; the grant is what actually holds. A
research session cannot write files because `Write` is not in its list —
the sentence telling it not to is a courtesy on top of that.

| type | grant | runs in |
|---|---|---|
| `intake` | `Read` + the SOP lookup script | its own directory |
| `research` | `Read`, `WebSearch`, `WebFetch` + the lookup script | its own directory |
| `execution` | `Bash`, `Read`, `Write`, `Edit` + the lookup script | **the target repository** |

`intake` and `research` are granted the lookup script specifically
(`Bash(python3 ../../scripts/tag_sop_lookup.py:*)`) rather than `Bash`, so
resolving a mandatory procedure is always possible and reading the
filesystem generally is not.

Skills are **project-scoped for `execution`**, and have to be: that
session's cwd is the target repo, so a skill in this unit's own tree does
not load. `session_types/execution/.claude/skills/` is copied in on every
tick (`FLOWS.md` §4); `ponytail` is what ships there today. The `Skill`
tool itself is not permission-gated — measured — so the grant above needs
no entry for it.

`execution` runs with the **target repository as cwd** so that repo's own
`CLAUDE.md`, conventions and gate hooks apply — a per-repo verification
gate is the strongest safety property available, and it is wired there,
not here. The cost is that this type's own `CLAUDE.md` no longer loads
from cwd, so it is read explicitly and folded into the prompt
(`pu/pipeline.py:339`). It is folded into the *prompt* rather than passed
as `--append-system-prompt` because it is multi-line, and multi-line argv
does not survive the Windows launcher — see `DECISIONS.md`
§no-multi-line-argv.

## Where the guards sit

Two, both in `pu/guards.py`, both existing because of one property: **a
failed session releases its claim.** That is correct — a task must never
be stranded by a session that died — but it means the task drops straight
back to the top of the queue. Without the guards, a task that cannot be
resolved is a task that is retried forever, on a schedule, spending real
money.

- **The repeat-dispatch breaker** counts how many consecutive ticks the
  same task has won selection. Winning repeatedly means it is not
  resolving, because a resolved task is closed and cannot win again. Past
  the threshold (3) it stops being agent-runnable and says why on itself.
  Blocking is expressed by **removing the `afk` tag**, not by inventing a
  status: the task stays open and stays on the frontier a human reads.
- **The stale-claim reaper** releases claims older than two hours. A claim
  is `+ACTIVE` in the store rather than state in memory, which is what
  makes it survive a crash — and therefore what makes it need collecting.

Neither deletes or closes anything. A person is the one who can now act.
