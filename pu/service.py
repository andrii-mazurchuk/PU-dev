"""The operations, as plain functions over a store. No HTTP here.

There are exactly **two write doors** into this unit, and neither is
generic CRUD:

**The wayfinding operations.** A wayfinder session runs outside PU -- it is
user-invoked and human-in-the-loop, so it can never be a `claude -p`
session this unit hosts -- and enclosure means it cannot touch the store
directly. So it gets the six operations the skill actually needs, typed:
create a map, create a child ticket, wire blocking, read the frontier,
claim, resolve. It can create a decision ticket on a map. It cannot invent
an execution task, set urgency, or mark anything agent-runnable.

**The inbox.** Everything else -- a peer unit, a message from a person.
It does not write a task; it mints exactly one `kind:intake` record and
nothing else. An intake *session* is what converts that into real work, or
bounces it saying what was missing. That is what keeps "PU executes
ratified work and never decides what work exists" true: ratification is an
act a session performs against a written procedure, not a side effect of
an HTTP call.

Reading is open to every peer. Writing is not, and the shape of the write
is the permission -- there is no endpoint that takes an arbitrary task.

One thing this module deliberately does *not* do: edit a map's body when a
ticket resolves. Wayfinder wants a one-line gist appended to the map's
Decisions-so-far, and choosing that gist is judgement. PU records the
answer and closes the ticket; the caller rewrites the map body through
`set_body`. Mechanical here, judgement there.
"""

from __future__ import annotations

from typing import Any, Sequence

from pu import bodies, records, taskstore


class ServiceError(ValueError):
    """A request that cannot be served as asked. Surfaces as a 400."""


def _require(value: Any, name: str) -> Any:
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ServiceError(f"{name} is required")
    return value


# -- reads: open to every peer -------------------------------------------


def as_dict(task: records.Task, body: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "uuid": task.uuid,
        "description": task.description,
        "status": task.status,
        "kind": task.kind,
        "project": task.project,
        "url": task.url,
        "parent": task.parent,
        "tags": list(task.tags),
        "depends": list(task.depends),
        "urgency": task.urgency,
        "afk": task.afk,
        "active": task.active,
        "session_type": task.session_type,
        "runnable": task.runnable,
    }
    if body is not None:
        out["body"] = body
    return out


def list_tasks(
    store: taskstore.TaskStore,
    project: str | None = None,
    kind: str | None = None,
    status: str = "pending",
    parent: str | None = None,
) -> list[dict[str, Any]]:
    filters: list[str] = []
    if status:
        filters.append(f"status:{status}")
    if project:
        # Taskwarrior matches a dotted project by prefix, so `project:mu-spec`
        # returns `mu-spec.behaviour` too. That is the intended behaviour:
        # a project is a scope, and scopes nest.
        filters.append(f"project:{project}")
    if kind:
        filters.append(f"kind:{records.check_kind(kind)}")
    if parent:
        filters.append(f"parent_uuid:{parent}")
    return [as_dict(t) for t in store.export(*filters)]


def get_task(
    store: taskstore.TaskStore, body_store: bodies.BodyStore, uuid: str
) -> dict[str, Any] | None:
    task = store.get(uuid)
    if task is None:
        return None
    return as_dict(task, body=body_store.get(uuid))


def list_projects(store: taskstore.TaskStore) -> list[dict[str, Any]]:
    """Every project with open work, and how much. Derived from the tasks
    rather than stored -- there is no project record to go stale."""
    counts: dict[str, int] = {}
    for task in store.export("status:pending"):
        if task.project:
            counts[task.project] = counts.get(task.project, 0) + 1
    return [
        {"project": name, "open_tasks": count}
        for name, count in sorted(counts.items())
    ]


def frontier(
    store: taskstore.TaskStore, parent: str | None = None
) -> list[dict[str, Any]]:
    """Wayfinder's frontier: open, unblocked, unclaimed, most urgent first,
    scoped to one map's children when `parent` is given.

    This deliberately includes human-in-the-loop tickets. A person working
    a map needs to see that the next thing is a grilling ticket -- hiding
    it would make the map look finished when it is not."""
    extra = [f"parent_uuid:{parent}"] if parent else []
    return [as_dict(t) for t in store.ready(*extra)]


def queue(
    store: taskstore.TaskStore, parent: str | None = None
) -> list[dict[str, Any]]:
    """What PU itself may select: the frontier narrowed to work an agent
    is allowed to take alone.

    Strictly narrower than `frontier`, and the two must not be merged --
    that conflation is what would let a session pick up a conversation
    that only a person can have."""
    extra = [f"parent_uuid:{parent}"] if parent else []
    return [as_dict(t) for t in store.runnable(*extra)]


# -- write door 1: the wayfinding operations ------------------------------


def create_map(
    store: taskstore.TaskStore,
    body_store: bodies.BodyStore,
    destination: str,
    project: str | None = None,
    body: str | None = None,
) -> dict[str, Any]:
    """A map is a record with children, never itself worked. It carries no
    `afk` tag and `map` has no session type, so it can never be selected."""
    _require(destination, "destination")
    uuid = store.add(destination, kind="map", project=project)
    if body:
        body_store.set(uuid, body)
    return {"uuid": uuid}


