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
2. **Collect stale claims**, before anything reads the queue -- a claim
   left by a process that died is otherwise invisible forever.
3. **Cost gate before selection**, on every path into this function. A
   gate that runs after selection can be skipped by whatever selected
   differently, and a gate an external trigger can route around is not a
   gate.
4. **Claim before work.** The claim is the store's own `+ACTIVE` state, so
   a concurrent session's frontier no longer contains the task. It is the
   session's first write for the same reason wayfinder makes it one.
5. **Run.**
6. **Record whatever happened** -- to local artifacts, to the logs unit,
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
    guards,
    intake as intake_mod,
    logs_client,
    notify,
    records,
    repo_setup,
    runner as runner_mod,
    session_types as session_types_mod,
    sessions as sessions_mod,
    sops,
    taskstore,
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


# -- context assembly -----------------------------------------------------


def build_prompt(
    task: records.Task,
    body: str | None,
    unit_root: Path,
    extra_context: str = "",
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

    return "\n".join(lines)


def intake_context(unit_root: Path) -> str:
    """The SOP catalogue, for the one session that assigns tags.

    Given up front rather than looked up: intake has to choose tags for
    tasks it proposes, and it cannot choose from a set it does not know
    exists."""
    catalogue = sops.catalogue(unit_root)
    if not catalogue:
        return (
            "## Available procedures\n\n"
            "There are no SOPs defined yet. Propose tasks without procedure "
            "tags."
        )
    lines = ["## Available procedures", "",
             "These are the tags that carry a binding procedure. Attach the "
             "ones that apply; do not invent others.", ""]
    for entry in catalogue:
        kinds = f" (kinds: {', '.join(entry['kinds'])})" if entry["kinds"] else ""
        lines.append(f"- `{entry['tag']}`{kinds} — {entry['description']}")
    return "\n".join(lines)


# -- outcome handling -----------------------------------------------------


def _apply_intake(
    store: taskstore.TaskStore,
    body_store: bodies.BodyStore,
    task: records.Task,
    result: runner_mod.SessionResult,
    mcp_bridge_url: str | None = None,
) -> tuple[str, str]:
    """Convert an intake session's proposal into tasks, or record a bounce.

    A bounce is a normal outcome, not a failure: the message was read and
    the answer is that it cannot become work as written."""
    try:
        plan = intake_mod.validate(intake_mod.extract_block(result.text))
    except intake_mod.IntakeError as exc:
        store.annotate(task.uuid, f"intake could not be applied: {exc}")
        store.release(task.uuid)
        return "invalid_proposal", str(exc)

    if "bounce" in plan:
        bounce = plan["bounce"]
        store.annotate(
            task.uuid, f"bounced ({bounce['condition']}): {bounce['reason']}"
        )
        store.complete(task.uuid)
        # A bounce is a finished answer, but only pu has heard it: the
        # record closes and whoever sent the message is never told what was
        # missing. Best-effort, so a silent bounce beats a failed tick.
        notify.notify_owner(
            mcp_bridge_url,
            f"pu bounced an inbox message ({bounce['condition']}): "
            f"{bounce['reason']}\n\nThe message was: {task.description}",
            source_unit=UNIT_NAME,
        )
        return "bounced", f"{bounce['condition']}: {bounce['reason']}"

    created = intake_mod.apply(store, body_store, plan)
    store.annotate(task.uuid, f"converted into {len(created)} task(s): "
                              f"{', '.join(created)}")
    store.complete(task.uuid)
    return "converted", f"{len(created)} task(s) created"


def _apply_resolution(
    store: taskstore.TaskStore, task: records.Task, result: runner_mod.SessionResult
) -> tuple[str, str]:
    """For every other session type: the final message is the answer."""
    answer = (result.text or "").strip()
    if not answer:
        store.annotate(task.uuid, "session produced no answer")
        store.release(task.uuid)
        return "empty", "session produced no answer"
    store.annotate(task.uuid, answer)
    store.complete(task.uuid)
    return "resolved", answer[:200]


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
) -> TickResult:
    """Do at most one unit of work."""
    if not _TICK_LOCK.acquire(blocking=False):
        return TickResult(ran=False, reason="a tick is already running")
    try:
        return _tick(
            store, body_store, session_store, unit_root, peers_path,
            cost_policy_path, session_runner, now,
            tracker_path or (unit_root / "state" / "repeat_tracker.json"),
            repeat_threshold, stale_claim_seconds, mcp_bridge_url,
        )
    finally:
        _TICK_LOCK.release()


def _tick(
    store, body_store, session_store, unit_root, peers_path, cost_policy_path,
    session_runner, now, tracker_path, repeat_threshold, stale_claim_seconds,
    mcp_bridge_url,
) -> TickResult:
    moment = now()

    # Before anything reads the queue: a claim left behind by a process
    # that died is otherwise invisible to every future tick.
    guards.release_stale_claims(store, stale_claim_seconds, moment)

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

    try:
        types = session_types_mod.discover(unit_root / "session_types")
    except session_types_mod.SessionTypeError as exc:
        return TickResult(ran=False, reason=str(exc))

    for candidate in store.runnable():
        stype = types.get(candidate.session_type or "")
        if stype is None:
            # A kind whose session type has no directory. Skipped and said
            # out loud rather than silently passed over.
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
                outcome="auto_blocked",
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

        return _run(
            store, body_store, session_store, unit_root, peers_path,
            candidate, stype, cwd, instructions, session_runner,
            mcp_bridge_url,
        )

    return TickResult(ran=False, reason="nothing runnable")


def _run(
    store, body_store, session_store, unit_root, peers_path,
    task, stype, cwd, instructions, session_runner, mcp_bridge_url,
) -> TickResult:
    store.claim(task.uuid)

    extra = intake_context(unit_root) if stype.name == "intake" else ""
    prompt = build_prompt(task, body_store.get(task.uuid), unit_root, extra)
    if instructions:
        prompt = "\n\n---\n\n".join([instructions, prompt])

    try:
        result = session_runner(
            prompt=prompt,
            cwd=cwd,
            allowed_tools=stype.allowed_tools,
            model=stype.model,
            # Gated on the type, not merely on the URL being configured:
            # passing one grants `mcp__mcp-bridge__*`, every tool every peer
            # exposes. See session_types.REACHES_PEERS for why intake is out.
            mcp_bridge_url=mcp_bridge_url if stype.reaches_peers else None,
        )
    except Exception as exc:  # a launch failure must not strand the claim
        store.release(task.uuid)
        store.annotate(task.uuid, f"session failed to launch: {exc}")
        return TickResult(
            ran=False, reason="launch failed", task_uuid=task.uuid,
            session_type=stype.name, outcome="launch_failed", detail=str(exc),
        )

    if not result.ok:
        store.release(task.uuid)
        store.annotate(task.uuid, f"session failed (exit {result.exit_code})")
        outcome, detail = "failed", f"exit {result.exit_code}"
    elif stype.name == "intake":
        outcome, detail = _apply_intake(
            store, body_store, task, result, mcp_bridge_url
        )
    else:
        outcome, detail = _apply_resolution(store, task, result)

    session_store.record(task.uuid, stype.name, prompt, result, outcome, detail)
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
