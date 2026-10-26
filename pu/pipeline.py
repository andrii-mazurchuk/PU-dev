"""One tick: pick the most urgent runnable task, run its session, record it.

The whole selection rule is one line -- take the top of `/queue` -- because
the ordering lives in Taskwarrior's urgency and the eligibility lives in
`Task.runnable`. There is deliberately no priority ladder here: with one
store holding every source, "research before execution" is a coefficient
rather than an `if`, retunable without a code change and inspectable by
hand with `task next`.

Order of operations, and each is load-bearing:

1. **One tick decides at a time.** The lock covers gates, selection and
   the claim -- not the session. Claim-before-work (step 5) is what closes
   the race for the head of the queue, so a session can run outside the
   lock and the next poke can start another, up to `PU_MAX_CONCURRENT`
   (default 1). Two sessions never share a target repository.
2. **Recover a lost run, report stale claims**, before anything reads the
   queue. A run this process lost is released; a claim anyone else holds
   is only ever reported.
3. **Cost gate before selection**, on every path into this function. A
   gate that runs after selection can be skipped by whatever selected
   differently, and a gate an external trigger can route around is not a
   gate.
4. **Evaluate triggers and create the work they imply.** After the gates
   so a blocked unit changes no state at all, rather than quietly
   accumulating a night of tasks from a misfiring watch; before selection
   so a trigger that fires can have its work run on the same tick. A
   trigger decides that work *exists* -- starting a session is still step 6,
   through the gates above, and there is no other door in.
5. **Claim before work.** The claim is the store's own `+ACTIVE` state, so
   a concurrent session's frontier no longer contains the task. It is the
   session's first write for the same reason wayfinder makes it one.
6. **Run.**
7. **Record whatever happened** -- to local artifacts, to the logs unit,
   and onto the task. A session that failed leaves the task released and
   annotated rather than silently claimed forever.
"""

from __future__ import annotations

import dataclasses
import functools
import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from pu import (
    bodies,
    control,
    guards,
    logs_client,
    mcpstore,
    notify,
    records,
    repo_setup,
    report as report_mod,
    roles as roles_mod,
    runner as runner_mod,
    session_types as session_types_mod,
    sessions as sessions_mod,
    sops,
    taskstore,
    triggers,
    usage_gate,
)

UNIT_NAME = "pu"

# One tick at a time, process-wide. Non-blocking: an overlapping poke is
# declined and says so, rather than queueing up behind a long session and
# firing into a queue that has moved on since.
_TICK_LOCK = threading.Lock()

# Sessions in flight in this process, by task uuid, with the repository each
# runs in. The ceiling and the one-session-per-repo rule read it; crash
# recovery reads it to tell a live run's marker from a dead process's.
_RUNNING: dict[str, dict[str, Any]] = {}
_RUNNING_LOCK = threading.Lock()


@dataclasses.dataclass(frozen=True)
class _Launch:
    """What a tick decided to run, claimed and registered, to be run after
    the deciding lock is released."""
    run: Callable[[], "TickResult"]
    task_uuid: str
    marker: Path
    emitted: tuple = ()


def max_concurrent(environ: dict[str, str] | None = None,
                   state_dir: Path | None = None) -> int:
    """How many sessions may run at once. A live setting the owner changes
    (`set_max_concurrent`, persisted in private storage) wins; otherwise
    `PU_MAX_CONCURRENT`; otherwise 1. Set a cost cap before raising it."""
    configured = control.configured_max(state_dir)
    if configured:
        return configured
    raw = (os.environ if environ is None else environ).get("PU_MAX_CONCURRENT", "")
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


def runway_fraction(environ: dict[str, str] | None = None) -> float:
    """The margin kept before a limit, as a fraction of it: at 0.15, work
    goes on one session at a time from 85% of the daily cap or of any usage
    ceiling. One number for both, like the ceilings themselves."""
    raw = (os.environ if environ is None else environ).get("PU_RUNWAY_FRACTION", "")
    try:
        value = float(raw)
    except ValueError:
        return 0.15
    return value if 0 <= value < 1 else 0.15


def reserved_cost(session_store) -> float:
    """What the sessions running now are expected to cost when they finish:
    each counted at its type's average, or the overall average for a type
    with no history. Added to finished spend wherever spend is compared to
    the cap, so N sessions in flight cannot carry spend past it unseen."""
    with _RUNNING_LOCK:
        running = [r.get("session_type") for r in _RUNNING.values()]
    if not running:
        return 0.0
    agg = session_store.aggregates()
    overall = (agg["cost_usd_total"] / agg["runs"]) if agg["runs"] else 0.0
    by_type = agg["by_session_type"]
    return round(sum(by_type.get(t, {}).get("avg_cost_usd", overall)
                     for t in running), 6)


def _ceiling(policy, session_store, today: str, state_dir: Path,
             mcp_bridge_url: str | None, windows=None, ceilings=None) -> int:
    """How many sessions may run right now. Within the runway of the daily
    cap (projected spend) or of any usage ceiling, it drops to one -- work
    continues, unparallel -- and the owner is told on the change, never
    every tick."""
    wanted = max_concurrent(state_dir=state_dir)
    edge = 1 - runway_fraction()
    reason = None
    cap = policy.get("daily_cost_cap_usd")
    if cap is not None:
        try:
            cap = float(cap)
            projected = session_store.cost_since(today) + reserved_cost(session_store)
            if projected >= edge * cap:
                reason = f"the budget is running low: ${projected:.2f} of the ${cap:.2f} daily cap, counting running sessions"
        except (TypeError, ValueError):
            pass
    for name, ceiling in (ceilings or {}).items():
        reading = (windows or {}).get(name)
        used = reading.get("utilization") if isinstance(reading, dict) else None
        if reason is None and isinstance(used, (int, float)) and used >= edge * ceiling:
            reason = (f"usage limits are running low: the {name.replace('_', '-')} "
                      f"window is at {used:.0%} of its {ceiling:.0%} ceiling")
    path = Path(state_dir) / "headroom.json"
    previous = usage_gate.load_blocked_state(path)
    if reason != previous:
        logs_client.record_event(
            "runway_on" if reason else "runway_off", "unit", "pu",
            reason or "budget and limits back outside the runway",
            detail={"max_concurrent": wanted})
        notify.notify_owner(
            mcp_bridge_url,
            f"pu: {reason}. One session at a time until it recovers."
            if reason else
            f"pu: budget and limits have recovered; up to {wanted} sessions "
            f"may run at once again.",
            source_unit=UNIT_NAME)
        usage_gate.save_blocked_state(path, reason)
    return 1 if reason else wanted


