"""The operations, as plain functions over a store. No HTTP here.

No write door into this unit is generic CRUD. The first and oldest:

**The wayfinding operations.** A wayfinder session runs outside PU -- it is
user-invoked and human-in-the-loop, so it can never be a `claude -p`
session this unit hosts -- and enclosure means it cannot touch the store
directly. So it gets the six operations the skill actually needs, typed:
create a map, create a child ticket, wire blocking, read the frontier,
claim, resolve. It can create a decision ticket on a map. It cannot invent
an execution task, set urgency, or mark anything agent-runnable.

There used to be a second door, the inbox: a peer pushed an untyped
message and an intake session turned it into work. It was wired into cu
and au and never used, and it was deleted on 2026-10-24 (pu vision). A
peer that knows what it wants creates a typed ticket; untyped input from
a person goes to cu, where a human is.

Reading is open to every peer. Writing is not, and the shape of the write
is the permission -- there is no endpoint that takes an arbitrary task.

One thing this module deliberately does *not* do: edit a map's body when a
ticket resolves. Wayfinder wants a one-line gist appended to the map's
Decisions-so-far, and choosing that gist is judgement. PU records the
answer and closes the ticket; the caller rewrites the map body through
`set_body`. Mechanical here, judgement there.
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from pu import (
    bodies, guards, logs_client, mcpstore, notify, pipeline, records, roles, session_types,
    taskstore, triggers,
)


class ServiceError(ValueError):
    """A request that cannot be served as asked. Surfaces as a 400."""


class ConflictError(ServiceError):
    """The request is well formed but the record is not in a state that
    allows it. Surfaces as a 409, so a client can tell it from a typo."""


def _require(value: Any, name: str) -> Any:
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ServiceError(f"{name} is required")
    return value


# -- addressing a task ----------------------------------------------------
#
# pu's listings and notifications show the first 8 characters of a uuid,
# so callers hold prefixes. A prefix is not a Taskwarrior short id (those
# renumber); it is part of a permanent identifier. Accepted wherever a uuid
# is, on reads and writes alike, when it names exactly one task -- git's
# rule for object ids. An ambiguous prefix is refused with the candidates,
# never first-match: a resolve sent to the wrong task loses an answer.

_FULL_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_PREFIX = re.compile(r"^[0-9a-f-]{8,35}$")
MIN_PREFIX = 8


def full_uuid(store: taskstore.TaskStore, ref: str | None) -> str | None:
    """The full uuid `ref` names, or None when it names no task. Raises
    ServiceError for a prefix too short or matching more than one task."""
    if not ref:
        return ref
    ref = ref.strip().lower()
    if _FULL_UUID.match(ref):
        return ref
    if not _PREFIX.match(ref):
        raise ServiceError(
            f"not a task uuid: {ref!r} -- give the uuid, or at least its "
            f"first {MIN_PREFIX} characters")
    # `uuid.startswith:`, not the bare prefix: an all-digit prefix would
    # otherwise be read as a short id (measured, Taskwarrior 2.6.1).
    found = store.export(f"uuid.startswith:{ref}")
    if not found:
        return None
    if len(found) > 1:
        raise ServiceError(
            f"{ref!r} matches {len(found)} tasks: "
            + ", ".join(f"{t.uuid} ({t.description[:40]})" for t in found[:5])
            + " -- give more of the uuid")
    return found[0].uuid


def _must_exist(store: taskstore.TaskStore, ref: str, what: str) -> str:
    uuid = full_uuid(store, ref)
    # A full uuid is returned unlooked-up, so existence is checked here.
    if uuid is None or store.get(uuid) is None:
        raise ServiceError(f"no such {what}: {ref}")
    return uuid


# -- the run namespace ----------------------------------------------------
#
# Trigger-fired work lands in one reserved project, so a schedule firing
# every morning does not fill the task views -- people's and agents' --
# with a year of small run-tasks. It is hidden from every read except the
# queue, which is the one place it must never be hidden from. Only triggers
# create tasks there. (pu vision, 2026-10-24.)

RUNS_PROJECT = records.RUNS_PROJECT


def _in_runs(project: str | None) -> bool:
    return bool(project) and (project == RUNS_PROJECT
                              or project.startswith(RUNS_PROJECT + "."))


def _hide_runs(project: str | None) -> tuple[str, ...]:
    """The filter that keeps run-tasks out of a read, unless the caller
    asked for that namespace by name. `project.not:` excludes nested
    scopes too, which is the behaviour wanted (measured, Taskwarrior
    2.6.1)."""
    return () if _in_runs(project) else (f"project.not:{RUNS_PROJECT}",)


def _refuse_runs(project: str | None) -> None:
    if _in_runs(project):
        raise ServiceError(
            f"{RUNS_PROJECT} is reserved for work triggers create; a map or "
            f"ticket belongs in a project of its own")


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
        "repo": task.repo,
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
    filters: list[str] = list(_hide_runs(project))
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
        filters.append(f"parent_uuid:{full_uuid(store, parent) or parent}")
    return [as_dict(t) for t in store.export(*filters)]


def get_task(
    store: taskstore.TaskStore, body_store: bodies.BodyStore, uuid: str
) -> dict[str, Any] | None:
    uuid = full_uuid(store, uuid)
    task = store.get(uuid) if uuid else None
    if task is None:
        return None
    out = as_dict(task, body=body_store.get(uuid))
    # A resolution is an annotation, so without this a caller cannot check
    # whether an answer landed or what it said. Single reads only; a list
    # of every task's annotations would be the whole store.
    out["annotations"] = list(task.annotations)
    return out


def list_projects(store: taskstore.TaskStore) -> list[dict[str, Any]]:
    """Every project with open work, and how much. Derived from the tasks
    rather than stored -- there is no project record to go stale."""
    counts: dict[str, int] = {}
    for task in store.export("status:pending", *_hide_runs(None)):
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
    extra = ([f"parent_uuid:{full_uuid(store, parent) or parent}"]
             if parent else [])
    return [as_dict(t) for t in store.ready(*extra, *_hide_runs(None))]


def queue(
    store: taskstore.TaskStore, parent: str | None = None
) -> list[dict[str, Any]]:
    """What PU itself may select: the frontier narrowed to work an agent
    is allowed to take alone.

    Narrower than `frontier` in what an agent may take, and the two must
    not be merged -- that conflation is what would let a session pick up a
    conversation that only a person can have. **Wider in one way:** it
    includes `pu.runs`, which every other read hides. Hiding trigger work
    here as well would be a silent outage."""
    extra = ([f"parent_uuid:{full_uuid(store, parent) or parent}"]
             if parent else [])
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
    # A map always lives in a project: it is the destination that project's
    # tickets are judged against. (Placement rule, 2026-10-25.)
    _require(project, "project")
    _refuse_runs(project)
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
    priority: str | None = None,
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
    _refuse_runs(project)

    parent = _must_exist(store, parent, "parent")
    project = _place(store, parent, project)

    tag_list = list(tags)
    if afk:
        tag_list.append(records.AFK_TAG)

    fields = {}
    if records.check_priority(priority):
        fields["priority"] = records.check_priority(priority)
    uuid = store.add(
        title,
        tags=tag_list,
        kind=kind,
        parent_uuid=parent,
        project=project,
        **fields,
    )
    if body:
        body_store.set(uuid, body)
    return {"uuid": uuid}


# -- placement: every ticket hangs off a map in its own project ----------
#
# Decided 2026-10-25. A task cannot exist without a map; its project must
# equal its map's project or be nested under it -- so the v1 map in
# `holonic-node` may hold `holonic-node.pu`'s vision ticket, and the
# gateway's map may not hold cu's dashboard. A map always has a project and
# may have no tickets. Several maps per project are fine. `pu.runs` is the
# one exemption: trigger work never has a map, by design.


def placed(ticket_project: str | None, map_project: str | None) -> bool:
    if not ticket_project or not map_project:
        return False
    return (ticket_project == map_project
            or ticket_project.startswith(map_project + "."))


def _place(store: taskstore.TaskStore, parent: str,
           project: str | None) -> str:
    """The project a ticket under `parent` gets: its own if that is placed,
    the map's when it names none. Refuses anything else."""
    map_task = store.get(parent)
    if map_task.kind != "map":
        raise ServiceError(f"{parent} is not a map; a ticket hangs off a map")
    if not map_task.project:
        raise ServiceError(
            f"map {parent} has no project; give it one with move_task first")
    project = project or map_task.project
    _refuse_runs(project)
    if not placed(project, map_task.project):
        raise ServiceError(
            f"project {project!r} does not belong under map "
            f"{map_task.description[:60]!r} (project {map_task.project!r}): "
            f"a ticket's project must be its map's or nested under it. Put "
            f"it on a map of its own project")
    return project


