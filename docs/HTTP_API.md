# HTTP API

Everything routed in `pu/server.py` — `do_GET` at `:284`, `do_POST` at
`:366`. That module is the only one that knows HTTP; every handler is a
thin call into `pu/service.py`.

Served on `127.0.0.1:9001` by default (`PU_PORT`), over a
`ThreadingHTTPServer`. No authentication: this is a loopback API and the
gateway is the only thing that exposes units to each other.

## Status codes

| code | when |
|---|---|
| `200` | a successful read, or a successful non-creating write |
| `201` | `POST /maps`, `POST /tickets` — something was created |
| `202` | `POST /inbox` — accepted for consideration, not for doing |
| `400` | `ServiceError`, `RecordError`, `BodyError` — the request cannot be served as asked |
| `404` | no such route, no such task, no body, unknown prompt tier |
| `502` | `TaskError` — the `task` binary itself failed. Carries its stderr. |

The `502` is the honest code: a failure of the store behind this unit, not
of the request. `TaskError` messages include the binary's own stderr,
which is the useful part — a rejected UDA value names the exact value.

## Request bodies

`POST` bodies are JSON objects by default. One exception, at
`pu/server.py:259`: a request whose `Content-Type` starts with `text/` is
read as **raw text** instead. Only `text/*` — an absent or unrecognised
content type falls through to JSON, so an existing caller cannot be
silently reinterpreted.

That path exists for `POST /tasks/<uuid>/body`, so a 5KB map containing
quotes and backticks needs no escaping from whoever is sending it:

```bash
curl -X POST --data-binary @map.md \
     -H "Content-Type: text/markdown" \
     http://127.0.0.1:9001/tasks/<uuid>/body
```

Path segments are percent-decoded individually (`pu/server.py:274`). A
uuid is safe either way, but a dotted project scope is not, and an
undecoded path silently 404s for every identifier containing a reserved
character.

---

# The four standard endpoints

## `GET /health`

Boolean liveness only. What the gateway polls on an interval.

```json
{"status": "ok"}
```

A constant. It does not touch the task store, so it answers immediately
even while a store behind WSL is cold — liveness of this process is the
only question it is asked. Whether the store is reachable shows up in
`/stats`.

## `GET /stats`

The envelope is identical across every unit in the system; `metrics` is
unit-specific. **Mechanical data only — never a verdict** (see
`ARCHITECTURE.md` §what pu is not).

```json
{
  "unit": "pu",
  "computed_at": "2026-09-08T17:04:11.229+00:00",
  "metrics": {
    "pending_tasks": 12,
    "by_kind": {"execution": 4, "research": 6, "map": 2},
    "runnable_now": 3,
    "claimed": 1,
    "blocked": 5,
    "bodies": 9,
    "body_bytes": 41233,
    "sessions": {
      "runs": 27,
      "cost_usd_total": 8.114,
      "by_session_type": {
        "research": {"runs": 14, "failures": 1, "cost_usd": 5.02,
                     "avg_duration_ms": 61204, "avg_cost_usd": 0.358}
      },
      "by_outcome": {"resolved": 22, "bounced": 2, "failed": 3},
      "last_run_at": "2026-09-08T16:40:02.117+00:00"
    }
  }
}
```

`metrics.sessions` is present only when a session store is wired
(`pu/server.py:294`). An unreachable or uninitialised task store degrades
the task counts to zero rather than taking the endpoint down
(`pu/service.py:301`).

## `GET /prompts/<tier>`

This unit's own self-description, as raw markdown — what a *peer* learns
about pu when assembling its own session context. Two tiers exist,
`default` and `reference`; anything else is a `404`.

Distinct from a session type's `CLAUDE.md`, which Claude Code loads
natively from cwd and which no other unit ever reads.

## `GET /tools`

The hand-written manifest the gateway's MCP bridge discovers and
dispatches through. `pu/server.py:26`. Excludes `/health` and `/tools`
itself.

```json
{"unit": "pu", "tools": [{"name": "...", "description": "...",
                          "method": "GET", "path": "/tasks/{uuid}",
                          "input_schema": {...}}]}
```

Names are action-style rather than path echoes, and **one route served
under two methods is two tools** — `get_body` and `set_body` both live at
`/tasks/{uuid}/body`.

---

# Reads — open to every peer

## `GET /tasks`

| query param | meaning |
|---|---|
| `project` | dotted scope. **Prefix-matched**: `project=mu-spec` also returns `mu-spec.behaviour`. A project is a scope, and scopes nest. |
| `kind` | one of the closed list. An unknown kind is a `400`. |
| `status` | `pending` (default), `completed`, `deleted`, `waiting` |
| `parent` | uuid of a map, to list only its children |

```json
{"tasks": [{"uuid": "...", "description": "...", "status": "pending",
            "kind": "research", "project": "pu.docs", "url": null,
            "parent": "...", "repo": null, "tags": ["afk"],
            "depends": [], "urgency": 12.4, "afk": true, "active": false,
            "session_type": "research", "runnable": true}]}
```

