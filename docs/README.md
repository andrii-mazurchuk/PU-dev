# pu — internal documentation

Written for an agent or a person arriving at this repository with no prior
context, and needing to understand it well enough to change it safely.

Nothing here is served over HTTP. The peer-facing description of this unit
is `prompts/default.md` and `prompts/reference.md`; those answer "what can
I ask pu for". These files answer "how does pu work, and what must I not
break".

## Reading order

Read `ARCHITECTURE.md` first. It is the only file that assumes nothing.
After that the order depends on what you came to do:

| you are here to… | read |
|---|---|
| understand the system at all | `ARCHITECTURE.md` |
| change what a task *is*, or add a kind | `DATA_MODEL.md`, then `DECISIONS.md` |
| add or change an endpoint | `HTTP_API.md`, then `ARCHITECTURE.md` §the two write doors |
| debug a tick that did the wrong thing | `FLOWS.md` §1, then `DECISIONS.md` |
| understand why intake cannot invent work | `FLOWS.md` §2 |
| deploy, register, or configure it | `OPERATIONS.md` |
| change something that looks redundant | **`DECISIONS.md`, first, without exception** |

## The files

- **`ARCHITECTURE.md`** — what this unit is and is not, where its boundary
  runs, the module map and which way dependencies point, the two write
  doors, and why the session layer has no way back in.
- **`DATA_MODEL.md`** — the universal record, the closed vocabularies, how
  ordering and runnability are computed, the Taskwarrior schema, and every
  byte this unit writes to disk.
- **`HTTP_API.md`** — every endpoint, in full: parameters, request and
  response shapes, status codes, and the raw-text body path.
- **`FLOWS.md`** — five control flows traced end to end across modules,
  with `file:line` anchors.
- **`OPERATIONS.md`** — configuration, running it, the Windows/WSL
  arrangement, verification, what degrades versus what raises, and
  registering with the gateway.
- **`DECISIONS.md`** — the register of load-bearing decisions: what was
  measured, what was chosen, and what breaks if it is undone.

## A note on why the code reads the way it does

Every module in `pu/` opens with a docstring explaining its own reasoning.
That is deliberate and these documents do not replace it — they connect
it. A module docstring can say why *that* module refuses multi-line
attribute values; only `FLOWS.md` can show you the path by which a
session's own output reaches that function.

If a document here and a module docstring disagree, the docstring is
closer to the code and more likely right — but the disagreement is a bug
in one of them, so fix it rather than picking a side silently.