def move_task(
    store: taskstore.TaskStore, uuid: str, parent: str | None = None,
    project: str | None = None, title: str | None = None,
) -> dict[str, Any]:
    """Change a task's map, project or title, keeping everything else --
    body, annotations, blocking: the history maps exist to keep. Checked
    against the placement rule like a new ticket, and every move says on
    the task where it came from."""
    uuid = _must_exist(store, uuid, "task")
    task = store.get(uuid)
    if parent is None and project is None and title is None:
        raise ServiceError("nothing to change: give parent, project or title")
    if title is not None:
        _require(title, "title")

    fields: dict[str, Any] = {}
    if task.kind == "map":
        if parent is not None:
            raise ServiceError("a map has no parent")
        if project is not None:
            _require(project, "project")
            _refuse_runs(project)
            stranded = [c for c in store.export(
                "status:pending", f"parent_uuid:{uuid}")
                if not placed(c.project, project)]
            if stranded:
                raise ServiceError(
                    f"moving this map to {project!r} would misplace "
                    f"{len(stranded)} of its tickets: "
                    + ", ".join(c.uuid[:8] for c in stranded[:10]))
            fields["project"] = project
    else:
        new_parent = (_must_exist(store, parent, "parent")
                      if parent is not None else task.parent)
        if not new_parent:
            raise ServiceError("a task needs a map: give parent")
        new_project = _place(store, new_parent,
                             project if project is not None else
                             (task.project if parent is None else None))
        if new_parent != task.parent:
            fields["parent_uuid"] = new_parent
        if new_project != task.project:
            fields["project"] = new_project
    if title is not None and title != task.description:
        fields["description"] = title

    if fields:
        was = ", ".join(f"{k}={getattr(task, 'parent' if k == 'parent_uuid' else k)!r}"
                        for k in fields)
        store.modify(uuid, **fields)
        store.annotate(uuid, f"moved: was {was}")
    return {"uuid": uuid, "changed": sorted(
        "parent" if k == "parent_uuid" else "title" if k == "description" else k
        for k in fields)}