def running() -> list[dict[str, Any]]:
    """The sessions in flight in this process, longest-running first."""
    now = datetime.now(timezone.utc)
    with _RUNNING_LOCK:
        items = [dict(info, task_uuid=uuid) for uuid, info in _RUNNING.items()]
    for item in items:
        item.pop("repo", None)
        try:
            item["elapsed_seconds"] = int(
                (now - datetime.fromisoformat(item["started_at"])).total_seconds())
        except (KeyError, TypeError, ValueError):
            item["elapsed_seconds"] = None
    return sorted(items, key=lambda i: i.get("started_at") or "")


def _repo_key(repo: str | None) -> str | None:
    return os.path.normcase(os.path.abspath(repo)) if repo else None


@dataclasses.dataclass(frozen=True)
class TickResult:
    ran: bool
    reason: str = ""
    task_uuid: str | None = None
    session_type: str | None = None
    outcome: str | None = None
    detail: str = ""
    cost_usd: float | None = None
    # What the triggers did this tick, one entry per trigger that fired or
    # failed. Reported even when no session ran: a tick that created three
    # tasks and selected none did something, and `ran=False` alone would
    # read as an idle unit.
    emitted: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


# -- the cost gate --------------------------------------------------------


def read_cost_policy(path: Path) -> dict[str, Any]:
    """This unit's own cost policy, written verbatim by the gateway.

    Always present, possibly `{}` -- which means enforce nothing. A missing
    or unparseable file means the same thing, deliberately: a consuming
    unit should never have to tell "no file" apart from "no policy"."""
    try:
        parsed = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def check_cost_gate(
    policy: dict[str, Any], session_store: sessions_mod.SessionStore, today: str,
    reserved: float = 0.0,
) -> str | None:
    """The reason work is blocked, or None to proceed.

    Enforcement is entirely unit-side: the gateway carries the policy
    verbatim and never reads it, so if this does not enforce it, nothing
    does."""
    cap = policy.get("daily_cost_cap_usd")
    if cap is None:
        return None
    try:
        cap = float(cap)
    except (TypeError, ValueError):
        return None
    # Finished spend plus what running sessions are expected to cost.
    spent = round(session_store.cost_since(today) + reserved, 6)
    if spent >= cap:
        return f"daily cost cap reached: {spent} >= {cap}"
    return None


def gate_state(
    session_store: sessions_mod.SessionStore,
    unit_root: Path,
    cost_policy_path: Path,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    state_dir: Path | None = None,
) -> dict[str, Any]:
    """What the two gates would decide right now, without deciding it.

    "Why has nothing run since this morning" is the question a person
    brings to this unit most often, and today it is answerable only by
    reading logs. This is that answer as data.

    It is a separate read from `tick` on purpose, and the separation is
    load-bearing in one direction: **asking must never be able to start
    a session.** A page that polls this refreshes every few seconds, and
    if the answer came from running a tick, looking at the dashboard
    would spend money.

    Both readings degrade the same way the gates themselves do -- an
    absent usage file and an absent policy both mean "nothing is
    blocking", which is what keeps the gates from latching shut.
    """
    moment = now()
    today = moment.strftime("%Y-%m-%d")

    policy = read_cost_policy(cost_policy_path)
    reserved = reserved_cost(session_store)
    cost_blocked = check_cost_gate(policy, session_store, today, reserved)

    windows = usage_gate.read_usage(unit_root / usage_gate.USAGE_FILENAME)
    ceilings = usage_gate.ceilings_from_env()
    usage_blocked = usage_gate.check_usage_gate(windows, ceilings)

    def window_rows() -> list[dict[str, Any]]:
        """The windows as meter rows, each carrying **its own** ceiling.

        They are separate limits, not an average, and a row drawn against
        the other one's ceiling reads "clear" while the gate is actively
        blocking on it -- a gauge contradicting the thing it reports.
        """
        rows: list[dict[str, Any]] = []
        for name in usage_gate.WINDOWS:
            reading = windows.get(name)
            if not isinstance(reading, dict):
                continue
            utilization = reading.get("utilization")
            if not isinstance(utilization, (int, float)):
                continue
            rows.append({
                "name": name.replace("_", "-"),
                "utilization": utilization,
                "ceiling": ceilings.get(name),
            })
        return rows

    cap = policy.get("daily_cost_cap_usd")
    spent = session_store.cost_since(today)
    # Not a gate -- a person's decision -- but it stops sessions all the
    # same, so it is reported first and wherever the gates are.
    pause = control.paused(state_dir)
    paused_note = (f"paused since {pause.get('since')}"
                   + (f": {pause['reason']}" if pause.get("reason") else "")
                   ) if pause else ""

    return {
        "blocked": bool(pause or cost_blocked or usage_blocked),
        "paused": pause,
        # The cost gate is reported first because it is the one a person
        # sets themselves, and so the one they can act on.
        "reason": paused_note or cost_blocked or usage_blocked or "",
        # The same facts arranged as label/value/note. Not judgement --
        # "blocked" and the reason are both mechanical readings; this is
        # only the shape a reader can scan.
        "summary": [
            {
                "key": "Sessions",
                "value": "paused" if pause else (
                    "blocked" if (cost_blocked or usage_blocked) else "clear"),
                "note": paused_note or cost_blocked or usage_blocked
                        or "nothing is holding work back",
            },
            {
                "key": "Spent today",
                "value": f"${spent:.2f}",
                "note": f"cap ${float(cap):.2f}" if cap is not None else "no cap set",
            },
            {
                "key": "Runs recorded",
                "value": str(len(session_store.runs(limit=10_000))),
                "note": "all time",
            },
        ],
        "cost": {
            "spent_today_usd": session_store.cost_since(today),
            "daily_cap_usd": policy.get("daily_cost_cap_usd"),
            "blocked": bool(cost_blocked),
        },
        "windows": window_rows(),
        "ceilings": dict(ceilings),
        # How many may run, how many are, and what those are expected to
        # cost -- the reserve every cap comparison above already counts.
        "concurrency": {
            "max": max_concurrent(state_dir=state_dir),
            "running": len(_RUNNING),
            "reserved_usd": reserved,
            "runway_fraction": runway_fraction(),
        },
    }


