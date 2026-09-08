# Decisions

The register of load-bearing choices: what was measured, what was chosen,
and **what breaks if it is undone**.

Read this before changing anything that looks redundant. Several entries
below exist precisely because the safe-looking simplification is the bug —
a check that duplicates another check, a flag that seems cosmetic, a
second write path that appears to do the same thing as the first.

Entries marked **measured** were verified against the real binary or the
real CLI before being built on. They are not reasoning from documentation.

---

## Guardian invariants

These are the exceptions to this system's everything-degrades rule. Each
raises rather than degrading, because degrading would let bad data into a
queue that agents then act on.

### `-e`, never `--`, when reaching a binary through WSL — **measured**

`wsl.exe -- <cmd>` hands the command to the default login shell, which
expands it. Backticks vanish, `${HOME}` interpolates, and `$(echo PWNED)`
**executes**. `wsl.exe -e <cmd>` execs directly; every byte survives.

Not a style preference: a task's annotations carry a **session's own final
message**, so this is a path from model output to shell execution.

`normalise_base_cmd` (`pu/taskstore.py:114`) rewrites it wherever it comes
from, rather than the code documenting the requirement, because
`PU_TASK_CMD` is config a human sets and a comment cannot stop someone
typing `--`.

**Undo it and:** a session can write shell into a store write and have it
run.

### No multi-line value in a `key:value` argument — **measured**

A `key:value` argument to `task` containing newlines, past a few hundred
bytes, **exits 0, silently discards the value, and overwrites the task's
description.** Success is reported; data is lost.

`_encode_fields` (`pu/taskstore.py:191`) refuses them outright. Nothing in
the codebase does this today; the check exists so the next caller does not
find out by losing data. Long text belongs in an annotation — which is
protected by `--` — or in the body store.

**Undo it and:** a caller loses a field and a description in one write,
silently.

### `check_kind` is the only guard on our own writes — **measured**

`task import` **validates no UDA value.** Hand it `kind: "nonsense"` and
it writes exactly that. The closed `uda.kind.values` list only guards the
`task add`/`modify` command path.

pu creates every task through `import`. So `records.check_kind`
(`pu/records.py:185`) is the real guard here, not a convenience wrapper
around the binary's. **There is a test asserting this. It is not
redundant; do not delete it as such.**

Both paths read `KINDS`, so there is one vocabulary and two enforcement
points — never two vocabularies.

**Undo it and:** a typo creates a task no session type can ever run, which
then sits on the queue.

### `uuid`, never the numeric `id`

Taskwarrior renumbers `id` as tasks complete. An `id` held across two
calls addresses a **different task**. Nothing in this unit may persist or
accept one.

**Undo it and:** the failure is silent and addresses the wrong record.

### Body keys are validated as uuids before building a path

`pu/bodies.py:32`. The body store is addressed by values that arrive over
HTTP, so the path is built from untrusted input. A strict pattern is what
keeps `../` out, rather than trusting a caller to have validated first.

### An execution target with no `.git` is refused

`repo_setup.check_repo` (`pu/repo_setup.py:59`). The deliberate exception
to everything-degrades: an execution session is granted `Write` and
`Edit`, and without version control there is no way to see what it did or
undo it. Degrading here would mean *making an unreviewable change to a
stranger's files*.

---

## The store

### Creation goes through `import`, on stdin, with a uuid we chose

`task add` does not report what it created. Recovering the uuid meant a
follow-up `+LATEST export` — correct only while **nothing else writes**.
Something else does write: a wayfinder session driving the same store
through the `task` CLI can land a write in that window, and we would
return *its* uuid as ours.

Choosing the uuid **removes the race rather than narrowing it**
(`pu/taskstore.py:323`). Because the record travels on stdin it also
escapes the command-line length limit (~30KB on Windows) and every quoting
hazard, so a description carrying newlines, backticks or `$(...)` is
stored verbatim.

This is also why there is **no write lock** in `taskstore.py`. Taskwarrior
does its own file locking, and ours would only ever have protected us from
ourselves — which stopped being the risk the moment a second writer became
real.

### No `.taskrc` that pu reads

