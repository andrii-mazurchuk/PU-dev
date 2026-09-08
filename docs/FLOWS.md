# Control flows

Five paths traced end to end across modules. Each module's own docstring
explains its local reasoning; this file is the part no single docstring
can show you.

---

## 1. A tick

The unit of work. Entered from `POST /trigger` (`pu/server.py:397`) or
from `main.tick` (`pu/main.py:48`). Everything below is `pu/pipeline.py`.

### Step 0 — one tick at a time (`:265`)

```python
if not _TICK_LOCK.acquire(blocking=False):
    return TickResult(ran=False, reason="a tick is already running")
```

A process-wide `threading.Lock`, taken **non-blocking**. The server is
threaded and the gateway pokes on a schedule, so a slow session and the
next poke overlap sooner or later.

A second tick **declines rather than queueing**. Two sessions running at
once is not a busier unit, it is two sessions racing for the same
top-of-queue task; and a queued poke would fire into a queue that has
moved on since.

### Step 1 — collect stale claims (`:286`)

`guards.release_stale_claims` (`pu/guards.py:115`) runs **before anything
reads the queue** — a claim left by a process that died is otherwise
invisible to every future tick, because `+ACTIVE` removes the task from
`ready()`.

For each `+ACTIVE status:pending` task: parse `start`
(`YYYYMMDDTHHMMSSZ`), and if it is older than two hours, `store.release`
it and annotate why. An unparseable stamp leaves the claim alone —
reaping on a value we did not understand would be worse.

Run here rather than on a timer: no thread, deterministic to test, and the
only moment it matters is just before selection anyway.

### Step 2 — the cost gate (`:288`)

```python
policy  = read_cost_policy(cost_policy_path)     # {} if missing/unparseable
blocked = check_cost_gate(policy, session_store, moment.strftime("%Y-%m-%d"))
if blocked: return TickResult(ran=False, reason=blocked)
```

`daily_cost_cap_usd` is compared against `session_store.cost_since(today)`
(`pu/sessions.py:97`), which sums `cost_usd` across every `result.json`
whose `recorded_at` starts with today's date.

**Enforcement is entirely unit-side.** The gateway carries the policy
verbatim and never reads it — if this function does not enforce it,
nothing does. And it runs **before selection, on every path into the
tick**: a gate that runs after selection can be skipped by whatever
selected differently.

### Step 3 — discover session types (`:294`)

A filesystem walk of `session_types/` (`pu/session_types.py:102`). A
missing directory ends the tick with a reason rather than raising.

### Step 4 — select (`:298`)

```python
for candidate in store.runnable():
```

`store.runnable` (`pu/taskstore.py:304`) is `ready()` filtered by
`Task.runnable`. `ready()` is `task +READY -ACTIVE export`, sorted by
urgency descending.

The whole selection rule is *take the top*. There is no priority ladder,
because ordering lives in Taskwarrior urgency (`DATA_MODEL.md`
§ordering) and eligibility lives in `Task.runnable`.

The narrowing is done in Python, not as a Taskwarrior filter string,
deliberately: expressing "has a kind with a session type" as a filter
would be a second copy of `SESSION_TYPE_FOR_KIND` that drifts the first
time a kind is added.

A candidate whose session type has **no directory** is skipped and the
loop continues (`:302`).

### Step 5 — the repeat-dispatch breaker (`:308`)

```python
seen = guards.note_selection(tracker_path, candidate.uuid)
if seen >= repeat_threshold:      # 3
    guards.block(store, candidate.uuid, "selected N ticks running without resolving…")
```

Counted **before any work**, so a session that fails even to launch counts
too. A *different* task winning resets the count — the question is whether
*this* task is stuck, not how busy the queue has been.

Winning selection repeatedly means it is not resolving, because a resolved
task is closed and cannot win again.

`guards.block` (`pu/guards.py:90`) annotates the reason and **removes the
`afk` tag**. It does not delete, close, or invent a status: the task stays
open and stays on the frontier a person reads. "This needs a person" is
exactly what the missing tag means.

### Step 6 — prepare the working directory (`:322`)

Two cases, from `stype.runs_in_target_repo` (`pu/session_types.py:66`):

