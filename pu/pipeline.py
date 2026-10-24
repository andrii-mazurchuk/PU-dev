"""One tick: pick the most urgent runnable task, run its session, record it.

The whole selection rule is one line -- take the top of `/queue` -- because
the ordering lives in Taskwarrior's urgency and the eligibility lives in
`Task.runnable`. There is deliberately no priority ladder here: with one
store holding every source, "research before execution" is a coefficient
rather than an `if`, retunable without a code change and inspectable by
hand with `task next`.

Order of operations, and each is load-bearing:

1. **One tick at a time.** The server is threaded and the gateway pokes
   `/trigger` on a schedule, so a slow session and the next poke overlap
   sooner or later. A second tick declines rather than queueing: two
   sessions running at once is not a busier unit, it is two sessions
   racing for the same top-of-queue task.
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
import json
import threading
from datetime import datetime, timezone
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
    policy: dict[str, Any], session_store: sessions_mod.SessionStore, today: str
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
    spent = session_store.cost_since(today)
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
    cost_blocked = check_cost_gate(policy, session_store, today)

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


# -- crash recovery -------------------------------------------------------
#
# A run pu was in the middle of when it stopped. The stale-claim reaper
# would release the claim two hours later and say nothing about a run; this
# finds it on the very next tick, because the marker is written at the claim
# and removed on every exit a live process makes. The tick lock means no
# run of this process can be in flight while a tick starts, so a marker
# present then belongs to a process that is gone. (pu vision, 2026-10-24.)


def _write_inflight(path: Path, task: records.Task, stype) -> None:
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


def _clear_inflight(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _recover_lost_run(store, session_store, path: Path, mcp_bridge_url) -> None:
    try:
        lost = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    _clear_inflight(path)
    task = store.get(lost.get("task_uuid") or "") if isinstance(lost, dict) else None
    if task is None or task.status != "pending":
        return
    why = (f"infrastructure: run lost -- pu stopped mid-session "
           f"(started {lost.get('started_at')})")
    # A lost run is an infrastructure failure: retried once, counted.
    outcome, detail = _fail(store, task, why, mcp_bridge_url)
    session_store.record(
        task.uuid, lost.get("session_type") or "unknown", "",
        runner_mod.SessionResult(exit_code=-1), "lost", detail,
        agent_role=lost.get("agent_role") or "system",
        extra={"started_by": (f"trigger:{task.trigger}" if task.trigger
                              else "task")},
    )


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
        result = TickResult(ran=False, reason="a tick is already running")
    else:
        try:
            result = _tick(
                store, body_store, session_store, unit_root, peers_path,
                cost_policy_path, session_runner, now, tracker_path,
                repeat_threshold, stale_claim_seconds, mcp_bridge_url,
                type_store, trigger_store,
            )
        finally:
            _TICK_LOCK.release()
    control.note_tick(tracker_path.parent, result.as_dict())
    return result


def _tick(
    store, body_store, session_store, unit_root, peers_path, cost_policy_path,
    session_runner, now, tracker_path, repeat_threshold, stale_claim_seconds,
    mcp_bridge_url, type_store=None, trigger_store=None,
) -> TickResult:
    moment = now()

    # Before anything reads the queue: a claim left behind by a process
    # that died is otherwise invisible to every future tick.
    # Before the gates: silence is about whether ticks happened at all,
    # and a blocked tick still happened.
    gap = control.silence(tracker_path.parent, moment, control.heartbeat_seconds())
    if gap is not None:
        notify.notify_owner(
            mcp_bridge_url,
            f"pu was not ticked for {int(gap // 60)} min. Nothing ran in that "
            f"time; schedules that came due fire late now, once each.",
            source_unit=UNIT_NAME)

    inflight_path = tracker_path.parent / "inflight.json"
    _recover_lost_run(store, session_store, inflight_path, mcp_bridge_url)
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

    policy = read_cost_policy(cost_policy_path)
    blocked = check_cost_gate(policy, session_store, moment.strftime("%Y-%m-%d"))
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
    if usage_blocked:
        return TickResult(ran=False, reason=usage_blocked)

    # Step 4. Past the gates, so a blocked unit has changed nothing; before
    # selection, so work a trigger just created is selectable on this tick.
    emitted = emit_due_work(trigger_store, store, body_store, now)

    # The kill switch. After triggers, so work keeps being created and
    # nothing is lost while paused; before selection, so nothing launches.
    pause = control.paused(tracker_path.parent)
    if pause:
        return TickResult(
            ran=False, emitted=emitted,
            reason=f"paused since {pause.get('since')}"
                   + (f": {pause['reason']}" if pause.get("reason") else ""))

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
            notify.notify_owner(
                mcp_bridge_url,
                f"pu switched off session type {stype.name}: its agent role "
                f"{stype.agent_role!r} is no longer in the gateway's registry."
                f"\n\nNothing ran. Give it an existing role and activate it "
                f"again.", source_unit=UNIT_NAME)
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

        return dataclasses.replace(_run(
            store, body_store, session_store, unit_root, peers_path,
            candidate, stype, cwd, instructions, session_runner,
            mcp_bridge_url, inflight_path, role_description,
            mcpstore.McpStore(type_store.root) if type_store is not None else None,
        ), emitted=emitted)

    return TickResult(ran=False, reason="nothing runnable", emitted=emitted)


def _run(
    store, body_store, session_store, unit_root, peers_path,
    task, stype, cwd, instructions, session_runner, mcp_bridge_url,
    inflight_path, role_description="", mcp_store=None,
) -> TickResult:
    store.claim(task.uuid)
    _write_inflight(inflight_path, task, stype)
    try:
        return _run_claimed(
            store, body_store, session_store, unit_root, peers_path,
            task, stype, cwd, instructions, session_runner, mcp_bridge_url,
            role_description, mcp_store)
    finally:
        # Cleared on every way out this process survives. Only a process
        # that dies mid-run leaves it behind, which is exactly what makes
        # its presence at the next tick mean "lost".
        _clear_inflight(inflight_path)


def _run_claimed(
    store, body_store, session_store, unit_root, peers_path,
    task, stype, cwd, instructions, session_runner, mcp_bridge_url,
    role_description="", mcp_store=None,
) -> TickResult:

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
        return TickResult(
            ran=False, reason="launch failed", task_uuid=task.uuid,
            session_type=stype.name,
            outcome="launch_failed" if outcome == "failed" else outcome,
            detail=str(exc),
        )

    outcome, detail = _settle(store, task, result, mcp_bridge_url)

    session_store.record(
        task.uuid, stype.name, prompt, result, outcome, detail,
        agent_role=stype.agent_role,
        extra={
            # The header: measured here, never self-reported.
            "session_type_version": stype.version,
            "started_by": (f"trigger:{task.trigger}" if task.trigger
                           else "task"),
            # The body, read back.
            **report_mod.parse(result.text).as_record(),
        },
    )
    # What this session saw of the account's rolling windows, back to the
    # gateway, which records it once for the whole node. Best-effort: the
    # session has already succeeded and a bridge that is down must not
    # turn that into a failure.
    usage_gate.report_usage(mcp_bridge_url, result.rate_limit_windows)
    logs_client.record_session_run(
        peers_path,
        source_unit=UNIT_NAME,
        outcome=outcome,
        duration_seconds=(result.duration_ms / 1000.0) if result.duration_ms else None,
        cost=result.cost_usd,
        extra={"session_type": stype.name, "task_uuid": task.uuid},
    )

    return TickResult(
        ran=True, task_uuid=task.uuid, session_type=stype.name,
        outcome=outcome, detail=detail, cost_usd=result.cost_usd,
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
        try:
            kwargs = {"fetch": fetch} if fetch is not None else {}
            evaluation = triggers.evaluate(trigger, cursor, now=now, **kwargs)
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
            )
            body_store.set(uuid, body)
            emitted.append({"trigger": name, "task_uuid": uuid,
                            "items": len(evaluation.items)})
        elif evaluation.fired:
            # Fired with nothing to emit. Recorded rather than silent: a
            # trigger with no `emits` is almost certainly half-written, and
            # the cursor still advances so it will not re-report.
            emitted.append({"trigger": name, "fired": True,
                            "note": "no emits, so no task was created"})

        if evaluation.advance:
            trigger_store.save_cursor(
                name, triggers.advanced(cursor, evaluation, now=now))
    return tuple(emitted)