# -- context assembly -----------------------------------------------------


def build_prompt(
    task: records.Task,
    body: str | None,
    unit_root: Path,
    extra_context: str = "",
    agent_role: str = session_types_mod.DEFAULT_AGENT_ROLE,
    role_description: str = "",
) -> str:
    """Everything the session gets, assembled here rather than fetched by
    the session itself.

    Deterministic retrieval before the run, rather than handing an agent a
    workspace and hoping: what it is given is visible in `prompt.txt`
    afterwards, identical on a retry, and does not spend the session's
    budget on finding its own inputs."""
    lines = [
        f"# Task {task.uuid}",
        "",
        f"**{task.description}**",
        "",
        f"- kind: `{task.kind}`",
        f"- project: `{task.project or '(none)'}`",
        # Which agent this session acts for. Its memory in mu-agent lives
        # under this name, so a session that is not told reads and writes
        # the wrong namespace -- or none.
        f"- agent role: `{agent_role}`"
        + (f" — {role_description}" if role_description else ""),
    ]
    if task.url:
        lines.append(f"- authoritative source: {task.url}")
    if task.repo:
        lines.append(f"- repository: `{task.repo}`")

    matched, unmatched = sops.match(unit_root, list(task.tags))
    procedures = [t for t in matched if t != records.AFK_TAG]
    if procedures:
        lines += [
            "",
            "## Mandatory procedures",
            "",
            "This task's tags make these procedures binding, not optional "
            "reading. Resolve each one and follow it:",
            "",
        ]
        for tag in procedures:
            lines.append(
                f"- `{tag}` — run "
                f"`{session_types_mod.LOOKUP_COMMAND} --tag {tag}` "
                f"and read what it points at"
            )
    free_tags = [t for t in unmatched if t != records.AFK_TAG]
    if free_tags:
        lines += ["", f"Other tags, carrying no procedure: {', '.join(free_tags)}."]

    if body:
        lines += ["", "## Detail", "", body]

    if task.annotations:
        lines += ["", "## Notes on this task", ""]
        lines += [f"- {a}" for a in task.annotations]

    if extra_context:
        lines += ["", extra_context]

    # Last, so it is the freshest thing the session read before it starts:
    # every run's final message opens with the same six fields.
    lines += ["", report_mod.CONTRACT]

    return "\n".join(lines)


# -- outcome handling -----------------------------------------------------


# The failure policy (pu vision, 2026-10-24). Two classes, because a crash
# and an agent that says it failed deserve different second attempts:
#
# - **infrastructure** -- the launch failed, the process exited non-zero, or
#   it said nothing. Retry once, as it was.
# - **agent** -- the report says `OUTCOME: failed`. Retry once, with that
#   report in hand: it is an annotation, and annotations are in the prompt.
#
# After a second failure the task stops being agent-runnable and goes to a
# person. Every failure tells the owner. The count lives on the task, so a
# pu restart does not reset it.
FAILURE_PREFIX = "run failed:"
MAX_FAILURES = 2


def _failures(store: taskstore.TaskStore, uuid: str) -> int:
    task = store.get(uuid)
    return sum(a.startswith(FAILURE_PREFIX) for a in (task.annotations if task else ()))


def _fail(
    store: taskstore.TaskStore, task: records.Task, why: str,
    mcp_bridge_url: str | None,
) -> tuple[str, str]:
    """Release, count, and either leave it for one retry or hand it over."""
    store.release(task.uuid)
    store.annotate(task.uuid, f"{FAILURE_PREFIX} {why}")
    count = _failures(store, task.uuid)
    first_line = why.splitlines()[0]
    if count >= MAX_FAILURES:
        guards.block(store, task.uuid,
                     f"failed {count} times; a person needs to look at this")
        notify.notify_owner(
            mcp_bridge_url,
            f"pu gave up on a task after {count} failed runs and needs a "
            f"person.\n\n{task.description}\nlast: {first_line}\n"
            f"task {task.uuid}",
            source_unit=UNIT_NAME)
        return "failed_twice", first_line
    notify.notify_owner(
        mcp_bridge_url,
        f"pu's run failed and will be retried once.\n\n{task.description}\n"
        f"{first_line}\ntask {task.uuid}",
        source_unit=UNIT_NAME)
    return "failed", first_line


def _settle(
    store: taskstore.TaskStore, task: records.Task,
    result: runner_mod.SessionResult, mcp_bridge_url: str | None,
) -> tuple[str, str]:
    """Decide what a finished run means for its task."""
    if not result.ok:
        return _fail(store, task, f"infrastructure: exit {result.exit_code}",
                     mcp_bridge_url)
    answer = (result.text or "").strip()
    if not answer:
        return _fail(store, task, "infrastructure: the session said nothing",
                     mcp_bridge_url)

    report = report_mod.parse(answer)
    if report.outcome == "failed":
        return _fail(store, task, f"the agent reported failure\n\n{report.text}",
                     mcp_bridge_url)

    store.annotate(task.uuid, report.text)
    if report.outcome == "partial" or report.needs_human:
        # Not a failure and not finished: the answer is kept, the task stays
        # open, and it is a person's now. No retry -- a second agent run
        # would not supply what a person has to.
        need = report.needs_human or "the agent finished only part of it"
        guards.block(store, task.uuid, f"needs a person: {need}")
        notify.notify_owner(
            mcp_bridge_url,
            f"pu ran a task that now needs a person.\n\n{task.description}\n"
            f"{need}\ntask {task.uuid}",
            source_unit=UNIT_NAME)
        return "needs_human", need

    store.complete(task.uuid)
    if not report.structured:
        # Done as far as anyone can tell, but the report did not follow the
        # contract -- which is worth a look before it becomes a habit.
        notify.notify_owner(
            mcp_bridge_url,
            f"pu resolved a task, but the session's report was unstructured."
            f"\n\n{task.description}\ntask {task.uuid}",
            source_unit=UNIT_NAME)
        return "resolved_unstructured", answer[:200]
    return "resolved", answer[:200]


