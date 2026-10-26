# Handoff — 2026-10-26 — pu: vision, reliability build, live verification, first real sessions agreed

> Previous handoff: `.claude/handoff/2026-09-25-dashboard-scopes-refresh-and-theme.md`.
> The Oct 9–11 session (session-type store, triggers, sessions view, 18 commits up to `a9ae5cf`) wrote **no handoff**; its decisions live on the pu map. This session ran 2026-10-23 → 26.

## Session summary

Andrey set out to finish pu as a reliable unit. This session:
- **Checked the live node** and found four bugs.
- **Ran the pu vision grilling**, which closed that ticket by mistake (see Gotchas).
- **Built everything the vision decided,** then four more grillings (placement, exposure, trigger concurrency and budget, dashboard review, stats and logs) and built each of those too.
- **Briefed the gateway session,** which landed the agent-role registry and a 30s tick for pu.
- **Re-verified on the restarted node,** fixing two more bugs found there.
- **Agreed with Andrey on pu's first recurring sessions,** which is where the next session starts.

All on `master`, pushed: `a9ae5cf..4f83e40`, 29 commits. The suite is green (391 passed). The live node runs the build from `f759bc3` or later; **`4a67dca` and `4f83e40` reach it on the next node restart**.

## What's done

- **Bugs:**
  - resolve 502 on retry: now 409, and the answer is never overwritten
  - a short uuid dropped the connection: prefixes now work everywhere
  - stats vs queue counts disagreed
  - tzdata was undeclared
  - a cron trigger created a task on every tick
  - a task with no repo was auto-blocked as "stuck"
  - Taskwarrior `end.*` filters return nothing on the live node: compared in Python now
  - `+needs-human` corrupted a task's description: the tag is `needs_human`, and the CLI refuses non-`[A-Za-z0-9_]` tags
- **Vision build:**
  - inbox deleted
  - `agent_role` (default `system`), validated against the gateway registry
  - `pu.runs` namespace, hidden from every view except the queue
  - report contract: six fields, 16k cap, parsed
  - failure policy: retry once, then handed to a person, always notified
  - crash recovery with per-run in-flight markers
  - why-not tick log, kill switch (`set_paused`), silence alerts, session-type dry run
- **MCP:**
  - holonic is attached to every queue session (**hard-coded, ponytail-marked; a memory note says remove later**)
  - a per-type MCP server registry, http only, `${ENV}` placeholders, config written into the type's own folder
- **Placement:**
  - a ticket's project must be its map's or nested under it
  - `move_task` (also retitles)
  - a map with open tickets can't be closed
  - daily `audit_tasks`
  - **19 misplaced tickets moved; the audit is clean**
- **Concurrency:**
  - the lock covers deciding, not running
  - `set_max_concurrent` live (default 1), one launch per tick, one session per repo
  - queue order: priority, then first fired; `emits.priority`, `emits.stale_after_seconds` (high priority goes stale in 1h)
  - reserve estimate, and a 15% runway on cost and usage
- **Exposure:** `list_running`, `needs_attention`, `get_spend` by day/week/month; `run_tick` removed from agent tools.
- **Dashboard review (backend):**
  - only permission edits (grant, role, mcp_servers, plugins, claims) switch a live type off, and only agent edits
  - expired work is tagged `expired`
  - tokens recorded per run
- **Stats and logs:**
  - `/stats` gains sessions, triggers, tasks (agent / yours / blocked) and unit sections, with windows (all, today, 7 days, 30 days) and min/max/avg/p50/p95
  - `rollup.json`
  - every attempt goes to mu-logs as `session_run`, plus a `pu_event` entry shape
  - transcripts purged after 30 days
- **Claim reaper replaced:** stale claims are reported (on `pu.runs` only), never released.

## Current state / open threads

- **Next is the ticket "Set up pu's first recurring sessions…"** (`c82748f3`). It's agreed; nothing is built yet.
- **Waiting on Andrey:**
  - the prototype ticket for the three dashboard views (Tasks, Sessions, Analytics); he directs it
  - grillings: what closing a map means; the analytical tool set beyond stats
- **Blocked on standards work:** the human-only guardrail, then the dashboard write door, then the approval queue. Also the declare convention, which blocks peer-declared types.
- **Plugins** per session type: its own ticket, not built.
- **Nothing has run on the live node since 2026-10-01:** there is no agent-runnable work. The first real run will be the first proof of the new run-record and mu-logs format.
- **Pre-existing and untouched:** an uncommitted `LATEST.md` (it held the 09-25 handoff) and `skills/domain-modeling/CONTEXT-FORMAT.md`, plus an untracked `CONTEXT.md`.

