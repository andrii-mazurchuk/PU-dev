# UNIT_CONTRACT — pu

The system-wide standard is the gateway's `docs/UNIT_STANDARDS.md`. If this
file and that one disagree, that one is right and this one is the bug.

## What pu implements

- The four standard endpoints: `GET /health`, `GET /stats`,
  `GET /prompts/<tier>` (`default`, `reference`), `GET /tools`.
- `POST /inbox` — the convention for accepting pushed work from a peer.
- `GET /dashboard` — a **page**-tier dashboard, per the standard's
  "Dashboards" section. Self-contained HTML; the node frames it and
  still owns the chrome.
- `unit_type: processing`, `lifecycle: persistent`, port **9001**.

## What is specific to pu

- **Private storage is one directory**, learned from
  `HOLONIC_STATE_DIR` -- the standard's "Private storage" section. It
  holds `taskdata/` (the Taskwarrior store), `taskrc`, `bodies/`,
  `sessions/` and `repeat_tracker.json`. The task store used to sit at
  `~/.pu-taskdata`, outside the repo; nothing migrates automatically and
  pu says so at boot when the new store is empty.
- **It gates on the account-usage ceilings** before selection, in the
  same place as its cost gate -- the standard's "Account-usage ceilings"
  section. Blocked means skip the tick and retry; no task changes state.
  The owner is told on the transition, not every tick.
- **It holds the task queue and runs the sessions.** This is the only unit
  that launches `claude -p`.
- **Its store is Taskwarrior, and there is no native Windows build.** The
  binary is reached through `PU_TASK_CMD`, which on a Windows host is
  `wsl -d Ubuntu -e task`. Nothing outside `taskstore.py` knows this.
- **One store, one record shape, every source.** A wayfinder ticket, a
  mirrored GitHub issue and an inbox message are the same record differing
  by field. There is no per-source partition and no priority ladder;
  ordering is Taskwarrior urgency with per-kind coefficients.
- **Two typed write doors, no generic CRUD.** The six wayfinding
  operations, and `/inbox` — which can only ever mint an `intake` record
  for a session to convert. Reading is open to every peer.

- **It takes the page tier, and only because of the graph.** Its
  dashboard is a dependency graph, which is the case the standard names
  as the escape hatch's reason to exist. Every other unit should serve a
  spec. The page draws no navigation, links no peer, renders no other
  unit's data, and assumes nothing about its own port being reachable.
- **Its dashboard is read-only, and the proxy enforces that rather than
  the page promising it.** The node forwards GET and refuses everything
  else with 405. The `ask` panel is present as layout and submits
  nothing; wiring it needs a write path through the bridge, never a
  private endpoint the dashboard alone knows about.
- **An ask session is granted no tools.** Sessions this unit spawns have
  no way back into it, and this one answers from context assembled going
  in. It has no kind mapped to it, so nothing anyone can write to the
  store causes one to run.
- **It declares `run_tick`.** `POST /trigger` is a tool, so any peer or
  MCP client that reaches the bridge can spend a session by name. That
  widening was decided on its own merits, not as a side effect of a
  dashboard button.
