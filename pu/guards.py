"""The two guards that stop the tick eating itself.

Both exist because of the same property: a failed session **releases** its
claim, which is right -- a task must never be stranded by a session that
died -- but it means the task drops straight back to the top of the queue.
Without these, a task that cannot be resolved is a task that gets retried
forever, and with a schedule behind it that is a loop spending real money.

**The repeat-dispatch breaker** counts how many ticks running the same task
has won selection. Winning repeatedly means it is not resolving, because a
resolved task is closed and cannot win again. Past the threshold it stops
being agent-runnable and a person is told why.

The sibling unit built this after a real incident: one task won selection
eight times and produced eight no-op sessions before anybody noticed.

**The stale-claim reaper** releases claims left behind by a process that
died mid-session. A claim is `+ACTIVE` in the store rather than state in
memory, which is what makes it survive a crash -- and therefore what makes
it need collecting.

Blocking is expressed by removing the `afk` tag, not by inventing a status.
Taskwarrior's statuses are a closed set, and this is the honest encoding
anyway: the task stays open and stays on the frontier a human reads, it
simply stops being something an agent may take. "This needs a person" is
exactly what it means.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pu import records, taskstore

# Three consecutive selections without resolution. Two is too eager -- a
# transient failure deserves one retry -- and much more than three is just
# a more expensive way to reach the same conclusion.
DEFAULT_REPEAT_THRESHOLD = 3

# Long enough for a slow execution session against a large repository,
# short enough that a crash is recovered the same working day.
DEFAULT_STALE_CLAIM_SECONDS = 2 * 60 * 60

BLOCK_PREFIX = "auto-blocked:"


def read_tracker(path: Path) -> dict[str, Any]:
    """The last task to win selection, and how many times running.

    Degrades to "nothing seen yet" on a missing or corrupt file: losing the
    count makes the breaker slower to fire, which is survivable, whereas
    raising here would take down a tick over bookkeeping."""
    try:
        parsed = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"uuid": None, "count": 0}
    if not isinstance(parsed, dict):
        return {"uuid": None, "count": 0}
    return {"uuid": parsed.get("uuid"), "count": int(parsed.get("count") or 0)}


def write_tracker(path: Path, uuid: str | None, count: int) -> None:
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"uuid": uuid, "count": count}),
                        encoding="utf-8")
    except OSError:
        pass


def note_selection(path: Path, uuid: str) -> int:
    """Record that this task won selection; return how many times running.

    A different task winning resets the count, because the question is
    whether *this* task is stuck, not how busy the queue has been."""
    previous = read_tracker(path)
    count = previous["count"] + 1 if previous["uuid"] == uuid else 1
    write_tracker(path, uuid, count)
    return count


def clear_tracker(path: Path) -> None:
    write_tracker(path, None, 0)


def block(store: taskstore.TaskStore, uuid: str, reason: str) -> None:
    """Stop a task being agent-runnable, and say why on the task itself.

    Not deleted and not closed: it stays open and stays visible to a
    person, who is the one who can now do something about it."""
    store.annotate(uuid, f"{BLOCK_PREFIX} {reason}")
    store.modify(uuid, remove_tags=[records.AFK_TAG])


def parse_started_at(value: str | None) -> datetime | None:
    """Taskwarrior stamps `start` as `20260908T101530Z`.

    An unparseable stamp returns None and the claim is left alone --
    reaping on a value we did not understand would be worse than leaving a
    claim in place."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(
            tzinfo=timezone.utc
        )
    except (ValueError, TypeError):
        return None


def release_stale_claims(
    store: taskstore.TaskStore,
    max_age_seconds: int = DEFAULT_STALE_CLAIM_SECONDS,
    now: datetime | None = None,
) -> list[str]:
    """Release claims older than the cutoff. Returns what was released.

    Run at the start of a tick rather than on a timer: it needs no thread,
    it is deterministic to test, and the only moment it matters is just
    before selection anyway."""
    moment = now or datetime.now(timezone.utc)
    cutoff = moment - timedelta(seconds=max_age_seconds)

    released: list[str] = []
    try:
        claimed = store.export("+ACTIVE", "status:pending")
    except taskstore.TaskError:
        return released

    for task in claimed:
        started = parse_started_at(task.start)
        if started is None or started > cutoff:
            continue
        age = int((moment - started).total_seconds())
        try:
            store.release(task.uuid)
            store.annotate(
                task.uuid,
                f"claim released: held {age}s with no result, so the session "
                f"holding it is gone",
            )
            released.append(task.uuid)
        except taskstore.TaskError:
            continue
    return released