def audit(store: taskstore.TaskStore) -> dict[str, list[dict[str, Any]]]:
    """Every placement fault, by kind. Mechanical, like stats: it names
    what breaks the rule and leaves what to do to whoever reads it."""
    pending = store.export("status:pending", *_hide_runs(None))
    maps = {t.uuid: t for t in pending if t.kind == "map"}

    def row(t, why):
        return {"uuid": t.uuid, "title": t.description[:80],
                "project": t.project, "why": why}

    out: dict[str, list[dict[str, Any]]] = {
        "maps_without_project": [row(m, "a map always has a project")
                                 for m in maps.values() if not m.project],
        "mapless": [], "misplaced": [], "orphaned": []}
    for t in pending:
        if t.kind == "map":
            continue
        if not t.parent:
            out["mapless"].append(row(t, "a task cannot exist without a map"))
            continue
        parent = maps.get(t.parent)
        if parent is None:
            closed = store.get(t.parent)
            out["orphaned"].append(row(
                t, f"its map is {closed.status if closed else 'missing'}, so "
                   f"no frontier reaches it"))
        elif not placed(t.project, parent.project):
            out["misplaced"].append(row(
                t, f"project {t.project!r} under map project "
                   f"{parent.project!r}"))
    return out


def needs_attention(store: taskstore.TaskStore, type_store=None,
                    trigger_store=None) -> dict[str, Any]:
    """Everything waiting on a person, kept in two groups on purpose: work
    that is theirs by design, and faults that were escalated as they
    happened and are collected here so there is one place to look."""
    def row(t):
        return {"uuid": t.uuid, "title": t.description[:80],
                "kind": t.kind, "project": t.project}

    human_kinds = {"grilling", "prototype"}
    frontier_hitl = [row(t) for t in store.ready(*_hide_runs(None))
                     if t.kind in human_kinds]
    handed_over = [row(t) for t in store.export(
        "status:pending", f"+{guards.NEEDS_HUMAN_TAG}")]

    unactivated = []
    for kind, source in (("session_type", type_store), ("trigger", trigger_store)):
        if source is None:
            continue
        for name, record in source.current().items():
            if record.get("retired") or record.get("enabled"):
                continue
            if kind == "session_type" and source.has_ever_been_enabled(name):
                continue
            unactivated.append({"kind": kind, "name": name,
                                "purpose": record.get("purpose", "")})

    faults = audit(store)
    return {
        "waiting_on_you": {
            "decisions": frontier_hitl,
            "handed_to_you": handed_over,
            "never_activated": unactivated,
        },
        "faults": {
            "stale_run_claims": [row(t) for t in guards.stale_run_claims(store)],
            **{k: v for k, v in faults.items() if v},
        },
    }


def wire_blocking(
    store: taskstore.TaskStore, uuid: str, blocked_by: Sequence[str]
) -> dict[str, Any]:
    """Blocking is Taskwarrior's native `depends`, which is what makes
    `+READY` mean what wayfinder means by the frontier. Wiring is a second
    pass because tickets need uuids before they can reference each other."""
    _require(uuid, "uuid")
    uuid = _must_exist(store, uuid, "task")
    resolved = [full_uuid(store, b) for b in blocked_by]
    missing = [b for b, r in zip(blocked_by, resolved) if r is None]
    if missing:
        raise ServiceError(f"no such blocker(s): {', '.join(missing)}")
    blocked_by = resolved
    store.modify(uuid, depends=list(blocked_by))
    return {"uuid": uuid, "blocked_by": list(blocked_by)}