# -- MCP servers a type attaches ------------------------------------------


def _write_mcp_config(stype, mcp_store, bridge_url: str | None) -> str:
    """Write this type's MCP config into its own directory and return the
    path. Per type, per launch: servers are attached where the type says,
    never set up system-wide, and the file carries resolved tokens so it
    stays in private storage rather than in argv."""
    if mcp_store is None:
        raise mcpstore.McpStoreError("no MCP registry wired")
    servers: dict[str, Any] = {}
    if bridge_url:
        servers["mcp-bridge"] = {"type": "http", "url": bridge_url}
    for name in stype.mcp_servers:
        record = mcp_store.get(name)
        if record is None:
            raise mcpstore.McpStoreError(
                f"session type {stype.name!r} attaches MCP server {name!r}, "
                f"which is not in the registry")
        servers[name] = mcpstore.resolve(record)
    path = Path(stype.dir) / ".mcp-session.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    return str(path)


# -- stale trigger work ---------------------------------------------------


DEFAULT_HIGH_STALE_SECONDS = 3600
EXPIRED_TAG = "expired"


def _expire_stale(store, moment: datetime, mcp_bridge_url: str | None,
                  state_dir: Path | None = None) -> None:
    """Close queued trigger work its trigger said would go stale, tagged
    `expired` so it is never mistaken for work that ran, counted in stats,
    and pruned with the other finished run-tasks. Only work nobody has
    started: a run in progress is never cut off."""
    try:
        waiting = store.export("status:pending", "-ACTIVE", "expires.any:")
    except taskstore.TaskError:
        return
    for task in waiting:
        try:
            deadline = datetime.fromisoformat(task.expires)
        except (TypeError, ValueError):
            continue
        if deadline > moment:
            continue
        why = ("sessions were switched off" if control.paused(state_dir)
               else "no session slot came free in time")
        store.annotate(task.uuid, f"expired: not started by {task.expires} "
                                  f"({why}), so never run")
        store.modify(task.uuid, add_tags=[EXPIRED_TAG])
        store.complete(task.uuid)
        logs_client.record_event(
            "work_expired", "task", task.uuid,
            f"not started by {task.expires}: {why}", title=task.description,
            detail={"trigger": task.trigger, "priority": task.priority})
        notify.notify_owner(
            mcp_bridge_url,
            f"pu let a task expire unrun: it waited past its trigger's "
            f"stale_after.\n\n{task.description}\ntask {task.uuid}",
            source_unit=UNIT_NAME)


# -- the placement audit, daily ------------------------------------------


def _daily_audit(store, state_path: Path, moment: datetime,
                 mcp_bridge_url: str | None) -> None:
    """Run the placement audit at most once a day and tell the owner about
    faults it has not reported before. Programmatic, inside the unit; a
    fault already told stays quiet until it is fixed and comes back."""
    from pu import service  # service imports this module

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if (moment - datetime.fromisoformat(state["at"])).total_seconds() < 86400:
            return
        told = set(state.get("told") or ())
    except (OSError, ValueError, KeyError, TypeError):
        told = set()
    try:
        faults = service.audit(store)
    except taskstore.TaskError:
        return
    keys = {f"{kind}:{row['uuid']}" for kind, rows in faults.items() for row in rows}
    new = [(kind, row) for kind, rows in faults.items() for row in rows
           if f"{kind}:{row['uuid']}" not in told]
    if new:
        lines = [f"- {kind}: {row['title'][:60]} ({row['uuid'][:8]}) -- {row['why']}"
                 for kind, row in new[:15]]
        notify.notify_owner(
            mcp_bridge_url,
            f"pu's daily audit found {len(new)} new placement fault(s):\n"
            + "\n".join(lines)
            + ("\n..." if len(new) > 15 else "")
            + "\n\nmove_task fixes placement; audit_tasks lists them all.",
            source_unit=UNIT_NAME)
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({"at": moment.isoformat(),
                                          "told": sorted(keys)}), encoding="utf-8")
    except OSError:
        pass


# -- crash recovery -------------------------------------------------------
#
# A run pu was in the middle of when it stopped. The stale-claim reaper
# would release the claim two hours later and say nothing about a run; this
# finds it on the very next tick, because the marker is written at the claim
# and removed on every exit a live process makes. The tick lock means no
# run of this process can be in flight while a tick starts, so a marker
# present then belongs to a process that is gone. (pu vision, 2026-10-24.)


def _write_inflight(state_dir: Path, task: records.Task, stype) -> Path:
    """One marker per run, so several runs can be in flight at once."""
    path = Path(state_dir) / "inflight" / f"{task.uuid}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "task_uuid": task.uuid,
            "session_type": stype.name,
            "agent_role": stype.agent_role,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }), encoding="utf-8")
    except OSError:
        pass
    return path


