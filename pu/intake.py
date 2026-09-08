"""Turning an intake session's answer into tasks, or into a bounce.

Intake is the only session that causes work to exist, so it is the one
place the unit's central invariant is at risk. Two things protect it.

**The session proposes; this module writes.** The session has no write
access to the store at all. It returns a structured block, which is
validated against the closed vocabulary here before anything is created.
A session cannot write a task PU did not approve -- which is a much
stronger property than trusting it to call the right endpoint.

**All or none.** The whole proposal is validated first, and if a write
fails partway the tasks already created are removed. A malformed proposal
creates nothing, rather than half a plan that someone has to reconcile by
hand.

The judgement that remains -- what the message meant, how to split it,
whether an agent may do it alone -- lives in the session's own `CLAUDE.md`,
where a human can read and edit it. Nothing in this file decides anything;
it validates a shape and performs writes.
"""

from __future__ import annotations

import json
from typing import Any

from pu import bodies, records, taskstore

# The reasons a message does not become work. A closed list, so that
# "intake could not do anything with this" is always a specific, answerable
# statement rather than a shrug.
BOUNCE_CONDITIONS = (
    "no_stateable_outcome",   # never says what would be true when done
    "no_placeable_target",    # no project or repo it belongs to
    "needs_a_map",            # resolving it means choosing between options
    "material_ambiguity",     # two readings, two different pieces of work
)


class IntakeError(ValueError):
    """The proposal could not be used. Recorded against the intake record;
    nothing is written."""


def extract_block(text: str) -> dict[str, Any]:
    """Find the proposal in the session's final message.

    Prefers a fenced ```json block and falls back to the outermost braces,
    because a model asked for JSON will often wrap it in a sentence. A
    missing or unparseable block raises -- guessing at what was meant is
    exactly the coercion this design refuses."""
    if not text or not text.strip():
        raise IntakeError("session returned no output")

    candidates: list[str] = []
    marker = "```json"
    start = text.find(marker)
    while start != -1:
        end = text.find("```", start + len(marker))
        if end == -1:
            # A truncated final fence is common enough to be worth
            # tolerating: take everything to the end.
            candidates.append(text[start + len(marker):])
            break
        candidates.append(text[start + len(marker):end])
        start = text.find(marker, end)

    first, last = text.find("{"), text.rfind("}")
    if first != -1 and last > first:
        candidates.append(text[first:last + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate.strip())
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise IntakeError("no JSON object found in the session's output")


def validate(proposal: dict[str, Any]) -> dict[str, Any]:
    """Check the whole proposal before any of it is written.

    Validated against a closed vocabulary and **raised on**, never coerced
    to a default. A silent default here would mean a task nobody asked for,
    of a kind nobody chose, entering a queue an agent then acts on."""
    if "bounce" in proposal:
        bounce = proposal["bounce"]
        if not isinstance(bounce, dict):
            raise IntakeError("bounce must be an object")
        condition = bounce.get("condition")
        if condition not in BOUNCE_CONDITIONS:
            raise IntakeError(
                f"unknown bounce condition {condition!r} "
                f"(allowed: {', '.join(BOUNCE_CONDITIONS)})"
            )
        if not str(bounce.get("reason") or "").strip():
            raise IntakeError("a bounce must carry a reason")
        return {"bounce": {"condition": condition, "reason": bounce["reason"]}}

    raw_tasks = proposal.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise IntakeError("proposal must contain a non-empty 'tasks' list, or a 'bounce'")

    validated: list[dict[str, Any]] = []
    for index, task in enumerate(raw_tasks):
        if not isinstance(task, dict):
            raise IntakeError(f"task {index}: not an object")

        title = str(task.get("title") or "").strip()
        if not title:
            raise IntakeError(f"task {index}: title is required")

        # Raises on anything outside the closed list.
        kind = records.check_kind(task.get("kind"))
        if kind is None:
            raise IntakeError(f"task {index}: kind is required")

        # Every proposed task must point at what in the message produced
        # it. This is the mechanical half of "intake transcribes, it does
        # not invent" -- a task with nothing to trace to is one the
        # session made up.
        traces_to = str(task.get("traces_to") or "").strip()
        if not traces_to:
            raise IntakeError(f"task {index}: traces_to is required")

        tags = task.get("tags") or []
        if not isinstance(tags, list) or any(not isinstance(t, str) for t in tags):
            raise IntakeError(f"task {index}: tags must be a list of strings")

        validated.append({
            "title": title,
            "kind": kind,
            "project": task.get("project") or None,
            # Only `execution` is ever run in a repo, but this is accepted on
            # any kind rather than conditioned on one: a repository named for
            # a `task` is still a true fact about it, and making the session
            # infer which kinds we happen to run elsewhere would be coupling
            # it to our internals. Without this field an intake-created
            # execution task can never run -- the tick skips it for having no
            # repo -- while the session's own instructions tell it to bounce
            # `no_placeable_target` when it cannot identify one.
            "repo": str(task.get("repo") or "").strip() or None,
            "tags": [t.strip() for t in tags if t.strip()],
            "afk": bool(task.get("afk")),
            "body": task.get("body") or None,
            "traces_to": traces_to,
        })

    return {"tasks": validated}


def apply(
    store: taskstore.TaskStore,
    body_store: bodies.BodyStore,
    plan: dict[str, Any],
) -> list[str]:
    """Create the proposed tasks. All of them, or none.

    ponytail: rollback by deleting what was created. Taskwarrior has no
    transaction, and a compensating delete is the honest approximation --
    a deleted task is still visible under `status:deleted`, so a failed
    intake leaves a readable trace rather than a silent gap."""
    created: list[str] = []
    try:
        for task in plan["tasks"]:
            tags = list(task["tags"])
            if task["afk"]:
                tags.append(records.AFK_TAG)
            uuid = store.add(
                task["title"],
                tags=tags,
                kind=task["kind"],
                project=task["project"],
                # .get rather than [] because a hand-built plan is a
                # legitimate caller, and absent means the same as None here.
                repo=task.get("repo"),
            )
            created.append(uuid)
            body = task["body"]
            if body:
                body_store.set(uuid, body)
            store.annotate(uuid, f"from intake, traces to: {task['traces_to']}")
    except Exception:
        for uuid in created:
            try:
                store.run([uuid, "delete"])
            except taskstore.TaskError:
                pass
        raise
    return created
