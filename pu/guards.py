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

**Stale claims are reported, never released** (pu vision follow-up,
2026-10-24). The old reaper released any claim held two hours, whoever
held it -- which meant pu took claims away from people and interactive
sessions, since pu's own runs can never be reaped mid-run (the tick lock)
and a pu run that died is caught on the next tick by the in-flight marker.
So releasing is a person's decision. What is left here is watching: a
claim on a trigger-created task (`pu.runs`) held past a day is counted and
reported once. People's own tasks are not watched at all.

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

# A day: no session runs that long, so a run-task still claimed after one
# was claimed by something that is gone.
DEFAULT_STALE_CLAIM_SECONDS = 24 * 60 * 60

BLOCK_PREFIX = "auto-blocked:"
# An underscore, not a hyphen: the CLI reads `+needs-human` as arithmetic,
# fails, and in one case overwrote the task's description with it.
NEEDS_HUMAN_TAG = "needs_human"


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
    # `needs_human` makes "handed to a person" something a query can find,
    # not only something an annotation says.
    store.modify(uuid, add_tags=[NEEDS_HUMAN_TAG],
                 remove_tags=[records.AFK_TAG])


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


def stale_run_claims(
    store: taskstore.TaskStore,
    max_age_seconds: int = DEFAULT_STALE_CLAIM_SECONDS,
    now: datetime | None = None,
) -> list[records.Task]:
    """Trigger-created tasks claimed longer than the cutoff. Read only."""
    moment = now or datetime.now(timezone.utc)
    cutoff = moment - timedelta(seconds=max_age_seconds)
    try:
        claimed = store.export("+ACTIVE", "status:pending",
                               f"project:{records.RUNS_PROJECT}")
    except taskstore.TaskError:
        return []
    return [t for t in claimed
            if (started := parse_started_at(t.start)) is not None
            and started <= cutoff]


def newly_stale(
    store: taskstore.TaskStore,
    state_path: Path,
    max_age_seconds: int = DEFAULT_STALE_CLAIM_SECONDS,
    now: datetime | None = None,
) -> list[records.Task]:
    """The stale run claims not reported before, remembered so each is
    reported once. Nothing is released: that is a person's call."""
    stale = stale_run_claims(store, max_age_seconds, now)
    try:
        told = set(json.loads(Path(state_path).read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        told = set()
    fresh = [t for t in stale if t.uuid not in told]
    try:
        Path(state_path).parent.mkdir(parents=True, exist_ok=True)
        # Only what is still stale is kept, so the file cannot grow forever.
        Path(state_path).write_text(json.dumps(sorted(t.uuid for t in stale)),
                                    encoding="utf-8")
    except OSError:
        pass
    return fresh


# How long a finished run-task stays. The run record -- the report -- is
# kept forever; the task was only ever the queue entry that carried it.
RUN_RETENTION_DAYS = 30
PRUNE_EVERY_SECONDS = 24 * 60 * 60


def prune_runs(
    store: taskstore.TaskStore,
    state_path: Path,
    now: datetime | None = None,
    retention_days: int = RUN_RETENTION_DAYS,
) -> list[str]:
    """Delete completed run-tasks older than the retention, once a day.

    Throttled through a state file rather than run every tick: at a 30s
    heartbeat that is ~2,900 exports a day to find nothing. Returns what
    was deleted."""
    moment = now or datetime.now(timezone.utc)
    try:
        last = json.loads(Path(state_path).read_text(encoding="utf-8")).get("at")
        if last and (moment - datetime.fromisoformat(last)).total_seconds() \
                < PRUNE_EVERY_SECONDS:
            return []
    except (OSError, ValueError, AttributeError, TypeError):
        pass

    cutoff = (moment - timedelta(days=retention_days)).strftime("%Y-%m-%d")
    deleted: list[str] = []
    try:
        old = store.export("status:completed", f"project:{records.RUNS_PROJECT}",
                           f"end.before:{cutoff}")
    except taskstore.TaskError:
        return deleted
    for task in old:
        try:
            store.delete(task.uuid)
            deleted.append(task.uuid)
        except taskstore.TaskError:
            continue
    try:
        Path(state_path).parent.mkdir(parents=True, exist_ok=True)
        Path(state_path).write_text(json.dumps({"at": moment.isoformat()}),
                                    encoding="utf-8")
    except OSError:
        pass
    return deleted