`afk`, `active`, `session_type` and `runnable` are **derived and
serialised for the caller** so a consumer never has to reimplement
runnability — which would be a second copy of `SESSION_TYPE_FOR_KIND`.

## `GET /tasks/{uuid}`

The same shape plus `"body"` when the task has one. `404` if no such task.

Addressed by uuid only. The short numeric id is renumbered as work
completes; one held across two calls addresses a different task.

## `GET /tasks/{uuid}/body`

**Raw markdown, not JSON** (`text/markdown; charset=utf-8`). `404` when
the task has no body.

This is not a nicety. It means a caller outside this unit reads a map with
one curl and no `jq` — and `jq` is not present by default on Windows,
while piping JSON through a shell to extract a field is exactly where the
quoting bugs live.

```bash
curl -s http://127.0.0.1:9001/tasks/<uuid>/body > map.md
```

## `GET /projects`

Every project with open work, and how much. Derived from the tasks
themselves — there is no project record to go stale.

```json
{"projects": [{"project": "mu-spec", "open_tasks": 4}]}
```

## `GET /frontier`

Open, unblocked, unclaimed work, most urgent first. Optional `?parent=`
scopes it to one map's children.

**Includes human-in-the-loop tickets.** A person working a map needs to
see that the next thing is a grilling ticket; hiding it would make the map
look finished when it is not.

## `GET /queue`

The frontier narrowed to what pu itself may select: work that carries
`afk` **and** whose kind has a session type. Optional `?parent=`.

Strictly narrower than `/frontier`, and the two must never be merged —
that conflation is what would let a session pick up a conversation only a
person can have.

## `GET /sops`

```json
{"sops": [{"tag": "integration", "description": "...", "kinds": ["execution"]}],
 "broken": []}
```

`broken` lists directories that claim to be an SOP and are not usable — no
`SKILL.md`, or no parseable description. Checked against the filesystem
rather than a list, so it cannot go stale.

An empty catalogue is a supported answer, not a misconfiguration.

---

# Writes — door 1: the wayfinding operations

## `POST /maps` → `201`

```json
{"destination": "how pu should mirror GitHub issues", "project": "pu", "body": "# ..."}
```
→ `{"uuid": "..."}`

A map is a record with children, never itself worked: it carries no `afk`
tag and `map` has no session type, so it **can never be selected**.

Its body is the map markdown — Destination, Notes, Decisions so far, Not
yet specified, Out of scope.

## `POST /tickets` → `201`

```json
{"parent": "<map uuid>", "title": "...", "kind": "research",
 "body": "the question this ticket resolves",
 "tags": ["integration"], "afk": true, "project": "pu"}
```
→ `{"uuid": "..."}`

`parent`, `title` and `kind` are required. A nonexistent parent is a
`400`, as is a kind outside the closed list.

**`afk` defaults to `false` and must be set explicitly.** It is
default-deny, not a filter applied later: a ticket type nobody has thought
about cannot become agent-runnable by accident. `grilling` and `prototype`
are human-in-the-loop and must never be marked `afk` — and even if they
are, they stay inert, because they have no session type.

## `POST /tasks/{uuid}/blocking` → `200`

```json
{"blocked_by": ["<uuid>", "<uuid>"]}
```
→ `{"uuid": "...", "blocked_by": [...]}`

A nonexistent task or blocker is a `400`, naming which.

Blocking is Taskwarrior's native `depends`, which is what makes `+READY`
mean what wayfinder means by the frontier. It is wired in a **second
pass** because tickets need uuids before they can reference each other.

## `POST /tasks/{uuid}/claim` → `200`

→ `{"uuid": "...", "claimed": true}`
or `{"uuid": "...", "claimed": false, "reason": "already claimed"}`

Claiming should be a session's **first write, before any work**, so a
concurrent session skips the task. It sets Taskwarrior's `start`, hence
`+ACTIVE` — the claim is the store's own state rather than a convention
layered on top, which is why it survives a crash and why the reaper
exists.

An already-claimed task is reported, not an error.

## `POST /tasks/{uuid}/resolve` → `200`

```json
{"answer": "the resolution, in full"}
```
→ `{"uuid": "...", "resolved": true}`

Records the answer as an annotation and closes the task.

**It deliberately does not touch the parent map's Decisions-so-far.**
Choosing the one-line gist that goes there is judgement, and belongs to
the caller, which writes it with `set_body`. Mechanical here, judgement
there.

## `GET` / `POST /tasks/{uuid}/body`

`GET` is documented above. `POST` replaces the body wholesale — used to
rewrite a map after a decision lands. Accepts either
`{"body": "..."}` as JSON or the raw text path described at the top.

→ `{"uuid": "...", "bytes": 4211}`

No history, no versioning: a map's history is the closed tickets it points
at.

