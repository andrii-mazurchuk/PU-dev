# Issue tracker: Taskwarrior, via the pu unit

Tasks for this repo live in Taskwarrior, held by the **pu** processing
unit. Two interfaces, and which one you use depends on what you are
touching:

- **`task`, the CLI** — everything Taskwarrior natively models: creating
  tickets, blocking, the frontier, claiming, resolving, reading. Works
  whether or not pu is running.
- **pu's HTTP API** — long-form bodies only, as raw markdown. A map body
  and a ticket's question are documents; Taskwarrior holds one-line
  descriptions and append-only annotations, so documents live in pu and
  are fetched with `curl`.

The rule: **use `task` for the task graph, HTTP for prose.**

## Setup

Every command needs pu's schema, or `kind:` and `parent_uuid:` do not
exist and your writes are rejected. pu writes the config file on start
and prints its path:

```bash
export TASKRC=/path/to/pu/state/taskrc     # or pass rc:<path> per command
export PU=http://127.0.0.1:9001
```

Check it: `task rc:$TASKRC _unique kind` should not error.

## Conventions

- **Create**: `task add kind:<kind> project:<scope> +tag -- "one-line title"`.
  Always put the description after `--`; without it a title containing
  `:` or a leading `+` is reparsed as an attribute or a tag.
- **Read one**: `task <uuid> export`. Always address by **uuid**, never by
  the short numeric id — Taskwarrior renumbers ids as work completes, so
  an id held across two commands points at a different task.
- **List**: `task status:pending export`, plus filters.
- **Comment**: `task <uuid> annotate -- "text"`. Annotations hold
  multi-line text fine and are the record of *what happened* to a task.
- **Close**: `task <uuid> done`.
- **Never** put multi-line text in a `key:value` argument. It exits 0,
  silently discards the value, and overwrites the description.

## Bodies

A body is the *statement* of a thing — a map's five sections, a ticket's
question. An annotation is *what happened* to it. Keep them apart.

```bash
curl -s $PU/tasks/<uuid>/body > map.md            # read
curl -s -X POST --data-binary @map.md \
     -H "Content-Type: text/markdown" $PU/tasks/<uuid>/body   # replace
```

Raw markdown both ways: no JSON, no escaping, no `jq`. A body is replaced,
not appended.

## When a skill says "publish to the issue tracker"

Create a task with `task add`, and put its long form in the body.

## When a skill says "fetch the relevant ticket"

`task <uuid> export`, then `curl -s $PU/tasks/<uuid>/body` if you need its
question in full.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a single task; its tickets are tasks
pointing at it.

- **Map**: a task with `kind:map`. Its body holds Destination, Notes,
  Decisions-so-far, Not-yet-specified and Out-of-scope.
  ```bash
  task add kind:map project:<effort> -- "<destination>"
  task +LATEST export        # take the uuid
  curl -s -X POST --data-binary @map.md -H "Content-Type: text/markdown" \
       $PU/tasks/<map-uuid>/body
  ```

- **Child ticket**: a task carrying `parent_uuid:<map-uuid>` and a
  `kind:` of `research`, `prototype`, `grilling` or `task`. The question
  goes in the body. Add `+afk` **only** for a ticket an agent may resolve
  alone — `grilling` and `prototype` are human-in-the-loop by definition
  and must never carry it.
  ```bash
  task add kind:research parent_uuid:<map-uuid> project:<effort> +afk \
       -- "what does it cost"
  ```
  Tags also select **mandatory procedures**. Consult the catalogue first
  and attach only tags that exist — an invented tag carries no procedure
  and nobody finds out:
  ```bash
  curl -s $PU/sops
  ```

- **Blocking**: Taskwarrior's native dependencies, which is what makes a
  blocked ticket disappear from the frontier and show as `+BLOCKED` in
  its own UI. Wire in a second pass, after the tickets have uuids.
  ```bash
  task <uuid> modify depends:<blocker-uuid>,<blocker-uuid>
  ```

- **Frontier query**: open, unblocked, unclaimed children of the map,
  most urgent first. `+READY` is Taskwarrior's own virtual tag for
  pending-and-unblocked-and-not-waiting; `-ACTIVE` drops what someone has
  claimed.
  ```bash
  task +READY -ACTIVE parent_uuid:<map-uuid> export
  ```
  This deliberately includes human-in-the-loop tickets: you need to see
  that the next thing is a grilling ticket, or the map looks finished
  when it is not.

- **Claim**: `task <uuid> start`. The session's first write, before any
  work, so a concurrent session skips it. Release with `stop`.

- **Resolve**: post the answer, close, then append a one-line gist to the
  map's Decisions-so-far.
  ```bash
  task <uuid> annotate -- "$(cat answer.md)"
  task <uuid> done
  # then edit map.md and POST it back
  ```

### Tickets an agent resolves for you

A ticket tagged `+afk` whose kind is `research` may be picked up by pu and
resolved unattended, between your sessions. You do not need to do anything
differently: it writes its answer as an **annotation** and closes the
ticket, exactly where you would have. Next time you work the map, that
ticket is closed and `task <uuid> export` has the answer.

So a closed ticket reads the same whether you resolved it or pu did — and
reading one needs only `task`, not pu.