def claim(store: taskstore.TaskStore, uuid: str) -> dict[str, Any]:
    """Claiming is the session's first write, before any work, so that a
    concurrent session skips it. `start` sets `+ACTIVE`; the claim is the
    store's own state rather than a convention layered on top."""
    uuid = _must_exist(store, uuid, "task")
    task = store.get(uuid)
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
    uuid = _must_exist(store, uuid, "task")
    task = store.get(uuid)
    # Checked before the annotation, not left to `done` to refuse: on a
    # completed task the annotate succeeds and only the `done` fails, so a
    # retry used to append a second answer and then report failure --
    # which invited the next retry. Live 2026-09-27, an answer lost.
    if task.status != "pending":
        raise ConflictError(
            f"task {uuid} is already {task.status}; its answer stands")
    # A map closes after its tickets, never before: closing it first hides
    # them from every frontier -- and a hidden grilling is work waiting on
    # a person who cannot see it.
    if task.kind == "map":
        open_children = store.export("status:pending", f"parent_uuid:{uuid}")
        if open_children:
            raise ConflictError(
                f"map has {len(open_children)} open ticket(s); resolve or "
                f"move them first: " + ", ".join(
                    f"{c.uuid[:8]} {c.description[:40]}"
                    for c in open_children[:10]))
    store.annotate(uuid, answer)
    store.complete(uuid)
    return {"uuid": uuid, "resolved": True}


def set_body(
    store: taskstore.TaskStore, body_store: bodies.BodyStore, uuid: str, body: str
) -> dict[str, Any]:
    uuid = _must_exist(store, uuid, "task")
    body_store.set(uuid, body)
    return {"uuid": uuid, "bytes": len(body.encode("utf-8"))}


# -- write door 4: trigger authoring --------------------------------------
#
# Symmetric with the session-type door and for the same reason: the store
# worked and nobody could reach it. Until this existed the only way to
# create a watch was to append a line to `triggers.jsonl` with an editor,
# which also skipped the validation -- `TriggerStore.append` validates and
# nothing called it, so a malformed line was not discovered until the tick
# that read it.
#
# Authoring a trigger is agent work, like authoring a session type, so this
# is in `/tools` and not behind `/actions`.


def list_triggers(trigger_store) -> dict[str, Any]:
    """Every trigger's current record, minus the retired half of a
    rename. Reading is open to every peer."""
    return {"triggers": [r for r in trigger_store.current().values()
                         if not r.get("retired")]}


def get_trigger(trigger_store, name: str) -> dict[str, Any] | None:
    """None for a name never written -- a 404 at the routing layer, the
    same shape as `get_task` and `get_session_type`."""
    return trigger_store.get(_require(name, "name"))


def get_trigger_cursor(trigger_store, name: str) -> dict[str, Any] | None:
    """Where a watch has got to.

    Served apart from the trigger rather than inside it, because it is
    *state* and the trigger is *config*: they change on different clocks,
    one is append-only and the other is overwritten every tick, and folding
    them together would imply a history the cursor does not have.

    It is also the evidence a watch is working at all -- the milestone
    asked for "the watch record showing the cursor before and after", and
    before this the answer was to go and read a file.
    """
    if trigger_store.get(_require(name, "name")) is None:
        return None
    return trigger_store.cursor(name)


def write_trigger(trigger_store, payload: dict[str, Any]) -> dict[str, Any]:
    """Append one trigger record, under the same two activation rules.

    **A trigger inherits both rules, and the reasoning is stronger than for
    a session type rather than weaker.** A session type is inert until a
    task reaches it; a trigger with `emits.afk` true causes spend on an
    interval with nobody watching. So: created inactive, and an edit to a
    live one switches it off.

    Nothing here validates the record. `TriggerStore.append` does, and this
    endpoint being its first real caller is the point -- a second copy of
    the rules would be a second thing to keep in step.
    """
    if not isinstance(payload, dict):
        raise ServiceError("a trigger is a JSON object")

    previous = trigger_store.get(payload.get("name") or "")
    record, transition = apply_activation_rules(previous, dict(payload))
    written = trigger_store.append(record)

    emits = written.get("emits") or {}
    spends = " It emits agent-runnable work, so it will spend." \
        if emits.get("afk") else ""
    if transition:
        logs_client.record_event(
            "trigger_activated" if transition == "activated"
            else "trigger_deactivated",
            "trigger", written["name"],
            "activated" if transition == "activated" else
            "edited while live, so it needs approval again",
            title=written.get("purpose", ""),
            detail={"source": written["source"].get("kind"),
                    "spends": bool(emits.get("afk"))})

    if transition == "deactivated_by_edit":
        notify.notify_owner(
            notify.bridge_url(),
            f"pu deactivated a live trigger because it was edited.\n\n"
            f"{written['name']} - {written['source'].get('kind')}\n"
            f"{written.get('purpose', '')}\n\n"
            f"An edit can change what it watches or what it creates, so it "
            f"is switched off rather than left firing under new rules. Read "
            f"the change and activate it again.",
            source_unit="pu",
        )
    elif transition == "activated":
        notify.notify_owner(
            notify.bridge_url(),
            f"pu activated a trigger, and nothing asked first.\n\n"
            f"{written['name']} - {written['source'].get('kind')}\n"
            f"{written.get('purpose', '')}\n\n"
            f"It will now create work on every tick its condition holds."
            f"{spends}",
            source_unit="pu",
        )
    return written

