# Data model

## The universal record

One shape, every source. Defined as a frozen dataclass at
`pu/records.py:88`; frozen because a `Task` is an observation of the store
at a moment, not a handle you mutate.

| field | type | Taskwarrior | meaning |
|---|---|---|---|
| `uuid` | `str` | native | **the only identity.** See below. |
| `description` | `str` | native | one line, always present |
| `status` | `str` | native | `pending`, `completed`, `deleted`, `waiting` |
| `kind` | `str \| None` | UDA | what resolving this produces → which session type runs it |
| `project` | `str \| None` | native | the effort; dotted, hierarchical, prefix-matched on read |
| `url` | `str \| None` | UDA | external authority. `None` means this record *is* the authority |
| `parent` | `str \| None` | UDA `parent_uuid` | what it is part of (a map, an epic, anything) |
| `repo` | `str \| None` | UDA | where an execution session must run |
| `tags` | `tuple[str, ...]` | native | each names a mandatory procedure; plus the `afk` marker |
| `depends` | `tuple[str, ...]` | native | blocking; works across sources for free |
| `annotations` | `tuple[str, ...]` | native | timestamped notes — resolutions, guard messages, skips |
| `urgency` | `float` | computed | ordering across every source |
| `entry`, `modified`, `start` | `str \| None` | native | Taskwarrior stamps, `YYYYMMDDTHHMMSSZ` |

Four derived properties, none stored:

| property | is | at |
|---|---|---|
| `afk` | `"afk" in tags` | `pu/records.py:109` |
| `active` | `start is not None` — i.e. claimed | `pu/records.py:114` |
| `session_type` | `SESSION_TYPE_FOR_KIND.get(kind)` | `pu/records.py:121` |
| `runnable` | `afk and session_type is not None` | `pu/records.py:129` |

### Identity is `uuid`, never `id`

Taskwarrior renumbers the short numeric `id` as tasks complete, so an `id`
held across two calls silently addresses a **different task**. Nothing in
this unit may persist or accept an `id`. `TaskStore.get`
(`pu/taskstore.py:284`) takes a uuid and says so in its own docstring.

### Absence is normal

`records.from_export` (`pu/records.py:135`) degrades every optional field
to `None`/`()`. Taskwarrior omits empty attributes entirely rather than
emitting nulls, so absence is the ordinary case, not an error.

The one thing that raises is a **missing uuid** — a record with no
identity is not a record.

One compatibility note in the same function: 2.6 exports `depends` as a
JSON list, older builds as a comma-separated string. Both are accepted.
This is the only field whose export shape has actually moved.

## Kinds — the closed vocabulary

`pu/records.py:46`. **Order is significant**: Taskwarrior uses the order
of `uda.kind.values` as the sort order for the attribute.

```
execution, research, intake, task, prototype, grilling, map
```

`SESSION_TYPE_FOR_KIND` (`pu/records.py:69`) maps only three of them:

| kind | session type | why |
|---|---|---|
| `execution` | `execution` | changing code in a repository |
| `research` | `research` | finding a fact a decision waits on |
| `intake` | `intake` | converting a pushed message into work |
| `task` | — | wayfinder's do-rather-than-decide type. Absent **for now**: its work is too heterogeneous to have earned a tool grant. |
| `prototype` | — | human-in-the-loop by nature. Absent **permanently**. |
| `grilling` | — | human-in-the-loop by nature. Absent **permanently**. |
| `map` | — | a record with children, never itself worked. |

Selection is default-deny: **a kind absent from that map is inert**, not
handed to whichever session type sorts first. Adding a kind without
building a session type for it is safe.

### Two enforcement points, one vocabulary

This is the subtlety most likely to be "cleaned up" by mistake.

- **The `task` command path** is guarded by the binary itself:
  `uda.kind.values` in `RC_SCHEMA` makes it reject an unknown kind with
  exit 2, writing nothing. This is what protects a person or an outside
  session driving the store by hand.