**Ordinary types** (`intake`, `research`): `cwd = stype.dir`. Claude Code
loads that directory's `CLAUDE.md` natively. `instructions = ""`.

**`execution`**: needs `candidate.repo`. Without one, the task is
annotated `"skipped: an execution task needs a repo to run in"` and the
loop continues. With one, `repo_setup.prepare` runs (flow 4 below); a
`RepoError` annotates the reason and continues. Then `cwd` is the repo,
and because that repo's `CLAUDE.md` now loads instead of the session
type's, `stype.instructions()` is read explicitly and folded into the
prompt at `:361`.

### Step 7 — claim, then run (`_run`, `:352`)

```python
store.claim(task.uuid)                                    # +ACTIVE, first write
prompt = build_prompt(task, body_store.get(task.uuid), unit_root, extra)
result = session_runner(prompt=…, cwd=…, allowed_tools=…, model=…)
```

**Claim before work**, for the same reason wayfinder makes it a session's
first write: the store's own `+ACTIVE` state removes the task from every
concurrent frontier.

`build_prompt` (`:117`) assembles everything the session gets — title,
kind, project, `url`, `repo`, the resolved mandatory procedures with the
lookup command to run for each, unmatched free-form tags called out
separately, the body, the existing annotations, and (for intake only) the
SOP catalogue via `intake_context` (`:177`).

This is **deterministic retrieval before the run**, not a workspace handed
to an agent. What the session was given is visible in `prompt.txt`
afterwards, identical on a retry, and does not spend the session's budget
on finding its own inputs.

The runner (`pu/runner.py:205`) builds argv (`:113`), runs
`claude -p --output-format stream-json --verbose` with **the prompt on
stdin**, and folds the stream (`:148`) into a `SessionResult`.

`exit_code` always comes from the real process, never inferred from the
stream's own `is_error` — a crash before any valid JSON must still produce
a real non-zero code. Every other field degrades to `None`: a process
killed mid-stream is missing data, not an error.

### Step 8 — record whatever happened (`:370`–`:395`)

| what happened | task ends up | outcome |
|---|---|---|
| the launch raised | released + annotated `session failed to launch: …` | `launch_failed` |
| `not result.ok` | released + annotated `session failed (exit N)` | `failed` |
| intake type | see flow 2 | `converted` / `bounced` / `invalid_proposal` |
| any other type | annotated with the final message, **completed** | `resolved` |
| any other type, empty answer | released + annotated `session produced no answer` | `empty` |

A session that failed leaves the task **released and annotated**, never
silently claimed forever. That is what makes the repeat breaker necessary
— and having both is the design, not redundancy.

Then two records are written, in this order:

1. `session_store.record` (`pu/sessions.py:41`) — `prompt.txt`,
   `stream.jsonl`, `result.json` under
   `state/sessions/<task-uuid>/<stamp>/`. **Best-effort**: a full disk
   must not turn a session that actually did its work into a failure, so
   an `OSError` is swallowed.
2. `logs_client.record_session_run` (`pu/logs_client.py:52`) — one
   `session_run` entry to whichever peer declares `log_write`. Also
   best-effort, and degrading is the *normal* case before registration:
   no `peers.json` → no peer → `False`, silently. Nothing is retried and
   nothing is buffered.

---

## 2. An inbox message becomes work

The path that carries the unit's central invariant, so it is worth reading
in full.

```
  peer ──POST /inbox──▶ service.push_inbox ──▶ ONE kind:intake record
                                                (+afk, message → body)
                                                        │
                            a later tick selects it ────┘
                                                        │
                            intake session runs ────────┘
                                     │  returns TEXT. no write access.
                                     ▼
                        intake.extract_block ──▶ intake.validate
                                                  │        │
                                        {"bounce"}│        │{"tasks": [...]}
                                                  ▼        ▼
                                    annotate + close   intake.apply
                                                       all-or-none
```

### The endpoint cannot create work (`pu/service.py:262`)

`push_inbox` mints one record and nothing else: `kind:intake`, tagged
`afk`, description `intake from <source>: <first line>`, the full message
written to the body store. `202`.

There is no argument it accepts that would produce anything else.

### The session proposes; `intake.py` writes