def _renamed(store, old: str, new: str, kind: str) -> dict[str, Any]:
    """Write the record under a new name and retire the old one.

    **A name was immutable because the log is keyed by it, not because
    anything downstream depends on it.** That is worth stating, because
    the obvious fear is wrong: a trigger reaches a session through the
    kind and tags it emits, never through either name, so renaming
    either side cannot unwire anything. What it does cost is history --
    runs are recorded against the session-type *name*, so a renamed
    session's past runs stay under the old one. Stated rather than
    hidden; the alternative is rewriting a log that is append-only
    precisely so it cannot be rewritten.

    Two appends and no deletion, for the same reason. The old name gets
    a final line carrying `retired`, which is what keeps it out of the
    listings while leaving the history readable.

    **The rename does not deactivate.** The edit rule exists because an
    edit can change what an approved thing *does*, and a name changes
    nothing it does -- not its grant, not its prompt, not what it
    watches or creates. Switching it off here would be ceremony.
    """
    record = store.get(old)
    if record is None:
        raise ServiceError(f"no {kind} named {old!r}")
    if not new or not isinstance(new, str):
        raise ServiceError("a rename needs a new name")
    new = new.strip()
    if new == old:
        raise ServiceError("that is the name it already has")
    # A retired name is free again. It is the old half of some earlier
    # rename, and refusing it would mean every name ever used is spent
    # forever -- including the one you just renamed away from and want
    # back thirty seconds later.
    held = store.get(new)
    if held is not None and not held.get("retired"):
        raise ServiceError(
            f"there is already a {kind} named {new!r}; a rename may not "
            f"write over one")

    written = store.append(dict(record, name=new))
    store.append({**{k: v for k, v in record.items() if k != "enabled"},
                  "enabled": False, "retired": True})
    return written


def rename_session_type(type_store, name: str, to: str) -> dict[str, Any]:
    """Rename a session type. Its runs stay under the old name."""
    return _renamed(type_store, _require(name, "name"), to, "session type")


def rename_trigger(trigger_store, name: str, to: str) -> dict[str, Any]:
    """Rename a trigger, carrying its cursor across.

    The cursor is the one thing that must move with it. Leaving it
    behind would hand the renamed trigger an empty `seen` list, and it
    would re-report every item its source is still serving -- which for
    a watch that emits agent-runnable work is a pile of paid sessions
    for things already dealt with.
    """
    old = _require(name, "name")
    written = _renamed(trigger_store, old, to, "trigger")
    cursor = trigger_store.cursors().get(old)
    if cursor is not None:
        trigger_store.save_cursor(written["name"], cursor)
    return written


def _require_servers(record: dict[str, Any], type_store) -> None:
    """A type may attach only servers the registry holds. Attaching one is
    an edit like any other, so on a live type the activation rules have
    already switched it off by the time this runs."""
    names = record.get("mcp_servers") or []
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ServiceError("mcp_servers is a list of server names")
    known = mcpstore.McpStore(type_store.root).current()
    missing = [n for n in names if n not in known]
    if missing:
        raise ServiceError(
            f"MCP server(s) {missing} are not in the registry "
            f"(known: {sorted(known) or 'none'}); add them with "
            f"write_mcp_server first. Holonic is attached to every session "
            f"already and is not listed here")


# -- write door 5: the MCP server registry --------------------------------
#
# Same rules as session types: agents may write, the record is append-only,
# and every write tells the owner -- a server is reach into somewhere else.


def list_mcp_servers(mcp_store) -> dict[str, Any]:
    return {"mcp_servers": list(mcp_store.current().values())}