- **The `task import` path is not guarded at all.** Measured: `task
  import` writes whatever JSON it is handed and validates no UDA value.
  Hand it `kind: "nonsense"` and it stores exactly that.

pu creates every task through `import`. So `records.check_kind`
(`pu/records.py:185`) is **the only guard on this unit's own writes** —
not a convenience wrapper around the binary's. There is a test asserting
this; it is not redundant. Both paths read `KINDS`, so there is one
vocabulary and two enforcement points, never two vocabularies.

## Ordering: urgency, not a priority ladder

There is no priority `if` anywhere in this codebase. Ordering across every
source is Taskwarrior's own urgency with a per-kind coefficient, declared
in `RC_SCHEMA` (`pu/taskstore.py:57`):

```
urgency.uda.kind.intake.coefficient    = 6.0
urgency.uda.kind.research.coefficient  = 4.0
urgency.uda.kind.execution.coefficient = 2.0
```

Consequences worth stating explicitly:

- Retuning what pu works on next is a **config change**, not a code change.
- The ordering is **inspectable by hand** — `task next` shows you the same
  order pu will select in.
- Taskwarrior's own urgency terms (age, tags, blocking, `+ACTIVE`) still
  contribute. The coefficients tilt the ranking; they do not replace it.

Selection itself is then one line: take the top of `/queue`
(`pu/pipeline.py:298`).

## Where a long form lives

Exactly one of three places, and which one is a property of the **record**
rather than of its source (`pu/bodies.py`):

| | condition | read by |
|---|---|---|
| **external** | `url` is set | follow the URL; GitHub owns the text |
| **local** | a blob in `state/bodies/<uuid>.md` | `GET /tasks/<uuid>/body` |
| **absent** | neither | one line was enough |

The body store is deliberately dumb: **no history, no versioning**. A
wayfinder map body is rewritten in place every time a decision lands,
which is what the skill expects — the map is an index, and its history is
the closed tickets it points at.

Two properties of the implementation are load-bearing:

- **Keys are validated against a strict uuid pattern** (`pu/bodies.py:32`)
  before being used to build a path. This store is addressed by values
  arriving over HTTP, so a strict pattern is what keeps `../` out of it,
  rather than trusting a caller to have validated first.
- **Writes are atomic and newline-preserving** (`pu/bodies.py:61`):
  written to a temporary sibling and `os.replace`d, so a reader never
  observes a half-written map; and `newline=""` on both read and write, so
  a body posted by one tool and read by another comes back byte for byte.
  Without that, Python rewrites every `\n` to `\r\n` on Windows and a map
  that round-trips through git churns on line endings.

## The Taskwarrior schema

Declared once, in code, at `pu/taskstore.py:57`. There is **no `.taskrc`
on disk that pu reads** — every invocation passes `rc:/dev/null` plus
explicit `rc.<key>=<value>` overrides (`pu/taskstore.py:238`). A config
file would be a second copy of the schema, and two copies drift.

```
confirmation = off        verbose = nothing        hooks = off
uda.kind.type        = string   uda.kind.label        = Kind
uda.kind.values      = execution,research,intake,task,prototype,grilling,map
uda.url.type         = string   uda.url.label         = URL
uda.parent_uuid.type = string   uda.parent_uuid.label = Parent
uda.repo.type        = string   uda.repo.label        = Repo
urgency.uda.kind.{intake|research|execution}.coefficient = 6.0 | 4.0 | 2.0
```

`render_taskrc` (`pu/taskstore.py:168`) renders **the same schema** as a
`.taskrc` for a human — or an outside session — running `task` by hand.
pu never reads that file. It exists so the config you use at the terminal
and the config pu passes cannot disagree, because both come from
`RC_SCHEMA`.

Two differences between the rendered file and the overrides, both
deliberate:

- `verbose=nothing` is **kept**. This file's main consumer is a session
  parsing `task ... export`, and Taskwarrior's chatter lands on stderr,
  where a caller treating stderr as failure aborts on it. Pass
  `rc.verbose=on` for one command to get it back.