def _clear_inflight(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _recover_lost_runs(store, session_store, state_dir: Path,
                       mcp_bridge_url, peers_path: Path | None = None) -> None:
    """Every marker whose run this process is not running belongs to a
    process that is gone. `inflight.json` is the single marker the one-
    session build wrote, still read so a deploy mid-run loses nothing."""
    markers = sorted((Path(state_dir) / "inflight").glob("*.json"))
    legacy = Path(state_dir) / "inflight.json"
    if legacy.exists():
        markers.append(legacy)
    for path in markers:
        try:
            lost = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _clear_inflight(path)
            continue
        uuid = lost.get("task_uuid") if isinstance(lost, dict) else None
        with _RUNNING_LOCK:
            if uuid in _RUNNING:
                continue
        _clear_inflight(path)
        task = store.get(uuid or "")
        if task is None or task.status != "pending":
            continue
        why = (f"infrastructure: run lost -- pu stopped mid-session "
               f"(started {lost.get('started_at')})")
        # A lost run is an infrastructure failure: retried once, counted.
        outcome, detail = _fail(store, task, why, mcp_bridge_url)
        _record_run(session_store, peers_path, task,
                    lost.get("session_type") or "unknown", "",
                    runner_mod.SessionResult(exit_code=-1), "lost", detail,
                    "infrastructure")
        logs_client.record_event(
            "run_lost", "task", task.uuid, why, title=task.description,
            detail={"session_type": lost.get("session_type"),
                    "started_at": lost.get("started_at")},
            peers_path=peers_path)


# -- the tick -------------------------------------------------------------


def tick(
    store: taskstore.TaskStore,
    body_store: bodies.BodyStore,
    session_store: sessions_mod.SessionStore,
    unit_root: Path,
    peers_path: Path,
    cost_policy_path: Path,
    session_runner: Callable[..., runner_mod.SessionResult] = runner_mod.run_session,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    tracker_path: Path | None = None,
    repeat_threshold: int = guards.DEFAULT_REPEAT_THRESHOLD,
    stale_claim_seconds: int = guards.DEFAULT_STALE_CLAIM_SECONDS,
    mcp_bridge_url: str | None = None,
    type_store=None,
    trigger_store=None,
) -> TickResult:
    """Do at most one unit of work, and write down what was decided."""
    tracker_path = tracker_path or (unit_root / "state" / "repeat_tracker.json")
    if not _TICK_LOCK.acquire(blocking=False):
        result = TickResult(ran=False, reason="another tick is deciding")
    else:
        try:
            decided = _tick(
                store, body_store, session_store, unit_root, peers_path,
                cost_policy_path, session_runner, now, tracker_path,
                repeat_threshold, stale_claim_seconds, mcp_bridge_url,
                type_store, trigger_store,
            )
        finally:
            _TICK_LOCK.release()
        if isinstance(decided, _Launch):
            try:
                result = dataclasses.replace(decided.run(),
                                             emitted=decided.emitted)
            finally:
                # On every exit a live process makes. Only a process that
                # dies mid-run leaves its marker behind -- which is what
                # makes a marker with no registered run mean "lost".
                with _RUNNING_LOCK:
                    _RUNNING.pop(decided.task_uuid, None)
                _clear_inflight(decided.marker)
        else:
            result = decided
    control.note_tick(tracker_path.parent, result.as_dict())
    return result


def _tick(
    store, body_store, session_store, unit_root, peers_path, cost_policy_path,
    session_runner, now, tracker_path, repeat_threshold, stale_claim_seconds,
    mcp_bridge_url, type_store=None, trigger_store=None,
) -> TickResult:
    moment = now()

    # Before the gates: silence is about whether ticks happened at all,
    # and a blocked tick still happened.
    gap = control.silence(tracker_path.parent, moment, control.heartbeat_seconds())
    if gap is not None:
        notify.notify_owner(
            mcp_bridge_url,
            f"pu was not ticked for {int(gap // 60)} min. Nothing ran in that "
            f"time; schedules that came due fire late now, once each.",
            source_unit=UNIT_NAME)

    _recover_lost_runs(store, session_store, tracker_path.parent, mcp_bridge_url,
                       peers_path)
    for stale in guards.newly_stale(
            store, tracker_path.parent / "stale_claims.json",
            stale_claim_seconds, moment):
        notify.notify_owner(
            mcp_bridge_url,
            f"A trigger-created task has been claimed for over a day and "
            f"nothing is working it.\n\n{stale.description}\ntask "
            f"{stale.uuid}\n\npu does not release claims. Release it "
            f"(task {stale.uuid} stop) or resolve it.",
            source_unit=UNIT_NAME)
    guards.prune_runs(store, tracker_path.parent / "prune.json", moment)
    session_store.purge_streams(30, moment)
    _daily_audit(store, tracker_path.parent / "audit.json", moment,
                 mcp_bridge_url)

    policy = read_cost_policy(cost_policy_path)
    blocked = check_cost_gate(policy, session_store, moment.strftime("%Y-%m-%d"),
                              reserved=reserved_cost(session_store))
    cost_state = tracker_path.parent / "cost_gate.json"
    if blocked != usage_gate.load_blocked_state(cost_state):
        logs_client.record_event(
            "gate_blocked" if blocked else "gate_cleared", "unit", "pu",
            blocked or "the daily cost cap no longer blocks",
            detail={"gate": "cost"}, peers_path=peers_path)
        if blocked:
            notify.notify_owner(mcp_bridge_url, f"pu stopped starting sessions: "
                                f"{blocked}.", source_unit=UNIT_NAME)
        usage_gate.save_blocked_state(cost_state, blocked)
    if blocked:
        return TickResult(ran=False, reason=blocked)

    # The account-usage ceilings, in the same place and for the same
    # reason as the cost gate above: before selection, on every path in.
    # See holonic-node/docs/UNIT_STANDARDS.md, "Account-usage ceilings".
    usage_blocked = usage_gate.check_usage_gate(
        usage_gate.read_usage(unit_root / usage_gate.USAGE_FILENAME),
        usage_gate.ceilings_from_env(),
    )
    # Reported on the transition rather than every tick. Blocked is a
    # state that can last hours, and a message per tick is one every few
    # minutes -- noise that teaches its own reader to skip it.
    state_path = tracker_path.parent / "usage_gate.json"
    previous = usage_gate.load_blocked_state(state_path)
    message = usage_gate.blocked_transition(previous, usage_blocked)
    if message is not None:
        notify.notify_owner(mcp_bridge_url, message, source_unit=UNIT_NAME)
        usage_gate.save_blocked_state(state_path, usage_blocked)
        logs_client.record_event(
            "gate_blocked" if usage_blocked else "gate_cleared", "unit", "pu",
            usage_blocked or "account usage back under its ceilings",
            detail={"gate": "usage"}, peers_path=peers_path)
    if usage_blocked:
        return TickResult(ran=False, reason=usage_blocked)

    # Step 4. Past the gates, so a blocked unit has changed nothing; before
    # selection, so work a trigger just created is selectable on this tick.
    emitted = emit_due_work(trigger_store, store, body_store, now)

    # Trigger work that waited past its stale_after is not worth starting.
    # Swept here, whether or not sessions are switched on: it goes stale
    # either way.
    _expire_stale(store, moment, mcp_bridge_url, tracker_path.parent)

    # The kill switch. After triggers, so work keeps being created and
    # nothing is lost while paused; before selection, so nothing launches.
    pause = control.paused(tracker_path.parent)
    if pause:
        return TickResult(
            ran=False, emitted=emitted,
            reason=f"paused since {pause.get('since')}"
                   + (f": {pause['reason']}" if pause.get("reason") else ""))

    # The ceiling, checked inside the lock like the gates: a launch decided
    # here is a launch counted here.
    ceiling = _ceiling(policy, session_store, moment.strftime("%Y-%m-%d"),
                       tracker_path.parent, mcp_bridge_url,
                       usage_gate.read_usage(unit_root / usage_gate.USAGE_FILENAME),
                       usage_gate.ceilings_from_env())
    with _RUNNING_LOCK:
        running = dict(_RUNNING)
    if len(running) >= ceiling:
        return TickResult(
            ran=False, emitted=emitted,
            reason=f"at the concurrency ceiling: {len(running)} running, "
                   f"at most {ceiling}")
    busy_repos = {r["repo"] for r in running.values() if r.get("repo")}

    try:
        types = session_types_mod.discover(
            unit_root / "session_types", store=type_store)
    except session_types_mod.SessionTypeError as exc:
        return TickResult(ran=False, reason=str(exc), emitted=emitted)

    for candidate in store.runnable():
        # A runtime-authored type claiming this task's kind and tags wins
        # over the kind's default. Runnability itself is unchanged and still
        # default-deny through `records.SESSION_TYPE_FOR_KIND`: a claim
        # chooses *which* session runs an already-runnable task, and cannot
        # make a new kind runnable.
        stype = session_types_mod.for_task(types, candidate)
        if stype is None:
            # A kind whose session type has no directory. Skipped and said
            # out loud rather than silently passed over.
            continue

        # A role that has left the gateway's registry takes its type with
        # it: switched off, owner told, nothing runs under a role that no
        # longer means anything. The last copy stands in for a gateway
        # that does not answer, so a blip stops nothing.
        exists, role_description = roles_mod.at_launch(
            stype.agent_role, mcp_bridge_url, tracker_path.parent)
        if not exists:
            if type_store is not None and stype.record is not None:
                type_store.append(dict(stype.record, enabled=False))
                logs_client.record_event(
                    "type_deactivated", "session_type", stype.name,
                    f"agent role {stype.agent_role!r} left the gateway's registry",
                    peers_path=peers_path)
            notify.notify_owner(
                mcp_bridge_url,
                f"pu switched off session type {stype.name}: its agent role "
                f"{stype.agent_role!r} is no longer in the gateway's registry."
                f"\n\nNothing ran. Give it an existing role and activate it "
                f"again.", source_unit=UNIT_NAME)
            continue

        # One session per target repository, whatever the ceiling: a session
        # is handed the repo itself as cwd, so two would share a tree, an
        # index and a branch. Checked before the repeat breaker counts a
        # selection, so waiting for a repo is never mistaken for being stuck.
        if stype.runs_in_target_repo and _repo_key(candidate.repo) in busy_repos:
            continue

        # Winning selection repeatedly means it is not resolving: a
        # resolved task is closed and cannot win again. Counted here,
        # before any work, so a session that fails to launch counts too.
        seen = guards.note_selection(tracker_path, candidate.uuid)
        if seen >= repeat_threshold:
            reason = (f"selected {seen} ticks running without resolving; a "
                      f"person needs to look at this")
            guards.block(store, candidate.uuid, reason)
            guards.clear_tracker(tracker_path)
            # Blocking *means* "this needs a person". Saying so only on the
            # task itself makes that true and unheard.
            notify.notify_owner(
                mcp_bridge_url,
                f"pu blocked a task and needs a person.\n\n"
                f"{candidate.description}\n{reason}\ntask {candidate.uuid}",
                source_unit=UNIT_NAME,
            )
            return TickResult(
                ran=False, reason="blocked after repeated dispatch",
                task_uuid=candidate.uuid, session_type=stype.name,
                outcome="auto_blocked", emitted=emitted,
            )

        if stype.runs_in_target_repo:
            if not candidate.repo:
                store.annotate(
                    candidate.uuid,
                    "skipped: an execution task needs a `repo` to run in",
                )
                continue
            try:
                report = repo_setup.prepare(candidate.repo, stype.dir)
            except repo_setup.RepoError as exc:
                store.annotate(candidate.uuid, f"skipped: {exc}")
                continue
            cwd = Path(report.repo)
            # cwd is the repo, so this type's own CLAUDE.md no longer loads
            # from it. Folded into the prompt rather than passed as
            # --append-system-prompt: it is multi-line, and a multi-line
            # argv value is truncated by a batch shim. See runner.py.
            instructions = stype.instructions()
        else:
            cwd = stype.dir
            instructions = ""

        # Claim before work, still inside the lock: the claim is what takes
        # the task off every other tick's queue.
        store.claim(candidate.uuid)
        marker = _write_inflight(tracker_path.parent, candidate, stype)
        with _RUNNING_LOCK:
            _RUNNING[candidate.uuid] = {
                "repo": (_repo_key(candidate.repo)
                         if stype.runs_in_target_repo else None),
                "session_type": stype.name,
                "agent_role": stype.agent_role,
                "started_at": datetime.now(timezone.utc).isoformat()}
        return _Launch(
            run=functools.partial(
                _run_claimed, store, body_store, session_store, unit_root,
                peers_path, candidate, stype, cwd, instructions,
                session_runner, mcp_bridge_url, role_description,
                mcpstore.McpStore(type_store.root)
                if type_store is not None else None),
            task_uuid=candidate.uuid, marker=marker, emitted=emitted)

    return TickResult(ran=False, reason="nothing runnable", emitted=emitted)


def _run_claimed(
    store, body_store, session_store, unit_root, peers_path,
    task, stype, cwd, instructions, session_runner, mcp_bridge_url,
    role_description="", mcp_store=None,
) -> TickResult:
    started = datetime.now(timezone.utc)
    queued = _queued_seconds(task, started)

    def record(result, outcome, detail, failure_class=None):
        _record_run(session_store, peers_path, task, stype, prompt, result,
                    outcome, detail, failure_class, queued)

    prompt = build_prompt(task, body_store.get(task.uuid), unit_root,
                          agent_role=stype.agent_role,
                          role_description=role_description)
    if instructions:
        prompt = "\n\n---\n\n".join([instructions, prompt])

    bridge = mcp_bridge_url if stype.reaches_peers else None
    extra: dict[str, Any] = {}
    if stype.mcp_servers:
        try:
            extra = {
                "mcp_servers": stype.mcp_servers,
                "mcp_config_path": _write_mcp_config(
                    stype, mcp_store, bridge),
            }
        except mcpstore.McpStoreError as exc:
            outcome, detail = _fail(store, task, f"infrastructure: {exc}",
                                    mcp_bridge_url)
            record(runner_mod.SessionResult(exit_code=-1), "launch_failed",
                   str(exc), "infrastructure")
            return TickResult(ran=False, reason="mcp config failed",
                              task_uuid=task.uuid, session_type=stype.name,
                              outcome=outcome, detail=str(exc))

    try:
        result = session_runner(
            prompt=prompt,
            cwd=cwd,
            allowed_tools=stype.allowed_tools,
            model=stype.model,
            # Holonic is attached to every queue session (hard-coded for
            # now); see session_types.NO_PEER_REACH.
            mcp_bridge_url=bridge,
            **extra,
        )
    except Exception as exc:  # a launch failure must not strand the claim
        outcome, detail = _fail(
            store, task, f"infrastructure: session failed to launch: {exc}",
            mcp_bridge_url)
        # A launch that never happened is still an attempt: recorded, or
        # the analytics undercount failures in exactly the case that most
        # needs counting.
        record(runner_mod.SessionResult(exit_code=-1), "launch_failed",
               str(exc), "infrastructure")
        return TickResult(
            ran=False, reason="launch failed", task_uuid=task.uuid,
            session_type=stype.name,
            outcome="launch_failed" if outcome == "failed" else outcome,
            detail=str(exc),
        )

    outcome, detail = _settle(store, task, result, mcp_bridge_url)
    failure_class = None
    if not result.ok or not (result.text or "").strip():
        failure_class = "infrastructure"
    elif report_mod.parse(result.text).outcome == "failed":
        failure_class = "agent"
    record(result, outcome, detail, failure_class)
    # What this session saw of the account's rolling windows, back to the
    # gateway, which records it once for the whole node. Best-effort: the
    # session has already succeeded and a bridge that is down must not
    # turn that into a failure.
    usage_gate.report_usage(mcp_bridge_url, result.rate_limit_windows)

    return TickResult(
        ran=True, task_uuid=task.uuid, session_type=stype.name,
        outcome=outcome, detail=detail, cost_usd=result.cost_usd,
    )


def _queued_seconds(task: records.Task, started: datetime) -> float | None:
    """How long the task waited between being created and its session
    starting -- the number that says whether the ceiling is too low."""
    try:
        entry = datetime.strptime(task.entry or "", "%Y%m%dT%H%M%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None
    return max(0.0, round((started - entry).total_seconds(), 3))


def _record_run(session_store, peers_path, task, stype, prompt, result,
                outcome, detail, failure_class=None, queued=None) -> None:
    """Every attempt, recorded the same way: the local run record (pu's
    working copy, which its gates and stats read) and a session_run entry
    in mu-logs (the system's record, which au analyses). Header measured
    here, never self-reported."""
    name = stype if isinstance(stype, str) else stype.name
    role = (session_types_mod.DEFAULT_AGENT_ROLE if isinstance(stype, str)
            else stype.agent_role)
    version = None if isinstance(stype, str) else stype.version
    report = report_mod.parse(result.text)
    started_by = f"trigger:{task.trigger}" if task.trigger else "task"
    header = {
        "session_type_version": version,
        "started_by": started_by,
        "failure_class": failure_class,
        "queued_seconds": queued,
        "priority": task.priority,
    }
    session_store.record(task.uuid, name, prompt, result, outcome, detail,
                         agent_role=role, extra={**header, **report.as_record()})
    logs_client.record_session_run(
        peers_path,
        source_unit=UNIT_NAME,
        outcome=outcome,
        duration_seconds=(result.duration_ms / 1000.0) if result.duration_ms else None,
        cost=result.cost_usd,
        extra={
            "session_type": name, "task_uuid": task.uuid, "agent_role": role,
            "trigger": task.trigger, "tokens": dict(result.tokens),
            "num_turns": result.num_turns, "report_outcome": report.outcome,
            "needs_human": report.needs_human, **header,
        },
    )


# -- triggers: a fired trigger creates a task, and nothing else -----------
#
# The whole emitter. There is no event log, no event-name binding and no
# `record_event` tool, and each of those was a settled design that the
# 2026-09-22 amendment deleted -- a task is already a durable record with a
# status, an atomic claim and annotations, and `kind` plus tags already bind
# work to the session type that claims it. Building any of them back is a
# regression, not an addition.
#
# Where this sits in the tick is load-bearing: **after the gates, before
# selection.** After, so a blocked unit changes no state at all rather than
# quietly accumulating a night's worth of tasks from a misfiring watch.
# Before, so a trigger that fires can have its work selected on the same
# tick instead of waiting for the next one.


def payload_body(trigger: dict, evaluation) -> str:
    """The task's body: why it exists, then the payload, labelled.

    The payload is a separate labelled section rather than prose woven
    around it, and a schedule's empty payload is *stated* rather than
    omitted -- a session that cannot tell "nothing was fetched" from
    "fetching was not part of this" has to guess, and it will guess wrong
    about whether to go and look.
    """
    source = trigger["source"]
    lines = [
        "## Why this exists",
        "",
        f"The trigger `{trigger['name']}` fired.",
    ]
    if trigger.get("purpose"):
        lines += ["", trigger["purpose"]]

    if source["kind"] == "schedule":
        if source.get("cron"):
            cadence = (f"on `{source['cron']}`"
                       f" ({source.get('tz') or 'UTC'})")
        else:
            cadence = f"every {source['every_seconds']}s"
        lines += [
            "", "## Payload", "",
            f"None. This is a schedule, firing {cadence}; it watches no "
            f"source, so there is nothing to carry and nothing failed to "
            f"arrive.",
        ]
    else:
        lines += [
            "", f"## Payload — {len(evaluation.items)} item(s) from "
            f"{source['kind']} `{source['url']}`", "",
            "```json",
            json.dumps(list(evaluation.items), indent=2, ensure_ascii=False),
            "```",
        ]
    return "\n".join(lines) + "\n"


def emit_due_work(
    trigger_store, store: taskstore.TaskStore, body_store: bodies.BodyStore,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    fetch=None,
) -> tuple[dict[str, Any], ...]:
    """Evaluate every enabled trigger; create a task for each that fired.

    Returns one record per trigger that did something, for the tick to
    report. A trigger that raises does not stop the others: one unusable
    regex would otherwise silence every watch in the unit.
    """
    if trigger_store is None:
        return ()

    emitted: list[dict[str, Any]] = []
    for trigger in trigger_store.enabled():
        name = trigger["name"]
        cursor = trigger_store.cursor(name)
        moment = now()
        # A watch fetches at most every source.every_seconds, and not at
        # all while a source has told it to back off. Skipped silently: a
        # watch that is simply not due is not news.
        due, _ = triggers.fetch_due(trigger["source"], cursor, moment,
                                    floor=control.heartbeat_seconds())
        if not due:
            continue
        attempt: dict[str, Any] = {}
        fired_items = None

        def recording(url, _fetch=fetch or triggers.http_fetch,
                      _attempt=attempt):
            result = _fetch(url)
            _attempt["status"] = result[0]
            _attempt["retry_after"] = result[2] if len(result) > 2 else None
            return result

        try:
            evaluation = triggers.evaluate(trigger, cursor, now=now,
                                           fetch=recording)
        except triggers.TriggerError as exc:
            # ponytail: reported in the tick result and nowhere else, so a
            # permanently broken trigger is visible only to whoever reads a
            # tick. Notify on the transition into broken -- the usage gate's
            # pattern -- once anything depends on a watch staying up.
            emitted.append({"trigger": name, "error": str(exc)})
            continue

        if evaluation.fired and trigger.get("emits"):
            emits = trigger["emits"]
            tags = list(emits.get("tags") or ())
            if emits.get("afk"):
                tags.append(records.AFK_TAG)
            # The task is created **before** the cursor advances. A crash
            # between the two re-creates on the next tick, which is the
            # at-least-once model this unit already owns; the other order
            # marks the items seen and loses the work with nobody the
            # wiser.
            # Built before the task exists: anything that raises after
            # `add` and before the cursor advances re-creates the task on
            # every tick. A cron schedule did exactly that once, through a
            # KeyError in here.
            body = payload_body(trigger, evaluation)
            extra_fields = {}
            if emits.get("priority"):
                extra_fields["priority"] = records.check_priority(
                    emits["priority"])
            stale = emits.get("stale_after_seconds")
            if stale is None and emits.get("priority") == "high":
                # High priority means "meant for a time"; run hours late it
                # is the wrong run. An hour unless the trigger says otherwise.
                stale = DEFAULT_HIGH_STALE_SECONDS
            if stale:
                extra_fields["expires"] = (moment + timedelta(
                    seconds=float(stale))).isoformat()
            uuid = store.add(
                triggers.fill_title(
                    emits.get("title") or f"{name}: {evaluation.reason}",
                    evaluation.items[0] if evaluation.items else None),
                tags=tags,
                kind=emits["kind"],
                # The run namespace unless the trigger names a project of
                # its own: a schedule firing daily would otherwise put a
                # year of run-tasks into every task view.
                project=emits.get("project") or records.RUNS_PROJECT,
                trigger=name,
                **extra_fields,
            )
            body_store.set(uuid, body)
            emitted.append({"trigger": name, "task_uuid": uuid,
                            "items": len(evaluation.items)})
            fired_items = len(evaluation.items)
        elif evaluation.fired:
            # Fired with nothing to emit. Recorded rather than silent: a
            # trigger with no `emits` is almost certainly half-written, and
            # the cursor still advances so it will not re-report.
            emitted.append({"trigger": name, "fired": True,
                            "note": "no emits, so no task was created"})

        updated = triggers.advanced(cursor, evaluation, now=now)
        # Carried over the advance, which rebuilds the cursor from its own
        # four fields and would otherwise drop the history stats reads.
        for key in ("fetched_at", "backoff_seconds", "backoff_until",
                    "fetches", "fetch_failures", "backoffs",
                    "backoff_seconds_total", "consecutive_failures",
                    "fires", "items_total", "items_max"):
            if key in cursor:
                updated.setdefault(key, cursor[key])
        if fired_items is not None:
            updated["fires"] = int(cursor.get("fires") or 0) + 1
            updated["items_total"] = int(cursor.get("items_total") or 0) + fired_items
            updated["items_max"] = max(int(cursor.get("items_max") or 0), fired_items)
        if "status" in attempt:
            updated = triggers.after_fetch(updated, moment, attempt["status"],
                                           attempt["retry_after"])
            if updated.get("backoff_until") not in (None, cursor.get("backoff_until")):
                why = (f"the source refused (status {attempt['status']}); "
                       f"backing off until {updated['backoff_until']}")
                emitted.append({"trigger": name, "error": why})
                logs_client.record_event("backoff_started", "trigger", name, why,
                                         detail={"status": attempt["status"]})
            elif updated.get("consecutive_failures") == 1:
                logs_client.record_event(
                    "fetch_failed", "trigger", name,
                    f"fetching {trigger['source'].get('url')} did not succeed "
                    f"(status {attempt['status']})",
                    detail={"status": attempt["status"]})
        if updated != cursor:
            trigger_store.save_cursor(name, updated)
    return tuple(emitted)
