# Handoff — 2026-09-08 — pu built, and wired to wayfinder

> Previous handoff: `C:\agents\units\mu-spec\.claude\handoff\2026-09-07-live-run-and-the-wayfinder-pivot.md`
> — that session ended with mu-spec finished as a pure tool unit and the
> pipeline pivoted to wayfinder. **The trail continues here**: this session
> built the processing unit that pivot needed. Nothing further will be
> written into mu-spec's handoff chain.

## Session summary

Started from nothing and built **pu**, the processing unit, in a new repo
at `C:\agents\units\pu`. It holds the task queue and is the only unit that
launches `claude -p` sessions. Twelve commits, **128 tests**, all running
against the real Taskwarrior binary rather than a fake.

It replaces the old placeholder `lainiwakuraagent-lgtm/pu-unit`, which was
read once as reference and is now dead to us.

The design settled through several rounds of the user pushing back, and
the pushbacks were right every time: a per-source partition collapsed into
one universal record; a proposed pu CLI was dropped in favour of the
Taskwarrior CLI plus two curl calls; a security "speed bump" was rejected
as friction the sessions it targeted would routinely bypass.

## What's done

**The store and the record.** One Taskwarrior store holding every source —
wayfinder maps and tickets, mirrored issues, inbox requests — as one
record shape. Differences are fields (`kind`, `project`, `parent_uuid`,
`url`, `repo`, tags), not separate stores. Ordering across sources is
Taskwarrior urgency with a per-kind coefficient, so there is no priority
ladder in code.

**Two typed write doors** over HTTP: the wayfinding operations, and
`/inbox`, which can only ever mint one `intake` record.

**The session layer.** A session type is a directory holding a
`CLAUDE.md`; three exist (`intake`, `research`, `execution`). Sessions get
context assembled going in and their result parsed coming out — they have
no write access back into the unit. Intake returns a *proposal*, validated
against the closed vocabulary and written all-or-none.

**Guards.** A repeat-dispatch breaker (two attempts, then the task stops
being agent-runnable and says why), a stale-claim reaper, and a
non-blocking tick lock.

**Proven live, twice.** A real intake session converted a message into two
tasks — one `+afk` execution task and one non-`afk` review task for the
human — splitting the work unprompted, for $0.254. Then a full wayfinder
loop driven only through `task` and `curl`: map charted, tickets created,
blocking wired, pu resolved the AFK research ticket for $0.607, and the
answer was readable with `task` alone.

**Seven skills installed globally** to `~/.claude/skills/`: `wayfinder`
and `grilling` vendored byte-identical (sha256-verified), `research`,
`prototype` and `domain-modeling` forked and adapted, plus our own
`pu-tasks` and `setup-project`.

## Current state / open threads

**pu has no git remote.** This blocks registration: `units.yaml` requires
`source.git_url`. Creating the GitHub repo is the first sub-step of the
next task, not an afterthought.

**Nothing is registered with the gateway.** No `peers.json` exists, so
`logs_client` finds no peer with `log_write` and **every session log is
being silently dropped**. It degrades correctly, which is exactly why
nobody would notice.

**No MCP bridge.** A session cannot reach mu-spec's tools. This was
deliberately deferred until registration.

**No GitHub mirror.** `url` and `repo` fields exist and nothing populates
them, so the source that feeds `execution` is not connected.

**The full loop has never run with the real skills.** Every piece is
proven individually, but the user invoking `/wayfinder` → it calling
grilling and domain-modeling → tickets landing in the queue → pu resolving
the AFK ones has not happened once. It needs a real project directory, not
a unit.

**A scratch directory needs deleting**: `C:\agents\units\pu\C\uf03a…`, four
Taskwarrior data files from a smoke run. Git-ignored and harmless; the
destructive-command guard blocks the agent from removing it.

## ▶ Next step

**1. Give pu a remote.** `gh repo create` under the user's own account.
Registration cannot proceed without it.

**2. Register with the gateway.** Add the `units.yaml` entry — port 9001,
`unit_type: processing`, `lifecycle: persistent`, `capabilities`,
`prompts`, `env` carrying `PU_TASK_CMD="wsl -d Ubuntu -e task"` and
`PU_TASKDATA`. Retire the old `pu-unit` entry, which currently owns 9001.
Then `holonic start` and confirm `peers.json`, `cost_policy.json` and
`delivery_policy.json` appear in the repo.