The session (`session_types/intake/CLAUDE.md`) is granted `Read` and the
SOP lookup script. **It has no write access to the store at all.** Its
final message is the only thing that leaves it.

`extract_block` (`pu/intake.py:46`) finds the JSON: fenced `` ```json ``
blocks first, in order, then the outermost braces as a fallback — a model
asked for JSON will often wrap it in a sentence. A truncated final fence
is tolerated. A missing or unparseable block **raises**; guessing at what
was meant is exactly the coercion this design refuses.

`validate` (`pu/intake.py:84`) checks the whole proposal **before any of
it is written**, and raises rather than coercing to a default. A silent
default here would mean a task nobody asked for, of a kind nobody chose,
entering a queue an agent then acts on. Per task it requires:

- `title`, non-empty
- `kind`, through `records.check_kind` — the closed vocabulary
- `traces_to`, non-empty — the sentence in the message this task came
  from. This is the mechanical half of *intake transcribes, it does not
  invent*: a task with nothing to trace to is one the session made up.
- `tags`, a list of strings if present

Or a `bounce`, whose `condition` must be one of four
(`pu/intake.py:33`) and which must carry a reason:

| condition | means |
|---|---|
| `no_stateable_outcome` | never says what would be true once done |
| `no_placeable_target` | no project or repo it belongs to |
| `needs_a_map` | resolving it means choosing between options |
| `material_ambiguity` | two readings, two different pieces of work |

A bounce is a **normal, correct outcome** — the message was read, and the
answer is that it cannot become work as written.

`apply` (`pu/intake.py:147`) creates the tasks, annotating each with
`from intake, traces to: …`. **All or none**: if a write fails partway,
every task already created is deleted in a compensating pass. Taskwarrior
has no transaction, and a deleted task remains visible under
`status:deleted`, so a failed intake leaves a readable trace rather than a
silent gap.

### Why this is stronger than trusting the session

The session never calls an endpoint. It returns a shape, validated against
a closed vocabulary by code it cannot reach. **A session cannot write a
task pu did not approve** — which is a different and much stronger
property than trusting it to call the right endpoint with the right
arguments.

The judgement that remains — what the message meant, how to split it,
whether an agent may do it alone — lives in the session's own `CLAUDE.md`,
where a human can read and edit it. Nothing in `intake.py` decides
anything; it validates a shape and performs writes.

---

## 3. The wayfinder loop, driven from outside

A `/wayfinder` session is user-invoked and human-in-the-loop, so it can
never be hosted by pu. It runs in an ordinary Claude Code session
somewhere else and still has to write here.

There is deliberately **no pu CLI**. It uses two things:

- **the `task` binary** for the task graph — structure, filters, ordering
- **two `curl` calls** for long-form bodies — prose

```bash
# read the frontier for a map, by hand, in the same order pu would select
task rc:/dev/null rc.data.location=$PU_TASKDATA \
     +READY -ACTIVE parent_uuid:<map-uuid> export

# load a map or a ticket question — raw markdown, no jq needed
curl -s http://127.0.0.1:9001/tasks/<uuid>/body > map.md

# rewrite the map after a decision lands
curl -X POST --data-binary @map.md \
     -H "Content-Type: text/markdown" \
     http://127.0.0.1:9001/tasks/<uuid>/body