Every invocation passes `rc:/dev/null` plus explicit `rc.<key>=<value>`
overrides (`pu/taskstore.py:238`). The schema lives in `RC_SCHEMA`, in
code. A config file on disk would be a second copy, and two copies drift.

`render_taskrc` (`:168`) generates the same schema as a `.taskrc` for a
human or an outside session running `task` by hand. **pu never reads that
file.** One source, two consumers.

### `state/taskrc` is written with `newline="\n"` — **measured**

`pu/main.py:73`. This file is written by whatever platform runs the unit
and read by the binary, which under the WSL arrangement is Linux.

Python would otherwise translate to CRLF, and Taskwarrior reads the
trailing carriage return **as part of the value** — so
`data.location=/tmp/x` silently becomes `/tmp/x\r`, a different directory.
Observed: a session and the unit wrote to different stores, both reporting
success.

The same reasoning makes the body store read and write **bytes**
(`pu/bodies.py`): a body posted by one tool and read by another comes back
byte for byte, and a map that round-trips through git does not churn on
line endings. Bytes rather than the `newline=` keyword there because
`read_text` only accepts it from 3.13 — see §the suite is verified against
the Python the project declares.

### Ordering is urgency coefficients, not a priority ladder

`RC_SCHEMA` carries `urgency.uda.kind.<kind>.coefficient`. With one store
holding every source, "research before execution" is a **coefficient
rather than an `if`** — retunable without a code change, and inspectable
by hand with `task next`.

**Undo it and:** the ordering rule exists in two places, one of which a
person cannot see from the terminal.

### `verbose=nothing` is kept in the rendered taskrc; `hooks=off` is dropped

`pu/taskstore.py:187`. That file's main consumer is a session parsing
`task ... export`, and Taskwarrior's chatter lands on **stderr**, where a
caller treating stderr as failure aborts on it. `rc.verbose=on` restores
it for one command.

`hooks` is dropped deliberately: pu disables them for its own writes, but
a person's hooks are theirs and nothing here should switch them off.

---

## The session layer

### Nothing multi-line ever goes in argv — **measured**

On Windows the Claude CLI is an **npm batch shim**, and a command line
containing a newline is truncated there, silently, at the newline.

Measured: a prompt passed as an argument reached the model as its **first
line only**, *and* took the trailing `--output-format stream-json
--verbose` down with it — so the session both misunderstood its task and
reported in plain text, and the parser got nothing.

Consequences, all in `pu/runner.py`:

- the prompt goes on **stdin** (`-p` with no prompt argument);
- session-type instructions are **folded into the prompt**
  (`pu/pipeline.py:361`) rather than passed as `--append-system-prompt`,
  which would have exactly the same problem;
- argv holds only short single-line flags, which is the shape that
  survives every launcher.

**Undo it and:** the failure is silent, plausible-looking, and costs a
real session every time.

### `--output-format stream-json --verbose` is not optional

Two reasons, both hard: the CLI **refuses** `--print --output-format
stream-json` without `--verbose`; and the stream is the only place cost,
duration, the ordered tool-call sequence and permission denials are
reported at all.

### `--allowedTools` is not optional either

Without it, every tool call sits behind an interactive permission prompt
and **there is nobody present to answer one**. A headless session with no
grant does not fail loudly — it stalls.

### `claude` is resolved with `shutil.which`

`pu/runner.py:39`. On Windows the CLI is an npm shim: the real file is
`claude.cmd`, and `CreateProcess` cannot run a bare extensionless name — a
plain `["claude", ...]` fails with "the system cannot find the file
specified". `shutil.which` applies `PATHEXT` and returns the name that
will actually start. `PU_CLAUDE_CMD` overrides, for the same reason
`PU_TASK_CMD` exists.

### `exit_code` always comes from the real process

Never inferred from the stream's own `is_error` (`pu/runner.py:74`). A
crash before any valid JSON is emitted must still produce a real non-zero
code with everything else absent.

### `tool_calls` is the ordered sequence, not counts

A consumer derives counts in one line. Aggregating here would permanently
lose the order, which is the question the field exists to answer.

### A session type is a directory with a `CLAUDE.md`; the grant is in code

Discovery is a filesystem walk (`pu/session_types.py:102`), because a
sibling unit's architecture audit found session-type identity defined in
**four separate places, already silently drifted**. One definition derived
from the tree cannot do that.

