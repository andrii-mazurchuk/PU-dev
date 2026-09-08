# UNIT_CONTRACT — pu

The system-wide standard is the gateway's `docs/UNIT_STANDARDS.md`. If this
file and that one disagree, that one is right and this one is the bug.

## What pu implements

- The four standard endpoints: `GET /health`, `GET /stats`,
  `GET /prompts/<tier>` (`default`, `reference`), `GET /tools`.
- `POST /inbox` — the convention for accepting pushed work from a peer.
- `unit_type: processing`, `lifecycle: persistent`, port **9001**.

## What is specific to pu

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