```

For the outside session to use bare `task`, the UDAs must be declared —
otherwise `kind:` and `parent_uuid:` are simply rejected for anyone but
pu, which passes them as `rc.` overrides. So `main.py:63` renders
`state/taskrc` from the same `RC_SCHEMA` at every start. One source, two
consumers, no drift.

The loop in practice:

1. A person charts a map. `POST /maps` with the map markdown as body.
2. Tickets are created as children. `POST /tickets`, each with the
   question as its body, `afk` set only where an agent can finish it
   alone.
3. Blocking is wired in a **second pass** — `POST /tasks/<uuid>/blocking`
   — because tickets need uuids before they can reference each other.
4. The person reads `/frontier`, which shows them the grilling ticket that
   is next. pu reads `/queue`, which never will.
5. pu ticks, resolves the `+afk` research ticket, annotates the answer and
   closes it.
6. The person reads the answer (`task <uuid> info`, or `GET
   /tasks/<uuid>`), decides the one-line gist, and rewrites the map's
   Decisions-so-far with `set_body`.

Step 6 is where `resolve` stops on purpose (`pu/service.py:234`).
Choosing that gist is judgement. pu records the answer and closes the
ticket; a caller with judgement rewrites the map. Mechanical here,
judgement there.

---

## 4. An execution session in a target repository

Everything in `pu/repo_setup.py`, called from `pu/pipeline.py:330`.

### `check_repo` (`:59`) — the one condition that raises

A missing directory, or a missing `.git`, raises `RepoError`. Not
degraded, and this is the deliberate exception to this unit's
everything-degrades rule: an execution session is granted `Write` and
`Edit`, and without version control there is no way to see what it did or
undo it. "Degrade gracefully" would here mean "make an unreviewable change
to a stranger's files".

### `prepare` (`:74`) — copy in, as a guest

`session_types/execution/.claude/skills/` is copied to
`<repo>/.claude/skills/`, because cwd is now the repo and those skills no
longer load from it. **This is the only way an execution session gets a
skill at all**, and the failure mode if it is skipped is silent: the
session simply builds without it.

What ships today is `ponytail` — vendored byte-identical, with its MIT
`LICENSE` beside it inside the skill directory so the notice travels with
every copy. `session_types/execution/.claude/NOTICE.md` records why, and
sits deliberately *outside* `skills/` so it does not travel: a repository
pu works in has no business knowing this unit exists.

Two rules, both about not being destructive in someone else's repository:

- **Never overwrite.** A file that already exists at the destination is
  left exactly as it is and reported as skipped. The repo's own version of
  anything always wins.
- **Never merge settings.** `.claude/settings.json` is the repo's own
  security configuration. If one exists it is left alone and reported. If
  none exists, **none is created either** — an absent settings file means
  the repo's defaults, and inventing one would be the same act of changing
  what a repo permits without anyone reviewing the change.

An `OSError` during a copy is reported as skipped, not raised.

### Then the session runs there

That repo's `CLAUDE.md`, conventions, hooks and gate apply, and they take
precedence over the session type's own instructions — which is the entire
point of running there. A per-repo verification gate is the strongest
safety property available, and it is wired in the repo, not here.

`session_types/execution/CLAUDE.md` says so to the session in as many
words: *they are the local law and this file is a guest.*

---

## 5. Both guards firing

### The breaker, over three ticks

```
tick 1   task X wins selection      tracker {X, 1}   session runs, fails
         → released + annotated, back at the top of the queue
tick 2   task X wins selection      tracker {X, 2}   session runs, fails
tick 3   task X wins selection      tracker {X, 3}   3 >= 3
         → annotate "auto-blocked: selected 3 ticks running without
            resolving; a person needs to look at this"
         → remove the `afk` tag
         → clear the tracker
         → TickResult(ran=False, outcome="auto_blocked")
```

The task is now open, visible on `/frontier`, absent from `/queue`, and
carries its own explanation. No session was spent on the third attempt.

Three is the threshold (`pu/guards.py:41`): two is too eager — a transient
failure deserves one retry — and much more than three is a more expensive
way to reach the same conclusion.

The tracker (`state/repeat_tracker.json`) degrades to "nothing seen yet"
on a missing or corrupt file. Losing the count makes the breaker slower to
fire, which is survivable; raising would take down a tick over
bookkeeping.

*This guard exists because of a real incident in a sibling unit: one task
won selection eight times and produced eight no-op sessions before anybody
noticed.*

### The reaper, after a crash

```
a session is claimed (+ACTIVE, start=20260908T090000Z), then the process dies
  → the claim survives, because it is store state, not memory
  → ready() filters `-ACTIVE`, so the task never appears on the frontier
     or the queue again, for anyone
next tick, 2h+ later
  → release_stale_claims sees it, `task <uuid> stop`
  → annotates "claim released: held 7412s with no result, so the session
     holding it is gone"
  → the task returns to the frontier and the queue
```

The claim being store state is what makes it survive a crash, and
therefore what makes it need collecting. Both halves of that are the same
decision.