The **tool grant** stays in code (`ALLOWED_TOOLS`, `:51`) precisely
because it is a security decision and should be visible in code review
rather than inferred from a file someone dropped in. Each grant is built
from what that type's `CLAUDE.md` actually instructs, starting from
nothing.

### Both `python` and `python3` are granted for the lookup script

`pu/session_types.py:41`. `python3` is right on Linux and absent on
Windows. A grant naming only one silently fails on the other platform —
and a session with no way to resolve a mandatory procedure does not fail
loudly, it quietly ignores one.

### Execution sessions run in the target repository

So that repo's own `CLAUDE.md`, conventions and gate hooks apply. A
per-repo verification gate is the strongest safety property available and
it is wired there, not here.

The cost is accepted: the session type's own `CLAUDE.md` no longer loads
from cwd, so it is read and folded in; and its skills are copied in
non-destructively (`FLOWS.md` §4).

### Execution skills are project-scoped, and only `ponytail` ships

An execution session's cwd is the target repository, so a skill living in
this unit's tree **does not load at all** — and the failure is silent, the
session just builds without it. The only route is
`session_types/execution/.claude/skills/`, copied in by
`repo_setup.prepare` on every tick.

`ponytail` is what ships: an execution session has `Bash`, `Read`, `Write`
and `Edit` in a repository it does not own, working from a one line task
with nobody watching, and the two things that costs are building more than
was asked and shrinking a change before understanding it.

**One of upstream's six, not all six.** `ponytail-audit`, `-debt`,
`-gain`, `-help` and `-review` are user-invoked one-shot commands an AFK
session never calls — copying them into someone else's repository would be
five files of permanent dead weight.

The skill is vendored byte-identical with its `LICENSE` **inside the skill
directory**, so the notice travels with every copy, which is what MIT
asks. The reasoning lives in `session_types/execution/.claude/NOTICE.md`,
deliberately *outside* `skills/` so it does not travel — a repository pu
works in has no business knowing this unit exists.

### The `Skill` tool is not permission-gated — **measured**

A session run with `--allowedTools "Bash,Read,Write,Edit"` invoked a
project skill and returned `tool_calls=('Skill',)` with **no permission
denial and no stall**. Repeated with `Skill` added to the grant: identical
result.

Recorded because the opposite would have been the expected failure — a
headless session with no grant for a tool does not fail loudly, it stalls
(see §`--allowedTools` above). `ALLOWED_TOOLS` in `pu/session_types.py:51`
therefore needs no entry for `Skill`, and adding one would be cargo.

### Peer tools are a per-type grant, not a deployment setting

`PU_MCP_BRIDGE_URL` says where the gateway's MCP bridge is;
`session_types.REACHES_PEERS` says who may use it. Both are required, and
they are deliberately different kinds of thing.

Passing a session a bridge URL adds `mcp__mcp-bridge__*` to its grant —
**every tool every peer exposes**, a surface far wider than anything in
`ALLOWED_TOOLS` and one that grows whenever somebody registers a new unit.
That is a security decision, so it lives in code beside the other grants
where it gets reviewed, exactly like `RUNS_IN_TARGET_REPO`.

`intake` is absent from the set. Its grant is `Read` plus the lookup
script, narrow because it is the one session that causes work to exist;
widening it to the whole system's tool surface must not be something a
deployment can do by filling in an environment variable.

### The owner is addressed by role, through the bridge

`notify.py` posts to the bridge's `/route` with `to: "owner"`. It never
names a unit — which unit serves that role, and which thread within it, is
resolved at the bridge from `delivery_policy.json`. So pu can tell a person
something without knowing a communication unit exists, let alone which one
is installed. Same principle as `logs_client` finding the logs peer by
capability.

**This is the one place pu deliberately does not go direct.** The
standard's default is unit-to-unit HTTP to a peer's `base_url`, and
`logs_client` follows it precisely so that recording a session never
depends on a third unit. Here there is no `base_url` for "the owner" —
role addressing is what `/route` exists for, so involving the bridge is the
documented case rather than an exception to encapsulation.