## ▶ Next step

**Work ticket `c82748f3` ("Set up pu's first recurring sessions").** In order:
1. **Settle E's sources with Andrey.** E is AI/LLM industry news, model releases and agent tooling, plus papers; not only AUG.
2. **Check every source** with `pu_dry_run_trigger`: the HN API, RemoteOK plus a Polish job board feed, the arXiv RSS and lab feeds. **All watches fetch daily.**
3. **Spawn a fresh agent** with only pu's tools and the ticket's spec. It writes the five session types (A: sonnet, B/C/E: haiku, D: sonnet) and their triggers, all switched off. It checks each type with `pu_dry_run_session_type`.
4. **Andrey switches each one on.** Watch the first run of each: a structured report, a `session_run` in mu-logs (`mu-logs_query_entries source_unit=pu`), and for A a Telegram message through cu's push tool.
5. **Run the three drills once** (failure, needs-human, switches), haiku with grant none.

## Files & references to read

- **pu map** "A session type is a user-authored object…" (`05b218d5`): its Decisions-so-far and the ticket `c82748f3`. Read the resolution of "pu vision: the central session runner" (`e0f917fb`) for the whole model.
- `pu/pipeline.py`: the tick (gates, emit, expiry, ceiling, selection, `_Launch`, `_record_run`).
- `pu/service.py`: write doors, placement rule, `needs_attention`, activation rules (`PERMISSION_FIELDS`).
- `pu/stats.py`: every stats section.
- `pu/triggers.py`: watch sources, `fetch_due`/`after_fetch`, cron.
- `pu/report.py`: the six-field contract every prompt ends with.
- `C:\agents\holonic-node\docs\UNIT_STANDARDS.md` §"Agent roles": the gateway's registry contract (`GET/POST/DELETE {bridge}/agent-roles`).

## Gotchas / warnings

- **The pu vision ticket (`e0f917fb`) was resolved early by mistake.** The v1 map says vision tickets close at the unit's exit gate. Its gate role is carried by **"pu passes its exit gate"** (`70812253`), and cu's vision is wired to that.
- **Taskwarrior on the node differs from local 2.6.1:**
  - `end.after:`/`end.before:` returned nothing there; compare in Python.
  - Use `uuid.startswith:` for prefixes; a bare all-digit prefix reads as a short id.
  - Hyphenated `+tags` break the CLI.
- **WSL cold starts** make the suite's binary probe time out (30s), and whole files skip silently. Warm it first with `wsl -d Ubuntu -e task --version`, and **check the run reports no skips**.
- **The dcg guard** blocks `rm -rf`, and shell redirects to `$VAR` paths. Heredocs containing backticks or `\\n` sometimes mangle. Write edit scripts to the scratchpad and run them with `uv run python`, or use Edit.
- **The gateway acts only on Andrey's approval given in its own session,** never relayed. Tell it what's needed and that Andrey must approve there.
- **No cost cap**, by Andrey's choice: the account usage limits are the ceiling. `PU_MAX_CONCURRENT`, the live ceiling, is 1.
- **Tests must not reach the real logs port:** `conftest.py` points `logs_client.DEFAULT_PEERS_PATH` at an empty dir. The store fixture removes its WSL data on teardown.

## Decisions made this session

- **pu runs standardised work only.** One-off and dynamic work goes to cu. Tasks are the single path to a session; no direct trigger-to-session.
- **A session type is the agent definition, and every type has one `agent_role`.** The gateway holds the role registry and cu writes to it.
- **Placement:** a task needs a map; a map needs a project and may be empty; several maps per project are fine.
- **Failure policy:** retry once, then hand to a person; notify on every failure. A partial or needs-human report keeps its answer, with no retry.
- **Order and budget:**
  - first fired, first run, within priority
  - one launch per tick
  - the reserve estimate counts running sessions toward the cap
  - a 15% runway on both cost and usage
- **Stats are big by design** (they're for au). Computed from pu's own records, never read back from mu-logs. `pu_event` reasons are composed by pu, never by an agent.
- **First recurring sessions:** morning brief, HN, jobs, weekly pu self-review, AI/LLM news. Every watch fetches daily.
