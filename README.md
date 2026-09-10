# pu — the processing unit

Holds the task queue, and runs the agent sessions that work it. It is not
the only unit that launches `claude -p` -- cu runs a session per
conversational turn and au runs its timeline reviews, which is why all
three gate on the account-usage ceilings -- but it is the only one that
selects what to run from a queue.

## The idea in one paragraph

Every task is the **same record**, whatever it came from — a wayfinder
decision ticket, a mirrored GitHub issue, a message a peer pushed at the
inbox. The differences are fields, not separate stores. Holding them
together is what removes the priority ladder: ordering across every source
is Taskwarrior's own urgency with a coefficient per kind, so retuning what
gets worked next is configuration rather than an `if`, and the ordering
stays inspectable by hand.

## Running it

```bash
uv sync && uv run pytest        # or: pip install -e ".[dev]" && pytest
python -m pu.main               # defaults to :9001
```

The dev dependency is declared twice in `pyproject.toml`, as a PEP 735
group *and* as an extra, and they must stay in step. `uv sync` reads the
group and ignores extras; `pip install -e ".[dev]"` (what CI runs) reads
the extra. With only one of them, the other toolchain silently produces an
environment with no pytest in it.

Config is environment, injected by the gateway:

| var | meaning |
|---|---|
| `PU_TASK_CMD` | how to reach the `task` binary. `task` on Linux; `wsl -d Ubuntu -e task` on a Windows host |
| `PU_TASKDATA` | Taskwarrior data location — a path on the *binary's* side |
| `PU_STATE_DIR` | this unit's private storage |
| `PU_PORT` | listen port |

## Where the `task` binary lives

The unit does not care, and nothing outside `taskstore.py` knows. The whole
of it is `PU_TASK_CMD` — the command up to the arguments.

**On Linux, set nothing.** The default is `task`, so a native deployment
needs no configuration at all. That is the ordinary case; the Windows
arrangement below is the special one.

**On Windows there is no native Taskwarrior** — upstream ships source only
— so the binary lives in WSL and is reached through a prefix:

```
PU_TASK_CMD="wsl -d Ubuntu -e task"
```

`-e` rather than `--` is load-bearing, not cosmetic: `--` hands the command
to a login shell, which expands what it is given — backticks disappear,
`${HOME}` interpolates, and command substitution *runs*. Annotations carry
a session's own final message, so that is a path from model output to shell
execution. `normalise_base_cmd` rewrites `--` to `-e` wherever it comes
from, so config cannot get this wrong.

**Mind which side of the boundary a path is on.** Under the WSL
arrangement, `PU_TASKDATA` is a path the *binary* sees — a Linux path,
where `~` expands to the WSL user's home — while `PU_STATE_DIR` and a
task's `repo` are paths *this process* sees, so on Windows they are Windows
paths. Natively on Linux the distinction disappears entirely.

One more Windows trap: a shell may rewrite a `/tmp/...` argument into a
Windows path before the process ever sees it. Git Bash does. Pass WSL paths
from PowerShell, or with `MSYS_NO_PATHCONV=1`.

## The two write doors

Reading is open to every peer. Writing is not, and the shape of the write
*is* the permission — there is no endpoint that accepts an arbitrary task.

- **The wayfinding operations** — create a map, create a child ticket, wire
  blocking, read the frontier, claim, resolve. For an interactive wayfinder
  session, which runs outside this unit and still has to write.
- **`/inbox`** — everyone else. It cannot create work to be done; it mints
  one `intake` record, and a session converts that into tasks or bounces
  it saying what was missing.

## Frontier and queue are not the same query

`/frontier` is what a person working a map sees: open, unblocked,
unclaimed — **including** human-in-the-loop tickets, because hiding them
would make a map look finished when it is not.

`/queue` is what this unit may select: strictly narrower. Runnability is a
conjunction, and both halves are default-deny — an explicit `afk` tag,
*and* a kind that has a session type behind it. A `grilling` ticket marked
`afk` is still inert.

## Sessions