The cost is accepted and stated: a notification depends on the bridge being
up. Which is why every outcome is `False` and never an exception — no
bridge, an unreachable one, an unconfigured owner, a tool the target does
not have. An unconfigured owner is a *valid* state; a system can genuinely
have no owner. A notification that cannot be delivered must never turn a
tick that did its work into a failure.

`delivery_policy.json` is therefore still never read in this repo, and now
by design rather than by omission.

### The notify tool name is config, because pu cannot know it

`/route` requires a `tool`, and which unit sits behind `owner` is the
deployment's choice — so `PU_NOTIFY_TOOL` names it, defaulting to
`send_message`. Same reason `PU_TASK_CMD` exists: what a thing is called is
not an assumption to bake into code. A wrong name is safe and visible
rather than silent: the bridge answers `delivered: false` with a reason.

### Two things get a person told: a block, and a bounce

Both were previously silent. The breaker taking a task away from agents
*means* "this needs a person", and saying so only on the task itself made
that true and unheard. A bounce is a finished answer that only pu had
heard — the record closed and whoever sent the message never learned what
was missing.

Nothing else notifies. A single failed session does not: the breaker
already covers repeated failure, and a message per failure is how a
notification channel becomes something a person mutes.

### Context is assembled going in, and the result parsed coming out

Deterministic retrieval before the run, rather than handing an agent a
workspace and hoping. What the session was given is visible in
`prompt.txt` afterwards, identical on a retry, and does not spend the
session's budget on finding its own inputs.

The corollary is the load-bearing half: **sessions have no route back into
this unit**, which is what makes intake safe.

---

## Scope and shape

### One store, one record, every source

Every apparent difference between sources turned out to be a **field, not
a partition**. Holding them together is what removes the priority ladder,
what makes blocking work across sources for free, and what collapsed the
inbox into an ordinary queue entry.

### Two typed write doors, no generic CRUD

**The shape of the write is the permission.** There is no endpoint that
accepts an arbitrary task. `ARCHITECTURE.md` §the two write doors.

### The frontier is not the queue

A person working a map must see the grilling ticket that is next; pu must
never select one. Two queries, and runnability is a **conjunction** — an
explicit `afk` *and* a kind with a session type.

**Merge them and:** a session picks up a conversation only a person can
have, and produces nothing.

### `afk` defaults to false

Default-deny, not a filter applied later. A ticket type nobody has thought
about cannot become agent-runnable by accident.

### No pu CLI

A session outside this unit uses `task` for the task graph and two `curl`
calls for long-form bodies. **`task` for structure, HTTP for prose.** A
CLI would be a third interface to keep in step with the other two.

### No Taskwarrior hook guarding `+afk`

A speed bump that the sessions it targets must routinely bypass is
friction, not protection. The escalation path predates the CLI decision
anyway: the HTTP API already accepts `afk: true` on an unauthenticated
loopback.

### The body store stays, over annotations

A map body is **rewritten in place** as decisions land and fog graduates,
which annotations cannot express. Cost accepted and stated: the map is
unreadable when pu is stopped. Tickets are not.

### No history and no versioning in the body store

A map is an index; its history is the closed tickets it points at.

### `resolve` does not touch the parent map

Choosing the one-line gist for Decisions-so-far is **judgement**, and
belongs to the caller. pu records the answer and closes the ticket.
Mechanical here, judgement there.

### Intake proposes; `intake.py` writes, all or none

A session cannot write a task pu did not approve — a much stronger
property than trusting it to call the right endpoint. Rollback is a
compensating delete, since Taskwarrior has no transaction; a deleted task
stays visible under `status:deleted`, so a failed intake leaves a readable
trace rather than a silent gap.

### `traces_to` is required on every proposed task

The mechanical half of *intake transcribes, it does not invent*. A task
with nothing to trace to is one the session made up.

### The cost gate runs before selection, inside the tick

A gate that runs after selection can be skipped by whatever selected
differently, and a gate an external trigger can route around is not a
gate. Enforcement is **entirely unit-side** — the gateway carries the
policy verbatim and never reads it.

### A second tick declines rather than queueing

Two sessions at once is not a busier unit, it is two sessions racing for
the same top-of-queue task. A queued poke would fire into a queue that has
moved on.

### Blocking a task means removing `afk`, not inventing a status

