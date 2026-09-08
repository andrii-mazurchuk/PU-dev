"""The universal task record.

Every task PU knows about -- a wayfinder decision ticket, a mirrored GitHub
issue, a message someone pushed at the inbox, a wayfinder map -- is the
same record. The differences between those are *fields*, not separate
stores:

  description  one line, always present
  body         the long form, when there is one (bodies.py, keyed by uuid)
  url          external authority; None means this record is the authority
  kind         what resolving this produces -> which session type runs it
  parent       what it is part of (a wayfinder map, an epic, anything)
  project      the effort, hierarchical, dotted
  tags         which SOPs apply (union, all mandatory) + the `afk` marker
  depends      blocking, native, works across sources

`kind` is a Taskwarrior UDA with a closed `values` list. That list is the
guard on the **`task` command** path -- the binary rejects an unknown kind
with exit 2 and writes nothing, which is what protects a session driving
the store by hand.

It is **not** a guard on the import path: `task import` writes whatever
JSON it is handed and validates no UDA value (measured). Since this unit
creates every task through `import`, `check_kind` below is the real guard
for our own writes, not a convenience wrapper around the binary's. Both
still read `KINDS`, so there is one vocabulary and two enforcement points,
not two vocabularies.

Identity is `uuid`, never `id`. Taskwarrior renumbers `id` as tasks
complete, so an `id` held across two calls silently addresses a different
task. Nothing in this unit may persist or accept an `id`.
"""

from __future__ import annotations

import dataclasses
from typing import Any

# The closed vocabulary. Order is significant: Taskwarrior uses the order of
# `uda.kind.values` as the sort order for the attribute.
#
# The first three are the kinds PU can actually run; each maps to a session
# type below. The last three exist because wayfinder stores them and this is
# one store -- PU must be able to hold a grilling ticket without ever being
# able to select one.
KINDS: tuple[str, ...] = (
    "execution",
    "research",
    "intake",
    "task",
    "prototype",
    "grilling",
    "map",
)

# Which session type resolves a kind. A kind absent from this map is one PU
# has no way to run -- selection is default-deny, so a new kind is inert
# until someone builds a session type for it, rather than being handed to
# whatever session type happens to sort first.
#
# `grilling` and `prototype` are wayfinder's hard human-in-the-loop types:
# "the agent never stands in for the human's side of it". They are absent
# here permanently and on purpose, not pending.
#
# `task` is wayfinder's do-rather-than-decide type. It is absent for now
# because its work is too heterogeneous to have earned a tool grant yet.
#
# `map` is a wayfinder map: a record with children, never itself worked.
SESSION_TYPE_FOR_KIND: dict[str, str] = {
    "execution": "execution",
    "research": "research",
    "intake": "intake",
}

# The tag that opts a task in to being run by an agent at all. Absence means
# no -- so a ticket type nobody has thought about cannot become
# agent-runnable by accident.
AFK_TAG = "afk"


class RecordError(ValueError):
    """A record that cannot be represented. Raised, never degraded --
    the guardian invariants are the documented exception to this system's
    everything-degrades rule, and a task addressed by `id` or carrying an
    unknown kind is exactly that case."""


@dataclasses.dataclass(frozen=True)
class Task:
    """One task, as read back from the store. Frozen: a Task is an
    observation of the store at a moment, not a handle you mutate."""

    uuid: str
    description: str
    status: str
    kind: str | None = None
    project: str | None = None
    url: str | None = None
    parent: str | None = None
    repo: str | None = None
    tags: tuple[str, ...] = ()
    depends: tuple[str, ...] = ()
    annotations: tuple[str, ...] = ()
    urgency: float = 0.0
    entry: str | None = None
    modified: str | None = None
    start: str | None = None

    @property
    def afk(self) -> bool:
        return AFK_TAG in self.tags

    @property
    def active(self) -> bool:
        """Claimed by a session. Taskwarrior's `start` is the claim
        primitive and `+ACTIVE` is the virtual tag for it; `start` being
        set is the same fact read from an export."""
        return self.start is not None

    @property
    def session_type(self) -> str | None:
        """The session type that may resolve this, or None if PU has no
        way to run it."""
        if self.kind is None:
            return None
        return SESSION_TYPE_FOR_KIND.get(self.kind)

    @property
    def runnable(self) -> bool:
        """Whether PU may select this at all. Both conditions, always:
        an explicit AFK opt-in, and a kind with a session type behind it."""
        return self.afk and self.session_type is not None


def from_export(raw: dict[str, Any]) -> Task:
    """Build a Task from one entry of `task export` JSON.

    Absence is normal throughout: Taskwarrior omits empty attributes
    entirely rather than emitting nulls, so every optional field degrades
    to None/() rather than raising. The one thing that does raise is a
    missing uuid, because a record with no identity is not a record."""
    uuid = raw.get("uuid")
    if not uuid:
        raise RecordError("export entry has no uuid")

    annotations = tuple(
        a.get("description", "")
        for a in raw.get("annotations", ())
        if isinstance(a, dict)
    )

    try:
        urgency = float(raw.get("urgency", 0.0))
    except (TypeError, ValueError):
        urgency = 0.0

    # 2.6 exports `depends` as a JSON list; older builds emit a
    # comma-separated string. Accept both -- the shape of this field is the
    # one place the export format has actually moved.
    raw_depends = raw.get("depends") or ()
    if isinstance(raw_depends, str):
        depends = tuple(d for d in raw_depends.split(",") if d)
    else:
        depends = tuple(raw_depends)

    return Task(
        uuid=uuid,
        description=raw.get("description", ""),
        status=raw.get("status", "pending"),
        kind=raw.get("kind"),
        project=raw.get("project"),
        url=raw.get("url"),
        parent=raw.get("parent_uuid"),
        repo=raw.get("repo"),
        tags=tuple(raw.get("tags", ()) or ()),
        depends=depends,
        annotations=annotations,
        urgency=urgency,
        entry=raw.get("entry"),
        modified=raw.get("modified"),
        start=raw.get("start"),
    )


def check_kind(kind: str | None) -> str | None:
    """Reject an unknown kind before it reaches the store.

    Load-bearing, not decorative. Tasks are created through `task import`,
    which validates no UDA value -- hand it `kind: "nonsense"` and it
    writes exactly that. So for this unit's own writes, this function is
    the only thing standing between a typo and a task no session type can
    ever run.

    The binary still enforces the same list on the `task add` path, which
    is what guards a person or a session writing by hand. One vocabulary,
    two enforcement points."""
    if kind is None:
        return None
    if kind not in KINDS:
        raise RecordError(f"unknown kind {kind!r} (allowed: {', '.join(KINDS)})")
    return kind