def create_ticket(
    store: taskstore.TaskStore,
    body_store: bodies.BodyStore,
    parent: str,
    title: str,
    kind: str,
    body: str | None = None,
    tags: Sequence[str] = (),
    afk: bool = False,
    project: str | None = None,
) -> dict[str, Any]:
    """A child of a map.

    `afk` is an explicit opt-in and defaults to false. Wayfinder's grilling
    and prototype types are human-in-the-loop -- "the agent never stands in
    for the human's side of it" -- so defaulting this to false means a
    ticket type nobody has thought about cannot become agent-runnable by
    accident. It is default-deny, not a filter applied later."""
    _require(parent, "parent")
    _require(title, "title")
    records.check_kind(_require(kind, "kind"))

    if store.get(parent) is None:
        raise ServiceError(f"no such parent: {parent}")

    tag_list = list(tags)
    if afk:
        tag_list.append(records.AFK_TAG)

    uuid = store.add(
        title,
        tags=tag_list,
        kind=kind,
        parent_uuid=parent,
        project=project,
    )
    if body:
        body_store.set(uuid, body)
    return {"uuid": uuid}


def wire_blocking(
    store: taskstore.TaskStore, uuid: str, blocked_by: Sequence[str]
) -> dict[str, Any]:
    """Blocking is Taskwarrior's native `depends`, which is what makes
    `+READY` mean what wayfinder means by the frontier. Wiring is a second
    pass because tickets need uuids before they can reference each other."""
    _require(uuid, "uuid")
    if store.get(uuid) is None:
        raise ServiceError(f"no such task: {uuid}")
    missing = [b for b in blocked_by if store.get(b) is None]
    if missing:
        raise ServiceError(f"no such blocker(s): {', '.join(missing)}")
    store.modify(uuid, depends=list(blocked_by))
    return {"uuid": uuid, "blocked_by": list(blocked_by)}


def claim(store: taskstore.TaskStore, uuid: str) -> dict[str, Any]:
    """Claiming is the session's first write, before any work, so that a
    concurrent session skips it. `start` sets `+ACTIVE`; the claim is the
    store's own state rather than a convention layered on top."""
    task = store.get(uuid)
    if task is None:
        raise ServiceError(f"no such task: {uuid}")
    if task.active:
        return {"uuid": uuid, "claimed": False, "reason": "already claimed"}
    store.claim(uuid)
    return {"uuid": uuid, "claimed": True}


def resolve(
    store: taskstore.TaskStore, uuid: str, answer: str
) -> dict[str, Any]:
    """Record the answer and close the ticket.

    The map's Decisions-so-far is *not* touched here: choosing the one-line
    gist that goes there is judgement, and belongs to the caller, which
    writes it with `set_body`."""
    _require(answer, "answer")
    if store.get(uuid) is None:
        raise ServiceError(f"no such task: {uuid}")
    store.annotate(uuid, answer)
    store.complete(uuid)
    return {"uuid": uuid, "resolved": True}


def set_body(
    store: taskstore.TaskStore, body_store: bodies.BodyStore, uuid: str, body: str
) -> dict[str, Any]:
    if store.get(uuid) is None:
        raise ServiceError(f"no such task: {uuid}")
    body_store.set(uuid, body)
    return {"uuid": uuid, "bytes": len(body.encode("utf-8"))}


# -- write door 2: the inbox ----------------------------------------------


def push_inbox(
    store: taskstore.TaskStore,
    body_store: bodies.BodyStore,
    source: str,
    message: str,
    project: str | None = None,
) -> dict[str, Any]:
    """Accept pushed work from a peer, as exactly one `kind:intake` record.

    This endpoint cannot mint anything else. An intake session reads the
    message and either creates real tasks from it or resolves the record
    with what was missing -- so a peer can cause work to be *considered*
    without deciding that work exists.

    It is tagged `afk` because converting it is the one thing PU can
    always do alone."""
    _require(source, "source")
    _require(message, "message")

    first_line = message.strip().splitlines()[0][:120]
    uuid = store.add(
        f"intake from {source}: {first_line}",
        tags=[records.AFK_TAG],
        kind="intake",
        project=project,
    )
    body_store.set(uuid, message)
    return {"uuid": uuid, "accepted": True}


# -- stats ----------------------------------------------------------------


def stats(
    store: taskstore.TaskStore, body_store: bodies.BodyStore
) -> dict[str, Any]:
    """Mechanical counts only. Never judgement -- that is the standard's
    rule for `/stats` and the reason this unit reports numbers rather than
    opinions about them."""
    try:
        pending = store.export("status:pending")
    except taskstore.TaskError:
        # An unreachable or uninitialised store degrades to empty rather
        # than taking the endpoint down with it.
        pending = []

    by_kind: dict[str, int] = {}
    for task in pending:
        by_kind[task.kind or "unset"] = by_kind.get(task.kind or "unset", 0) + 1

    return {
        "pending_tasks": len(pending),
        "by_kind": by_kind,
        "runnable_now": sum(1 for t in pending if t.runnable and not t.active),
        "claimed": sum(1 for t in pending if t.active),
        "blocked": sum(1 for t in pending if t.depends),
        "bodies": body_store.count(),
        "body_bytes": body_store.total_bytes(),
    }