Taskwarrior's statuses are a closed set, and this is the honest encoding
anyway: the task stays open, stays on the frontier a human reads, and
simply stops being something an agent may take.

### `/stats` reports numbers, never verdicts

Cost per session type, failure counts, durations — yes. "The queue is
unhealthy" — never. That is an analytical unit's call, and computing it
here would be pu grading its own work.

### Log peers are addressed by capability, never by name

`pu/logs_client.py:28`. A literal unit name in code makes that unit
un-renameable and un-swappable, which is the opposite of what a holonic
system is for. And the call goes **direct, not through the bridge** — a
unit's ability to record what it did must not depend on a third unit being
up.

### SOPs are `sops/`, not `skills/`

A skill is optional and judgement-triggered; the model decides it is
relevant. An SOP is **required by the task's own tag**, resolved by exact
string match, no fuzzy search and no agent discretion. Selection is a
union with no precedence. Zero SOPs is a supported state.

### The tag is the directory suffix

The filesystem is the index. There is no manifest to go stale, and
`validate()` checks the tree against itself rather than against a list
someone has to remember to update.

### The four standard endpoints are reimplemented per unit

Small duplication, deliberate, and a standing decision across the system.
**Do not factor it out.** Revisit only when a third new unit makes the
copy-paste cost concrete.

### `TOOLS` is hand-written, never generated

Every entry needs an LLM-facing description. Names are action-style rather
than path echoes, and one route under two methods is two tools.

### The suite runs against the real binary

A fake that also builds the argv tests nothing. This exact gap let **six
launch bugs survive a fully green suite** in a sibling unit, so CI
installs Taskwarrior and the seam is the subprocess, never the argv
construction.

### The dev dependency is declared twice, on purpose

`uv sync` reads `[dependency-groups]` and ignores extras; `pip install -e
".[dev]"` reads the extra. With only one, the other toolchain silently
produces an environment with no pytest in it. **They must stay in step.**

### Skills install globally, not per repo

One queue and one set of efforts means one copy. Per-repo copies would be
N copies of one fact, and they would drift. `--repo` exists for a
repository that genuinely needs its own, not as the normal path.

### Vendored skills stay byte-identical

`wayfinder` and `grilling` are copied verbatim (sha256-verified) from
upstream. Anything changed there is something that has to be re-merged
every time upstream moves. Forks carry a note at the bottom saying exactly
what changed and why — see `skills/NOTICE.md`.

### ADRs dropped from `domain-modeling`

A resolved ticket already carries the reasoning, and a second decision log
drifts.

### The glossary stays out of the spec graph

The spec unit holds claims, not definitions. `CONTEXT.md` lives in the
project repository.

---

### The suite is verified against the Python the project declares

`Path.read_text(newline=...)` is 3.13+. `pyproject` declares `>=3.10`, the
dev machine runs 3.14, and CI ran 3.11 — so `bodies.get` raised `TypeError`
on every CI run since the repo was created, and the suite could not have
passed there. It also killed the request-handler thread mid-response, which
surfaced as nine `RemoteDisconnected` failures in `test_server` looking
like an unrelated fault.

Two consequences, both kept:

- **The body store reads and writes bytes**, which means the same thing on
  every version.
- **CI runs the version `pyproject` declares as the minimum**, not a newer
  one that happens to work. A floor that is never tested is not a floor.

## Environment traps, for whoever is debugging

Not decisions, but the same category of thing: measured once, expensive to
rediscover.

- **WSL cold start is ~9.4s**, against ~0.37s warm. The store is warmed in
  a daemon thread at boot (`pu/taskstore.py:246`).
- **Git Bash rewrites `/tmp/...` arguments** into Windows paths before the
  process sees them. Drive pu from PowerShell, or `MSYS_NO_PATHCONV=1`.
- **Never put task data in `/tmp`** — WSL wipes it when the distro shuts
  down.
- **`dcg`** (the local destructive-command guard) blocks `rm -rf`,
  `shutil.rmtree`, `git checkout --`, and heredocs containing backticks or
  `$(...)`. Write commit messages to a file and use `git commit -F`.
- **Do not install anything into a unit.** `setup-project` refuses when it
  sees `UNIT_CONTRACT.md`. Learned by doing it to a sibling unit and
  reverting.
