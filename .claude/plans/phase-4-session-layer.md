# Phase 4 — the session layer

Agreed before implementation. Phases 1–3 (store, record, typed write doors)
are committed; this covers everything that turns a queued task into a
`claude -p` run and back.

## 4a — logging and stats

**Session logs go to the logs memory unit.** That is the system standard,
not a PU convention. One `session_run` entry per run, payload
`{outcome, duration_seconds, cost}` — mu-logs assigns id and timestamp and
runs its own rollups over them.

Reached by **capability, never by name**: find the peer in `peers.json`
carrying `log_write` and POST directly. Not via the bridge — PU's ability
to log must not depend on a third unit being up. Best-effort throughout; a
broken logs unit can never fail a session.

**PU also keeps local run artifacts**, for the two things the log entry
deliberately does not carry:

```
state/sessions/<task-uuid>/<utc-stamp>/
  prompt.txt     the assembled prompt, verbatim
  stream.jsonl   the raw stream -- the parsed summary discards tool-call
                 arguments, and this is the only on-disk record of what a
                 session that died mid-run actually wrote
  result.json    exit code, cost, duration, ordered tool calls, outcome
```

**`/stats` reports derived aggregates over that history.** The written
standard says mechanical data, never agentic judgement; the intent is
precomputed numbers an analytical unit can judge. Those reconcile on one
line: *a derived aggregate is mechanical, a verdict is not.* Cost per
session type, success and failure counts, queue depth by kind — yes.
"The queue is unhealthy" — never; that is AU's job, and precomputing it
here would be PU grading its own work.

## 4b — how a session reaches PU: it does not

PU spawns the session, so PU assembles the context going in and parses the
result coming out. **A PU-spawned session needs no write access to PU.**

The only caller that genuinely needs to write from outside is an
interactive wayfinder session, which is not PU-spawned, and which already
has the six typed operations over HTTP.

This is what makes intake safe: the session returns a *proposal*, PU
validates it against the closed vocabulary and writes it. A session cannot
write a task PU did not approve.

## 4c — SOPs

`sops/sop-<tag>/SKILL.md`, frontmatter `name` + `description`, plus an
optional `kinds:` line naming which kinds the procedure applies to. No
manifest — the filesystem is the index, so it cannot go stale.

**The catalogue ships empty.** Zero SOPs is a supported state and there is
a test for it: empty catalogue, session proceeds, nothing breaks.

**How wayfinder learns SOPs exist.** Wayfinder consults the tracker doc for
how this repo expresses its operations, and we author that doc. Its
child-ticket operation gains one line: consult the catalogue, attach the
tags that apply. Wayfinder's own body never changes.

**The typo hazard.** A tag that looks like an SOP but is misspelled means
the procedure silently does not apply. So task-creating operations return
`sops_matched` and `tags_without_sop`. Informational, never an error:
unknown tags stay legal, because free-form labelling is useful.

## 4d — intake

**Intake transcribes; it does not invent.** Every task it proposes must be
traceable to something the message actually said, and carries the sentence
it came from.

Returns a structured block, validated before anything is written:

```json
{"tasks": [{"title", "kind", "project", "tags", "afk", "body", "traces_to"}]}
{"bounce": {"condition", "reason"}}
```

**Bounce conditions, definite:**

1. `no_stateable_outcome` — the message never says what would be true when done
2. `no_placeable_target` — no project or repo it belongs to
3. `needs_a_map` — resolving it means choosing between options; that is a
   decision, and creating a map is a human act
4. `material_ambiguity` — two readings produce two different pieces of work

A bounce is a normal outcome, not a failure.

**Intake decides `afk` per task**, and may split one message into an AFK
task plus a not-AFK verification task. Not enforced. Its `CLAUDE.md`
carries the AFK/HITL doctrine: a human-in-the-loop item resolves only
through live exchange, and an agent never stands in for the human's side
of it.

The guardrail underneath is already built: `runnable` is a conjunction, so
a `grilling` task marked AFK is still inert.

**Writes are all-or-none.** Validate the whole proposal, then write; on any
failure mid-write, delete what was created. A malformed proposal creates
nothing rather than half a plan.

## 4e — the three session types

A session type is a directory containing a `CLAUDE.md`. Adding one is never
a code change.

| | intake | research | execution |
|---|---|---|---|
| input | one intake record's body | one ticket's question | one task + its repo |
| output | a task proposal, or a bounce | a resolution | a code change |
| writes files | no | no | yes |
| network | no | yes | yes |
| grant | `Read`, lookup script | + `WebSearch`, `WebFetch` | `Bash`, `Read`, `Write`, `Edit` |

Research gets no Write deliberately: its artifact is the resolution, and
letting it write files invites findings that live where nobody reads them.

## 4f — execution runs in the target repo

Chosen over running from the session-type directory, because the target
repo's own conventions and gate hooks are the strongest safety property
available and they are wired per-repo.

Implies, and each is real work:

- resolve a task's repo path; refuse to run if it is not a git repo
- a setup step that transfers execution's skills and settings into the
  target repo — deciding what is copied, what is merged, and what must
  never overwrite an existing file
- confirm the gate hooks actually fire for a `claude -p` session there; if
  they do not, option B loses its justification and we revisit
- inject session-type instructions via `--append-system-prompt`, since the
  repo's own `CLAUDE.md` now owns cwd

## Not in this phase

GitHub mirror (one-way, phase 6). `units.yaml` registration and the
wayfinder tracker doc (phase 7). Reviewing the old unit's SOPs for reuse.