- `hooks=off` is **dropped**. pu disables hooks for its own writes, but a
  person's hooks are theirs, and nothing here should switch them off.

## On-disk layout

Two roots, and **they can be on opposite sides of a filesystem boundary**.
Get this wrong and two writers report success while writing to different
stores.

### The Taskwarrior data location — `PU_TASKDATA`

A path the **`task` binary** sees. Under the WSL arrangement that is a
*Linux* path, where `~` is the WSL user's home. Default `~/.pu-taskdata`.

```
~/.pu-taskdata/
  pending.data      completed.data      undo.data      backlog.data
```

Never put this in `/tmp` — WSL wipes it when the distro shuts down.

### This unit's private storage — `PU_STATE_DIR`

A path **this process** sees, so on Windows it is a Windows path. Default
`state/`, gitignored as `state*/` so scratch state directories are ignored
too. Nothing outside this repository may read it.

```
state/
  taskrc                          rendered from RC_SCHEMA at every start,
                                  with newline="\n" — see DECISIONS.md
  repeat_tracker.json             {"uuid": ..., "count": n} — the breaker
  bodies/
    <uuid>.md                     one long-form blob per task
  sessions/
    <task-uuid>/
      <YYYYMMDDTHHMMSSffffffZ>/   one directory per run
        prompt.txt                exactly what the session was given
        stream.jsonl              the raw stream-json, verbatim
        result.json               outcome, cost, duration, tool sequence
```

`sessions/` is **not** the system's session log — those go to the logs
peer as `session_run` entries. What is kept here is the two things that
entry deliberately does not carry (`pu/sessions.py:1`):

- **the raw stream**, because the parsed summary discards tool-call
  arguments, and this is the only on-disk record of what a session that
  died mid-run actually wrote;
- **enough history to compute aggregates**, so `/stats` answers without a
  round-trip that would make this unit's stats depend on a peer being up.

### Written by the gateway, never by pu

Three files land in the repository root before the process starts. All
three are gitignored, read-only to this unit, and **always present,
possibly `{}`** — a consuming unit should never have to tell "no file"
apart from "no policy" (`pu/pipeline.py:80`).

| file | read by | for |
|---|---|---|
| `peers.json` | `pu/logs_client.py:33` | finding the `log_write` peer **by capability, never by name** |
| `cost_policy.json` | `pu/pipeline.py:80` | `daily_cost_cap_usd`, enforced entirely unit-side |
| `delivery_policy.json` | — | not currently read |

## SOPs — procedures a tag makes mandatory

An SOP is a directory `sops/sop-<tag>/SKILL.md`. **The tag is the
directory suffix, so the filesystem is the index** — there is no manifest
to go stale, and `sops.validate` (`pu/sops.py:129`) checks the tree
against itself rather than against a list someone must remember to update.

Distinct from a Claude Code skill, and the distinction is the point:

| | a skill | an SOP |
|---|---|---|
| triggered by | the model's judgement that it is relevant | the task's own tag |
| resolved by | description matching | **exact string match**, no fuzzy search |
| optional | yes | **no** |

Hence `sops/`, not `skills/`.

Selection is a **union**: every tag that resolves contributes a mandatory
procedure, and there is no precedence between them. Nothing ranks or
picks.

An unmatched tag is **not an error** — free-form labelling is useful — but
it is reported (`sops.match`, `pu/sops.py:115`), because a tag that looks
like an SOP and is misspelled means the procedure silently does not apply,
which is a green path with no procedure on it.

**Zero SOPs is a supported state.** The catalogue ships empty; a session
with no resolving tags proceeds with none, and the lookup script says so
plainly.

The frontmatter parser (`pu/sops.py:29`) is deliberately not a YAML
parser: this unit has no dependencies, and the frontmatter it needs is two
or three scalar fields. It handles inline values and folded (`>`) block
scalars, which is what the fields actually use.
