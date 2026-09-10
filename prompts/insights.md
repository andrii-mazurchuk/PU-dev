# pu — insights

What is worth a periodic analytical judgment about pu, and how to read its
`/stats` honestly. Written for the Analytical Unit's own review session,
not for a human. See `default.md` / `reference.md` for what pu does; this
is about what is worth *watching*.

## Why pu is the unit that matters most here

pu is the only unit in this node that launches `claude -p` sessions. Every
dollar the node spends on its own initiative is spent by pu. If exactly one
unit in a cycle deserves a careful read, it is this one.

## `runnable_now: 0` is usually correct, not a stall

The single most likely way to write a wrong judgment about pu is to see
`pending_tasks` above zero with `runnable_now: 0` and call it a backlog
that is not moving.

Read `by_kind` before concluding anything. Two kinds — `grilling` and
`prototype` — are human-in-the-loop **by definition**: an agent must never
resolve one. A queue made of grilling tickets with `runnable_now: 0` is a
queue in its correct state, waiting on a person. That is not pu stalling;
that is pu doing exactly what it was built to do.

`runnable_now: 0` is only worth naming when `by_kind` shows `research` or
`execution` tickets present and nothing is running — those are the kinds an
agent may resolve alone.

## `claimed` is a duration signal, never a count signal

A claim is taken as a session's first write and released when it finishes.
Any single snapshot showing `claimed: 1` means "a session is working," which
is normal and good.

What is worth a judgment is a claim that is still held across your whole
analysis window with no corresponding new `sessions.runs`. That shape means
a session died holding its claim, and the ticket is now invisible to every
future frontier query — a silent stall that looks like ordinary work from
any single snapshot. You cannot see claim age from `/stats`; say so, and
frame it as a suspicion tied to the window rather than a fact.

## `blocked` is structure, not trouble

Blocked tasks are Taskwarrior dependencies wired deliberately when a map is
charted. A steady `blocked` count is a map with sequencing in it. A blocked
count that never falls across several cycles is the thing worth naming —
it suggests the blockers themselves are not being worked.

## `sessions` — a lifetime total handed to you without a window

`runs`, `cost_usd_total`, `by_session_type`, `by_outcome` and `last_run_at`
are **cumulative since the store was created**, not windowed. A single
snapshot of `cost_usd_total` is close to meaningless on its own. What you
can legitimately do is compare it against the same field in your own prior
judgment for this unit and speak about the delta across the cycle.

`by_outcome` is where a real finding usually lives: a failure share that is
rising cycle over cycle is worth escalating, because pu failing sessions
costs money and resolves nothing. `by_session_type` tells you *which* kind
of session is consuming the budget.

`runs: 0` with a non-empty queue is worth naming only when the queue holds
agent-resolvable kinds. With a queue of grilling tickets it means nothing.

## What `/stats` cannot tell you, and you must not invent

- **Cost per resolved ticket.** There is no resolution counter here. You
  can see spend and you can see queue depth, but you cannot divide one by
  the other. Do not present a rate you did not receive.
- **Whether a ticket is any good.** Queue depth is not quality.
- **Why a session failed.** `by_outcome` gives you counts, not causes.
- **Claim or ticket age.** Nothing here is timestamped per task.

If the honest answer this cycle is that the queue is small, human-gated,
and nothing has run, say that with `confident` and escalate nothing. A
node that is deliberately idle is not a finding.
