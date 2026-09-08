"""The two guards that stop the tick eating itself."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from pu import guards, pipeline, records, runner, sessions

UNIT_ROOT = Path(__file__).resolve().parent.parent


def _session_store(tmp_path):
    return sessions.SessionStore(tmp_path / "sessions")


def _tick(store, body_store, tmp_path, session_runner, **kw):
    return pipeline.tick(
        store, body_store, _session_store(tmp_path), UNIT_ROOT,
        tmp_path / "peers.json", tmp_path / "cost_policy.json",
        session_runner=session_runner,
        tracker_path=tmp_path / "repeat_tracker.json",
        **kw,
    )


# -- the repeat-dispatch breaker ------------------------------------------


def test_the_count_resets_when_a_different_task_wins(tmp_path):
    """The question is whether *this* task is stuck, not how busy the
    queue has been."""
    path = tmp_path / "t.json"
    assert guards.note_selection(path, "a") == 1
    assert guards.note_selection(path, "a") == 2
    assert guards.note_selection(path, "b") == 1
    assert guards.note_selection(path, "a") == 1


def test_a_corrupt_tracker_degrades_to_nothing_seen(tmp_path):
    path = tmp_path / "t.json"
    path.write_text("{not json", encoding="utf-8")
    assert guards.read_tracker(path) == {"uuid": None, "count": 0}
    assert guards.note_selection(path, "a") == 1


def test_a_task_that_never_resolves_is_blocked_not_retried_forever(
    store, body_store, tmp_path
):
    """The failure this exists for: a failed session releases its claim, so
    the task returns to the top of the queue and is picked again. With a
    schedule behind it, that is a loop spending real money."""
    uuid = store.add("cannot be done", tags=["afk"], kind="research")
    runs = {"n": 0}

    def always_fails(**kwargs):
        runs["n"] += 1
        return runner.SessionResult(1, text="")

    first = _tick(store, body_store, tmp_path, always_fails)
    assert first.outcome == "failed"
    second = _tick(store, body_store, tmp_path, always_fails)
    assert second.outcome == "failed"

    third = _tick(store, body_store, tmp_path, always_fails)
    assert third.ran is False
    assert third.outcome == "auto_blocked"

    # Two attempts, then stop -- the third tick spends nothing.
    assert runs["n"] == 2

    task = store.get(uuid)
    assert task.afk is False, "must no longer be agent-runnable"
    assert task.runnable is False
    assert task.status == "pending", "still open: a person can act on it"
    assert any(a.startswith(guards.BLOCK_PREFIX) for a in task.annotations)

    # And it stays visible to whoever reads the frontier.
    assert uuid in [t.uuid for t in store.ready()]

    # A further tick finds nothing rather than picking it up again.
    assert _tick(store, body_store, tmp_path, always_fails).reason == "nothing runnable"


def test_a_task_that_resolves_first_time_is_never_blocked(
    store, body_store, tmp_path
):
    store.add("fine", tags=["afk"], kind="research")
    result = _tick(store, body_store, tmp_path,
                   lambda **k: runner.SessionResult(0, text="done", is_error=False))
    assert result.outcome == "resolved"


# -- the stale-claim reaper -----------------------------------------------


def test_a_fresh_claim_is_left_alone(store):
    uuid = store.add("work", tags=["afk"], kind="research")
    store.claim(uuid)
    assert guards.release_stale_claims(store, max_age_seconds=3600) == []
    assert store.get(uuid).active is True


def test_a_claim_older_than_the_cutoff_is_released(store):
    """A claim is store state, not memory, which is what makes it survive a
    crash -- and therefore what makes it need collecting."""
    uuid = store.add("work", tags=["afk"], kind="research")
    store.claim(uuid)

    later = datetime.now(timezone.utc) + timedelta(hours=5)
    released = guards.release_stale_claims(store, max_age_seconds=3600, now=later)

    assert released == [uuid]
    task = store.get(uuid)
    assert task.active is False
    assert any("claim released" in a for a in task.annotations)
    # Released, not resolved: the work still needs doing.
    assert task.status == "pending"
    assert task.runnable is True


def test_an_unparseable_start_stamp_leaves_the_claim_alone():
    """Reaping on a value we did not understand is worse than leaving it."""
    assert guards.parse_started_at("not a timestamp") is None
    assert guards.parse_started_at(None) is None
    assert guards.parse_started_at("20260908T101530Z") == datetime(
        2026, 9, 8, 10, 15, 30, tzinfo=timezone.utc
    )


def test_a_tick_collects_a_stale_claim_before_selecting(
    store, body_store, tmp_path
):
    """A task stranded by a dead process must become selectable again."""
    uuid = store.add("stranded", tags=["afk"], kind="research")
    store.claim(uuid)
    assert store.runnable() == [], "claimed work is out of the queue"

    later = datetime.now(timezone.utc) + timedelta(hours=5)
    result = _tick(
        store, body_store, tmp_path,
        lambda **k: runner.SessionResult(0, text="picked it up", is_error=False),
        now=lambda: later,
    )
    assert result.ran is True
    assert result.task_uuid == uuid
    assert store.get(uuid).status == "completed"


# -- one tick at a time ----------------------------------------------------


def test_an_overlapping_tick_declines_rather_than_racing(
    store, body_store, tmp_path
):
    """Two sessions at once is not a busier unit, it is two sessions racing
    for the same top-of-queue task."""
    store.add("work", tags=["afk"], kind="research")
    inner: dict = {}

    def reentrant(**kwargs):
        # Simulates the next scheduled poke arriving mid-session.
        inner["result"] = _tick(store, body_store, tmp_path,
                                lambda **k: runner.SessionResult(0, text="x"))
        return runner.SessionResult(0, text="done", is_error=False)

    outer = _tick(store, body_store, tmp_path, reentrant)
    assert outer.ran is True
    assert inner["result"].ran is False
    assert inner["result"].reason == "a tick is already running"


def test_blocking_removes_only_the_afk_tag(store):
    uuid = store.add("work", tags=["afk", "integration"], kind="research")
    guards.block(store, uuid, "because")
    task = store.get(uuid)
    assert records.AFK_TAG not in task.tags
    assert "integration" in task.tags, "procedure tags are not the breaker's business"