A session type is a directory holding a `CLAUDE.md`; adding one is not a
code change. The tool grant is in code, because what a session can reach is
a security decision and belongs where it gets reviewed.

Sessions this unit spawns have **no way back into it**. Context is
assembled going in and the result parsed coming out. That is what makes
intake safe: it returns a *proposal*, validated against the closed
vocabulary and written whole or not at all.

`execution` is the exception to everything above — it runs with the target
repository as its working directory, so that repo's own conventions and
gates apply.

## The dashboard

`GET /dashboard` answers with a **panel spec** -- JSON describing what to
show, which the node's console renders with its own components. The
contract is the gateway's `docs/UNIT_STANDARDS.md`, section "Dashboards".

The node owns the chrome permanently. This unit declares scopes and
panels; it draws no navigation, links no peer, and renders no other
unit's data. Six visible pages -- overview, queue, tasks, maps, sessions,
spend -- plus two hidden ones reached by drilling into a row.

**The spec computes nothing.** It names what to show; anything that has
to be current lives in the data a panel fetches. An earlier draft baked
the account-usage ceilings into the meters because a meter carried one
ceiling for a whole panel -- `ceiling_field` removed the need, and the
ceiling now travels with the reading it belongs to.

The overview's **gate panel** is the point of the whole thing: "why has
nothing run since this morning" is the question this unit is asked most,
and until now it needed a log to answer. `GET /gate` reports what the two
gates would decide right now -- and it is a separate read from `tick` on
purpose, so that looking at a page can never start a session.

### Asking it a question

The one panel that causes anything to happen. Submit is `ask_unit`, a
declared tool the console dispatches through the bridge's `POST /route`
like any other tool call; the poll is a `GET`, carried by the same
read-only proxy as every other panel source. No new door.

```bash
curl -X POST localhost:9001/ask -d '{"question":"why has nothing run?"}'
# {"id": "..."}   then: curl localhost:9001/ask/<id>
```

Single-turn: each question carries its own context and knows nothing of
the last. The session is granted **no tools at all** -- sessions this
unit spawns have no way back into it, and this one needs nothing the
prompt does not already carry.

It is a money tap on an origin with no authentication, so: both gates
refuse rather than queue, one question runs at a time for the whole unit,
the question is capped and fenced below instructions it cannot
renegotiate, and the runner gives up after five minutes rather than
holding the lock for good.

The answer is model output derived from task bodies that other agents
wrote. It is rendered as escaped text and must stay that way.

## Verifying it

```bash
pytest                                     # 136 tests, against the real binary
python scripts/tag_sop_lookup.py --validate-all
python scripts/smoke.py --base-url http://127.0.0.1:9001
curl -X POST http://127.0.0.1:9001/trigger # spends a real session
```

`scripts/smoke.py` exercises the HTTP surface a peer sees and writes a
throwaway map — point it at a scratch `--taskdata`.

## Documentation

Detailed internal documentation lives in `docs/` -- architecture, the data
model, the full HTTP reference, control flows traced across modules,
operations, and the register of load-bearing decisions. Start at
`docs/README.md`.

## Modules

| module | owns |
|---|---|
| `records` | the universal record: kinds, runnability, export parsing |
| `taskstore` | the only module that runs `task` |
| `bodies` | one optional markdown blob per uuid |
| `sops` | procedures a task's tags make mandatory |
| `session_types` | which types exist, and what each may reach |
| `runner` | the `claude -p` invocation and its stream |
| `sessions` | local run artifacts, and the `/stats` aggregates |
| `dashboard` | the panel spec the node renders at `/dashboard` |
| `ask` | one question, one session, polled for an answer |
| `logs_client` | best-effort `session_run` entries to whoever stores logs |
| `notify` | best-effort word to the `owner` role when a person is needed |
| `intake` | validating a proposal, and writing it all-or-none |
| `repo_setup` | preparing a target repo, non-destructively |
| `pipeline` | one tick: gate, select, claim, run, record |
| `service` | the operations as plain functions. No HTTP |
| `server` | routing and the tool manifest. The only module that knows HTTP |