def write_mcp_server(mcp_store, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ServiceError("an MCP server is a JSON object")
    try:
        written = mcp_store.append(payload)
    except mcpstore.McpStoreError as exc:
        raise ServiceError(str(exc)) from None
    notify.notify_owner(
        notify.bridge_url(),
        f"pu's MCP registry changed: {written['name']} -> {written['url']}\n"
        f"{written.get('purpose', '')}\n\nA session type reaches it only "
        f"once it attaches it, and attaching switches a live type off.",
        source_unit="pu")
    return written


def dry_run_session_type(
    type_store, payload: dict[str, Any], store: taskstore.TaskStore | None = None,
    body_store: bodies.BodyStore | None = None, unit_root=None,
) -> dict[str, Any]:
    """Show exactly what a draft session type would run as, launching
    nothing and writing nothing.

    The sibling of `dry_run_trigger`, and exit condition 2's "testable"
    half: the generated instructions, the tools, where it would run, the
    model, the agent role and the argv. With `task`, also the full prompt
    that task would produce -- the one thing a reviewer most needs to read
    and, until now, could only see after paying for a run."""
    from pu import runner, typestore

    if not isinstance(payload, dict):
        raise ServiceError("a session type is a JSON object")
    draft = {k: v for k, v in payload.items() if k not in ("task", "enabled")}
    record = typestore.validate(dict(draft, origin="authored"))
    grant = session_types.GRANTS[record["grant"]]

    out: dict[str, Any] = {
        "name": record["name"],
        "agent_role": record["agent_role"],
        "grant": record["grant"],
        "allowed_tools": grant.tools or None,
        "mcp_servers": ["mcp-bridge", *(record.get("mcp_servers") or [])],
        "cwd": ("the task's target repository" if grant.runs_in_target_repo
                else str(type_store.dirs / record["name"])),
        "model": record.get("model"),
        "claude_md": typestore.render(record),
        "argv": runner.build_argv(
            allowed_tools=grant.tools or None, model=record.get("model"),
            mcp_bridge_url="<bridge>",
            mcp_servers=record.get("mcp_servers") or (),
            mcp_config_path=("<type dir>/.mcp-session.json"
                             if record.get("mcp_servers") else None)),
        "note": "Nothing was written and nothing was launched.",
    }
    task_uuid = payload.get("task")
    if task_uuid and store is not None:
        task_uuid = _must_exist(store, task_uuid, "task")
        task = store.get(task_uuid)
        out["prompt"] = pipeline.build_prompt(
            task, body_store.get(task_uuid) if body_store else None,
            unit_root, agent_role=record["agent_role"])
    return out


def dry_run_trigger(trigger_store, payload: dict[str, Any]) -> dict[str, Any]:
    """Evaluate a draft trigger now, and report what it would do.

    **Writes nothing.** No record, no task, no cursor. That is the whole
    reason this exists: a trigger is the one object here whose correctness
    cannot be read off its own record -- whether a predicate holds depends
    on what a source is serving this minute -- and the alternative way to
    find out is to activate it and wait.

    It runs the *same* functions the tick runs, not a reimplementation of
    them. `validated`, `read_source`, `evaluate` and `payload_body` are
    the production path; a second opinion written for the preview would
    eventually disagree with the real one, and a preview that disagrees is
    worse than none because it is believed.

    The cursor is **read and not advanced**, so the aggregates
    (`count_since_fire`, `age_since_last_seen`) are the real ones for a
    saved trigger and empty for a draft. Running this repeatedly is
    therefore idempotent and tells you the same thing each time, which is
    what you want of a test button.

    One fetch, memoised, because `read_source` and `evaluate` would
    otherwise each hit the source and a test that doubles the load on
    somebody's feed is a test people learn not to press.
    """
    if not isinstance(payload, dict):
        raise ServiceError("a trigger is a JSON object")
    record = triggers.validated({k: v for k, v in payload.items()
                                 if k != "enabled"})
    name = record["name"]
    source = record["source"]

    cache: dict[str, tuple[int, bytes]] = {}

    def fetch(url: str) -> tuple[int, bytes]:
        if url not in cache:
            cache[url] = triggers.http_fetch(url)
        return cache[url]

    cursor = (trigger_store.cursor(name) if trigger_store is not None
              else {"seen": [], "last_new_at": None, "pending": 0,
                    "fired_at": None})
    seen = set(cursor.get("seen") or ())

    if source["kind"] == "schedule":
        ok, items, reason = True, [], "a schedule fetches nothing"
    else:
        ok, items, reason = triggers.read_source(source, fetch)

    evaluation = triggers.evaluate(record, cursor, fetch=fetch)
    fresh = [i for i in items
             if triggers.item_id(i, source) not in seen]

    emits = record.get("emits") or {}
    would_create = None
    if evaluation.fired and emits:
        tags = list(emits.get("tags") or ())
        if emits.get("afk"):
            tags.append(records.AFK_TAG)
        would_create = {
            "title": triggers.fill_title(
                emits.get("title") or f"{name}: {evaluation.reason}",
                evaluation.items[0] if evaluation.items else None),
            "kind": emits["kind"],
            "tags": tags,
            "project": emits.get("project") or records.RUNS_PROJECT,
            "body": pipeline.payload_body(record, evaluation),
        }

    return {
        "ok": ok,
        "reason": reason if not ok else evaluation.reason,
        "fired": evaluation.fired,
        "fetched": len(items),
        "fresh": len(fresh),
        # The matched items themselves, capped: this is a preview, and a
        # feed returning two hundred matches would answer the question
        # with the first few just as well.
        "matched": [i for i in evaluation.items[:5]],
        "matched_total": len(evaluation.items),
        "would_create": would_create,
        "note": ("Nothing was written. The cursor was read and not "
                 "advanced, so this reports the same thing each time."),
    }


# -- stats ----------------------------------------------------------------


def _expired_by_trigger(store: taskstore.TaskStore) -> dict[str, int]:
    counts: dict[str, int] = {}
    try:
        for t in store.export("status:completed", "+expired"):
            counts[t.trigger or "(none)"] = counts.get(t.trigger or "(none)", 0) + 1
    except taskstore.TaskError:
        pass
    return counts


def stats(
    store: taskstore.TaskStore, body_store: bodies.BodyStore
) -> dict[str, Any]:
    """Mechanical counts only. Never judgement -- that is the standard's
    rule for `/stats` and the reason this unit reports numbers rather than
    opinions about them."""
    try:
        # Open work as the task views show it: without the run namespace,
        # which is counted on its own line instead.
        pending = store.export("status:pending", *_hide_runs(None))
        runs_pending = len(store.export(
            "status:pending", f"project:{RUNS_PROJECT}"))
        # The same call `queue()` makes, so the two numbers cannot differ.
        # Counting `t.runnable` over all pending work also counted blocked
        # tasks, and stats said 5 while the queue said none.
        runnable_now = len(store.runnable())
        blocked = len(store.export(
            "status:pending", "+BLOCKED", *_hide_runs(None)))
    except taskstore.TaskError:
        # An unreachable or uninitialised store degrades to empty rather
        # than taking the endpoint down with it.
        pending, runs_pending, runnable_now, blocked = [], 0, 0, 0

    by_kind: dict[str, int] = {}
    for task in pending:
        by_kind[task.kind or "unset"] = by_kind.get(task.kind or "unset", 0) + 1

    return {
        "pending_tasks": len(pending),
        "by_kind": by_kind,
        "runs_pending": runs_pending,
        # Trigger-created tasks claimed over a day: reported, never released.
        "stale_run_claims": len(guards.stale_run_claims(store)),
        # Trigger work that expired unrun, per trigger, over the run-task
        # retention window (30 days).
        "expired_runs": _expired_by_trigger(store),
        # Placement faults by kind; audit_tasks names them.
        "placement_faults": {k: len(v) for k, v in audit(store).items()},
        "runnable_now": runnable_now,
        "claimed": sum(1 for t in pending if t.active),
        # `+BLOCKED`, not "has depends": a finished blocker blocks nothing.
        "blocked": blocked,
        "bodies": body_store.count(),
        "body_bytes": body_store.total_bytes(),
    }


# -- write door 3: session-type authoring ---------------------------------
#
# A third typed door, and still not generic CRUD. The shape is the
# permission here too: this endpoint writes a session-type record and
# nothing else, it cannot set `origin`, and it cannot enable anything.
#
# It is the agent-facing door on purpose. The authoring decision put the
# human-only half -- approve, reject, the auto-approve switch -- behind
# `/actions`, which discovery never reads; creating a type is explicitly
# something an agent may do, so it lives in `/tools` with everything else.


def list_session_types(type_store) -> dict[str, Any]:
    """Every type's current record. Reading is open to every peer.

    A retired name is left out. It is the old half of a rename, kept in
    the log so the history stays readable and kept out of here so the
    listing is what exists rather than what has ever existed."""
    return {"session_types": [r for r in type_store.current().values()
                              if not r.get("retired")]}


def get_session_type(type_store, name: str) -> dict[str, Any] | None:
    """None for a name that has never been written, matching `get_task`:
    a missing named resource is a 404 at the routing layer, not a 400 --
    the caller's request was well formed, the thing simply is not there."""
    return type_store.get(_require(name, "name"))


def write_session_type(type_store, payload: dict[str, Any]) -> dict[str, Any]:
    """Append one session-type record, as the `authored` origin.

    Two refusals live here rather than in the store, because both are
    about who is calling rather than about whether the record is
    representable:

    **`origin` is set here and never accepted from the caller.** A record
    arriving at this endpoint is authored by definition. The two origins
    differ in exactly one thing -- a declared type skips the first-enable
    gate because it arrived inside reviewed, deployed software -- so a
    caller able to claim `declared` would be claiming that exemption.

    **A type is created inactive, always.** Settled 2026-10-09: anyone may
    create one and it arrives switched off, so creating and activating are
    two acts rather than one. `enabled` on a first write is overridden
    rather than refused -- a refusal would make every caller handle an
    error for something this unit can simply decide.

    **Editing a live type deactivates it.** Andrey: *"editing a session
    practically should either deactivate it or wait for user approval,
    because editing a session practically may change its entire purpose and
    it's a security breach."* Of the two, deactivating is the one available
    today -- waiting needs the first-enable gate, which does not exist. The
    exploit it closes is specific: `prompt` is the whole behaviour, so
    editing the prompt of an already-approved type silently changes what an
    approved session does, with no new approval anywhere.

    A write that touches **only `enabled`** is honoured, because that *is*
    the activation path and deactivating in response to it would make a
    type impossible to turn on.

    **Activation is the user's by design and by nothing else.** Nothing
    authenticates a caller, so an agent or any process on the host can
    activate a type. No gate is added to pretend otherwise; what exists is
    that **every activation and every forced deactivation tells the
    owner**. The whole question is deferred to the security ticket, whose
    deferral expires the moment the node stops being loopback-only or
    single-user.

    The flag itself binds: `session_types.discover` skips a record that is
    not enabled, so writing one is never enough to start spending money.
    """
    if not isinstance(payload, dict):
        raise ServiceError("a session type is a JSON object")
    if "origin" in payload:
        raise ServiceError(
            "origin is set by this unit, not by the caller: a record written "
            "here is `authored`. Only a peer's own declaration is `declared`, "
            "and that is what skips the first-enable gate")

    # Read before the write, so every question below is answered about the
    # state from before this call rather than the one it just created.
    previous = type_store.get(payload.get("name") or "")
    # The role defaults on both sides before they are compared. Otherwise a
    # record written before the field existed, re-sent with its default
    # spelled out, would read as an edit and switch a live type off. A
    # *real* role change does count as an edit, on purpose: once a role
    # carries MCP permissions, moving a type between roles is a permission
    # change and needs approval again.
    def with_role(r):
        return dict(r, agent_role=r.get("agent_role")
                    or session_types.DEFAULT_AGENT_ROLE)
    if previous is not None:
        previous = with_role(previous)
    record, transition = apply_activation_rules(
        previous, with_role(dict(payload, origin="authored")),
        fields=PERMISSION_FIELDS)
    # Only an existing role can be named. Checked against the gateway's
    # registry, strictly: an unreachable registry refuses a non-system role
    # rather than letting a typo through.
    try:
        roles.require(record["agent_role"], notify.bridge_url(), type_store.root)
    except ValueError as exc:
        raise ServiceError(str(exc)) from None
    _require_servers(record, type_store)
    written = type_store.append(record)

    if transition:
        logs_client.record_event(
            "type_activated" if transition == "activated" else "type_deactivated",
            "session_type", written["name"],
            "activated" if transition == "activated" else
            "an agent changed what it may do, so it needs approval again",
            title=written.get("purpose", ""),
            detail={"grant": written.get("grant"),
                    "agent_role": written.get("agent_role")})
    if transition == "deactivated_by_edit":
        notify.notify_owner(
            notify.bridge_url(),
            f"pu deactivated a live session type because it was edited.\n\n"
            f"{written['name']} - grant {written['grant']}\n"
            f"{written.get('purpose', '')}\n\n"
            f"An edit can change what an already-approved session does, so "
            f"it is switched off rather than left running under new "
            f"instructions. Read the change and activate it again.",
            source_unit="pu",
        )
    elif transition == "activated":
        # A flip, not every save. A message per edit is noise that teaches
        # its own reader to skip it -- the same reasoning the usage gate
        # reports on transitions rather than per tick.
        notify.notify_owner(
            notify.bridge_url(),
            f"pu activated a session type, and nothing asked first.\n\n"
            f"{written['name']} - grant {written['grant']}\n"
            f"{written.get('purpose', '')}\n\n"
            f"It now claims {written.get('claims') or 'nothing'} and will "
            f"run sessions. Switch it off by writing the record again with "
            f"enabled false.",
            source_unit="pu",
        )
    return written


def _changes_more_than_enabled(
    record: dict[str, Any], previous: dict[str, Any],
    fields: tuple[str, ...] | None = None,
) -> bool:
    """Whether this write alters anything but the switch.

    Compared as whole records, not as a patch, because both stores hold a
    full record per line: a field the caller left out has been *removed*,
    and treating that as "unchanged" would let an edit drop a field from a
    live record without deactivating it.
    """
    if fields is not None:
        return any(record.get(k) != previous.get(k) for k in fields)
    return ({k: v for k, v in record.items() if k != "enabled"}
            != {k: v for k, v in previous.items() if k != "enabled"})


# What a session type may do, as opposed to what it says. Changing one of
# these on a live type is a permission change and needs approval again;
# a prompt or purpose fix does not (dashboard review, 2026-10-25). For a
# trigger every field counts: what it watches and what it creates are both
# what makes it spend.
PERMISSION_FIELDS = ("grant", "agent_role", "mcp_servers", "plugins", "claims",
                     "origin")


def apply_activation_rules(
    previous: dict[str, Any] | None, record: dict[str, Any],
    fields: tuple[str, ...] | None = None, by_human: bool = False,
) -> tuple[dict[str, Any], str | None]:
    """The two rules from the accessibility grilling, for both doors.

    Shared rather than written twice because the rules must not drift
    between session types and triggers -- the same reason the two logs
    share their reading. The *messages* stay per door, since what a person
    needs to be told differs.

    Returns the record to write and which transition happened, if any:
    `activated` or `deactivated_by_edit`.

    1. **Created inactive, always.** Overridden rather than refused: a
       refusal makes every caller handle an error for something this unit
       can simply decide.
    2. **Editing a live record switches it off**, because an edit can
       change what an already-approved thing does. A write touching only
       `enabled` is honoured -- that is the activation path, and
       deactivating in response to it would make a record impossible to
       turn on.
    """
    if previous is None:
        return dict(record, enabled=False), None
    # A person editing has nothing to re-approve: they are the approver.
    # Nothing authenticates a caller yet, so only the dashboard's future
    # human-only write door passes by_human; every API write is an agent's.
    if (previous.get("enabled") and not by_human
            and _changes_more_than_enabled(record, previous, fields)):
        return dict(record, enabled=False), "deactivated_by_edit"
    if record.get("enabled") and not previous.get("enabled", False):
        return record, "activated"
    return record, None