**3. Prove session logs land.** Run one real tick and check the logs unit
actually received a `session_run` entry. Until this passes, the logging
path is unverified in both directions.

**4. Then the MCP bridge**, so sessions reach mu-spec via its `/tools`.

## Files & references to read

- `C:\agents\units\pu\README.md` — the design in one page, including the
  two-interface rule and the platform arrangement.
- `C:\agents\units\pu\.claude\plans\phase-4-session-layer.md` — the agreed
  plan for the session layer, as built.
- `pu\pu\taskstore.py` — the only module that runs `task`. Its header
  documents three measured decisions and why each was forced.
- `pu\pu\logs_client.py` — what registration needs to make work: finds the
  logs peer **by capability, never by name**.
- `pu\skills\NOTICE.md` — which skills are ours, vendored, or forked, and
  what each fork changed.
- `pu\skills\pu-tasks\SKILL.md` — the tracker doc wayfinder looks for.
- `C:\agents\holonic-node\docs\UNIT_STANDARDS.md` — the registration
  contract; `units.yaml.example` has a processing-unit entry to copy.

## Gotchas / warnings

- **Nothing multi-line may go in argv.** On Windows the Claude CLI is an
  npm batch shim that truncates a command line at the first newline —
  silently, taking every flag after it. Measured: a prompt reached the
  model as line one only *and* the output-format flags were lost. Prompts
  go on stdin.
- **`wsl -- cmd` runs through a login shell**; `$(...)` in a task
  description *executed*. `normalise_base_cmd` forces `-e`. Never undo it.
- **A multi-line `key:value`** to `task` exits 0, discards the value, and
  overwrites the description. `_encode_fields` refuses them.
- **`task import` validates no UDA value.** `records.check_kind` is the
  only guard on our own writes. There is a test saying so; do not delete
  it as redundant.
- **Files written on Windows and read by the Linux binary need
  `newline="\n"`.** CRLF made Taskwarrior read `data.location=/tmp/x` as
  `/tmp/x\r`, so a session and the unit wrote to different stores, both
  reporting success.
- **Never put task data in `/tmp`** — WSL wipes it when the distro shuts
  down. Default `~/.pu-taskdata` is correct.
- **WSL cold start is ~9.4s**, against 0.37s warm. The store is warmed in
  a daemon thread at boot.
- **Git Bash rewrites `/tmp/...` arguments into Windows paths** before the
  process sees them. Drive pu from PowerShell, or `MSYS_NO_PATHCONV=1`.
- **`dcg` blocks** `rm -rf`, `shutil.rmtree`, `git checkout --`, and
  heredocs containing backticks or `$(...)`. Write commit messages to a
  file and use `git commit -F`.
- **Do not install anything into a unit.** `setup-project` refuses when it
  sees `UNIT_CONTRACT.md`. This was learned by doing it to mu-spec and
  reverting.

## Decisions made this session

- **One store, one record, every source** — every apparent difference
  between sources turned out to be a field, not a partition. Holding them
  together is what removes the priority ladder and collapsed the inbox
  into an ordinary queue entry.
- **No pu CLI.** A session outside the unit uses `task` for the task graph
  and two curl calls for long-form bodies. `task` for structure, HTTP for
  prose.
- **The body store stays**, over annotations, because a map body is
  rewritten in place as decisions land and fog graduates. Cost accepted:
  the map is unreadable when pu is stopped; tickets are not.
- **Frontier ≠ queue.** A person working a map must see the grilling
  ticket that is next; pu must never select one. Two queries, and
  runnability is a conjunction — an explicit `+afk` *and* a kind with a
  session type.
- **No Taskwarrior hook guarding `+afk`** — a speed bump the sessions it
  targets must routinely bypass is friction, not protection. The
  escalation path predates the CLI decision anyway: the HTTP API already
  accepted `afk: true` on an unauthenticated loopback.
- **Execution sessions run in the target repo**, so that repo's own
  conventions and gate hooks apply. Its skills are copied in
  non-destructively; its settings file is read and never touched.
- **ADRs dropped from domain-modeling** — a resolved ticket already
  carries the reasoning, and a second decision log drifts.
- **The glossary stays out of the spec graph.** mu-spec holds claims, not
  definitions; `CONTEXT.md` lives in the project repo.
- **Skills install globally, not per repo** — one queue and one set of
  efforts means one copy. Per-repo copies would be N copies of one fact.
