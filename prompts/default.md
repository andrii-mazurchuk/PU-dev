# pu — the processing unit

PU holds a queue of work and runs agent sessions against it. It is the only
unit in this system that launches `claude -p` sessions.

## What it holds

One store, one record shape. Every task PU knows about is the same record
whatever it came from — a wayfinder decision ticket, a mirrored GitHub
issue, a task a trigger emitted. The differences are fields:

- `kind` — what resolving it produces, and so which session type runs it.
  One of `execution`, `research`, `task`, `prototype`, `grilling`, `map`.
  The list is closed; an unknown kind is refused.
- `project` — the effort it belongs to, dotted and hierarchical. Filtering
  by a project also returns the scopes nested under it.
- `tags` — each tag names a mandatory procedure for that task. Also carries
  `afk`, which is the explicit opt-in to an agent working it at all.
- `depends` — blocking. A blocked task drops out of every frontier query
  until its blockers close.
- `url` — set when something else owns the text (a GitHub issue). Absent
  means this record is the authority.
- `parent` — what it is part of, e.g. the map a ticket hangs off.

Tasks are addressed by **uuid**, never by the short numeric id. That id is
renumbered as work completes, so one held across two calls silently
addresses a different task.

## What you can ask it

**Reading is open.** `query_tasks`, `get_task`, `list_projects`,
`get_frontier`, `get_stats`. `get_frontier` is the useful one: open,
unblocked, unclaimed, agent-runnable work, most urgent first.

**Writing is not open, and the shape of the write is the permission.**
There is no endpoint that takes an arbitrary task. The door is the
wayfinding operations — `create_map`, `create_ticket`, `wire_blocking`,
`claim_task`, `resolve_task`, `set_body` — for a session charting or
working a map.

If you are another unit and you want something done, that is your door
too: create a typed ticket with `create_ticket`, choosing the `kind` that
says what resolving it produces. A ticket hangs off a map (`parent`), so
`create_map` first if your work has none. There is no channel for untyped requests.
If what you have is a person's unshaped input, it belongs at cu, where a
human is.

## What it will not do

PU does not decide what work exists. It runs work that is already on the
queue, and the only way onto that queue from outside is a typed ticket.

It does not store specifications, decisions, or memory — those live
elsewhere. A task here is a pointer to work, plus enough to rank it.