---

# Writes — door 2: the inbox

## `POST /inbox` → `202`

```json
{"source": "cu", "message": "the request, as worded", "project": "pu"}
```
→ `{"uuid": "...", "accepted": true}`

The `202` is exact: **accepted for consideration, not for doing.**

This endpoint cannot mint anything but one `kind:intake` record. The
message becomes that record's body; its description is
`intake from <source>: <first line, truncated to 120 chars>`. It is tagged
`afk`, because converting it is the one thing pu can always do alone.

An intake session later reads it and either creates real tasks from it or
resolves it with what was missing — so a peer can cause work to be
*considered* without deciding that work exists.

---

# `POST /trigger` → `200`

Not gateway-config, but gateway-called: a `persistent` unit that declares
a `trigger` is **poked** here on schedule rather than relaunched.

Runs exactly one tick, **synchronously**, and returns its result:

```json
{"ran": true, "reason": "", "task_uuid": "...", "session_type": "research",
 "outcome": "resolved", "detail": "...", "cost_usd": 0.607}
```

`ran: false` with a reason covers every non-run: `"nothing runnable"`,
`"a tick is already running"`, `"daily cost cap reached: 4.2 >= 4.0"`,
`"blocked after repeated dispatch"`, `"no pipeline wired"`.

Synchronous and single-tick by design. The cost gate runs **inside** the
tick (`pu/pipeline.py:288`), so there is no path in that can route around
it — a gate an external trigger can bypass is not a gate.

Calling this **spends a real session and real money.**


## `GET /dashboard`

The panel spec the node's console renders. `application/json` is what
tells it this is the spec tier -- the tier is read off the response,
never declared.

Built per request rather than served from a file: the meters carry the
account-usage ceilings this process was given, which come from the
environment. See `pu/dashboard.py`.

```json
{ "unit": "pu", "title": "Processing unit", "lede": "...",
  "pages": [ { "id": "overview", "title": "Overview", "panels": [ ... ] } ] }
```

## `GET /gate`

What the two gates would decide right now, without deciding it.

A **separate read from a tick, deliberately**: a dashboard polls this,
and if the answer came from running a tick then looking at a page would
spend money.

```json
{ "blocked": false, "reason": "",
  "summary": [ { "key": "Sessions", "value": "clear", "note": "..." } ],
  "cost": { "spent_today_usd": 0.42, "daily_cap_usd": 5.0, "blocked": false },
  "windows": [ { "name": "five-hour", "utilization": 0.41, "ceiling": 0.70 },
               { "name": "seven-day", "utilization": 0.12, "ceiling": 0.80 } ],
  "ceilings": { "five_hour": 0.70, "seven_day": 0.80 } }
```

Each window carries **its own** ceiling, because they are separate
limits rather than an average -- a meter drawn against the other one's
limit reads "clear" while the gate is actively blocking on it.

Both readings degrade to "nothing is blocking" when absent, which is what
keeps the gates from latching shut -- see `pu/usage_gate.py`.

## `GET /sessions`

Recorded runs, most recent first. `limit` (default 50, max 500) and
`task` (a uuid) narrow it. Ordered on `recorded_at`, not on the directory
name: the stamp is when the run started.

## `GET /sessions/{task_uuid}/{run_id}`

One run, with the prompt it was given. The raw stream is **not**
included -- it is the largest thing on disk and nothing renders it;
whoever needs it reads the file.

## `GET /spend`

Daily cost, oldest first. `days` (default 14, max 90). Quiet days are
present as zero rather than omitted: a series that skips them renders as
continuous work.

## `POST /ask` → `202`

Ask this unit a question. Returns at once, before the session runs.

```json
{ "question": "why has nothing run today?" }
```

The response is **exactly** `{"id": "..."}` whatever happened -- a fixed
shape, so a caller has one path to render rather than two. A blocked
gate, an empty question and an over-long one all produce an id whose
record is already `error`.

Spends money. Refused rather than queued when either gate is blocking.
One question runs at a time for the whole unit.

## `GET /ask/{id}`

```json
{ "status": "running" | "done" | "error",
  "question": "...", "answer": "...", "error": "", "cost_usd": 0.031,
  "at": "2026-09-10T14:22:01+00:00" }
```

`running` continues; `done` and `error` are terminal. `answer` is model
output derived from task descriptions and bodies that other agents and
`POST /inbox` wrote -- **render it as escaped text, never as markup.**

## `POST /trigger` → `200`

One tick: gate, select, claim, run, record. Declared as the `run_tick`
tool, so it is callable by any peer or MCP client that reaches the
bridge. Spends money. The gate runs inside the tick, so there is no path
in that routes around it; blocked returns `ran: false` with the reason
and changes no task's state.

```json
{ "ran": false, "reason": "nothing runnable", "task_uuid": null,
  "session_type": null, "outcome": null, "detail": "", "cost_usd": null }
```
