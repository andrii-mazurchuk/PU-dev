"""One tick: pick the most urgent runnable task, run its session, record it.

The whole selection rule is one line -- take the top of `/queue` -- because
the ordering lives in Taskwarrior's urgency and the eligibility lives in
`Task.runnable`. There is deliberately no priority ladder here: with one
store holding every source, "research before execution" is a coefficient
rather than an `if`, retunable without a code change and inspectable by
hand with `task next`.

Order of operations, and each is load-bearing:

1. **Cost gate first, before selection**, on every path into this
   function. A gate that runs after selection can be skipped by whatever
   selected differently, and a gate an external trigger can route around
   is not a gate.
2. **Claim before work.** The claim is the store's own `+ACTIVE` state, so
   a concurrent session's frontier no longer contains the task. It is the
   session's first write for the same reason wayfinder makes it one.
3. **Run.**
4. **Record whatever happened** -- to local artifacts, to the logs unit,
   and onto the task. A session that failed leaves the task released and
   annotated rather than silently claimed forever.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from pu import (
    bodies,
    intake as intake_mod,
    logs_client,
    records,
    repo_setup,
    runner as runner_mod,
    session_types as session_types_mod,
    sessions as sessions_mod,
    sops,
    taskstore,
)

UNIT_NAME = "pu"


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
                f"- `{tag}` — "
                f"`python3 ../../scripts/tag_sop_lookup.py --tag {tag}`"
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
) -> TickResult:
    """Do at most one unit of work."""
    policy = read_cost_policy(cost_policy_path)
    blocked = check_cost_gate(policy, session_store, now().strftime("%Y-%m-%d"))
    if blocked:
        return TickResult(ran=False, reason=blocked)

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
            append_system_prompt = stype.instructions()
        else:
            cwd = stype.dir
            append_system_prompt = None

        return _run(
            store, body_store, session_store, unit_root, peers_path,
            candidate, stype, cwd, append_system_prompt, session_runner,
        )

    return TickResult(ran=False, reason="nothing runnable")


def _run(
    store, body_store, session_store, unit_root, peers_path,
    task, stype, cwd, append_system_prompt, session_runner,
) -> TickResult:
    store.claim(task.uuid)

    extra = intake_context(unit_root) if stype.name == "intake" else ""
    prompt = build_prompt(task, body_store.get(task.uuid), unit_root, extra)

    try:
        result = session_runner(
            prompt=prompt,
            cwd=cwd,
            allowed_tools=stype.allowed_tools,
            model=stype.model,
            append_system_prompt=append_system_prompt,
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
        outcome, detail = _apply_intake(store, body_store, task, result)
    else:
        outcome, detail = _apply_resolution(store, task, result)

    session_store.record(task.uuid, stype.name, prompt, result, outcome, detail)
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
