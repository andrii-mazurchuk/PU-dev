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
    # Retry once, then hand it to a person (pu vision, 2026-10-24) -- the
    # failure policy reaches this before the repeat breaker, which stays
    # as the backstop for a task that is selected and never even runs.
    second = _tick(store, body_store, tmp_path, always_fails)
    assert second.outcome == "failed_twice"

    third = _tick(store, body_store, tmp_path, always_fails)
    assert third.ran is False

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
    assert result.outcome.startswith("resolved")


# -- stale claims: reported, never released -------------------------------


def test_a_stale_run_claim_is_reported_once_and_never_released(store, tmp_path):
    run = store.add("digest: fired", tags=["afk"], kind="research",
                    project="pu.runs")
    mine = store.add("a person's task", kind="task", project="hub")
    store.claim(run)
    store.claim(mine)

    later = datetime.now(timezone.utc) + timedelta(days=2)
    state = tmp_path / "stale.json"
    assert [t.uuid for t in guards.newly_stale(store, state, now=later)] == [run]
    assert guards.newly_stale(store, state, now=later) == [], "once"

    # Nothing is released -- neither the run-task nor, above all, the
    # person's own claim, which is not even watched.
    assert store.get(run).active is True
    assert store.get(mine).active is True


def test_a_fresh_run_claim_is_not_stale(store):
    run = store.add("digest: fired", kind="research", project="pu.runs")
    store.claim(run)
    assert guards.stale_run_claims(store) == []


def test_an_unparseable_start_stamp_leaves_the_claim_alone():
    """Reaping on a value we did not understand is worse than leaving it."""
    assert guards.parse_started_at("not a timestamp") is None
    assert guards.parse_started_at(None) is None
    assert guards.parse_started_at("20260908T101530Z") == datetime(
        2026, 9, 8, 10, 15, 30, tzinfo=timezone.utc
    )


def test_a_tick_never_takes_a_claim_away(store, body_store, tmp_path):
    """Someone else's claim stays theirs, however old. A run pu itself lost
    is recovered by the in-flight marker instead (see crash recovery)."""
    uuid = store.add("someone is on it", tags=["afk"], kind="research")
    store.claim(uuid)

    later = datetime.now(timezone.utc) + timedelta(days=3)
    result = _tick(
        store, body_store, tmp_path,
        lambda **k: runner.SessionResult(0, text="should not run", is_error=False),
        now=lambda: later,
    )
    assert result.ran is False
    assert store.get(uuid).active is True


# -- one tick at a time ----------------------------------------------------


def test_a_poke_mid_session_is_refused_at_the_default_ceiling_of_one(
    store, body_store, tmp_path
):
    """The lock covers deciding, not running, so the next poke gets in --
    and at the default ceiling of one, it starts nothing."""
    store.add("work", tags=["afk"], kind="research")
    store.add("more work", tags=["afk"], kind="research")
    inner: dict = {}

    def reentrant(**kwargs):
        # Simulates the next scheduled poke arriving mid-session.
        inner["result"] = _tick(store, body_store, tmp_path,
                                lambda **k: runner.SessionResult(0, text="x"))
        return runner.SessionResult(0, text="done", is_error=False)

    outer = _tick(store, body_store, tmp_path, reentrant)
    assert outer.ran is True
    assert inner["result"].ran is False
    assert "concurrency ceiling" in inner["result"].reason


def test_blocking_removes_only_the_afk_tag(store):
    uuid = store.add("work", tags=["afk", "integration"], kind="research")
    guards.block(store, uuid, "because")
    task = store.get(uuid)
    assert records.AFK_TAG not in task.tags
    assert "integration" in task.tags, "procedure tags are not the breaker's business"


# -- crash recovery -------------------------------------------------------


def test_a_run_lost_to_a_restart_is_released_and_recorded(
        store, body_store, tmp_path):
    """pu died mid-session: the claim is still held and the marker is still
    there. The next tick must admit the run was lost, not leave a ghost."""
    import json

    from pu import pipeline, sessions

    uuid = store.add("long job", tags=["afk"], kind="research")
    store.claim(uuid)
    (tmp_path / "inflight.json").write_text(json.dumps({
        "task_uuid": uuid, "session_type": "research",
        "agent_role": "system", "started_at": "2026-10-24T10:00:00+00:00",
    }), encoding="utf-8")

    session_store = sessions.SessionStore(tmp_path / "sessions")
    pipeline.tick(
        store, body_store, session_store, Path(__file__).resolve().parent.parent,
        tmp_path / "peers.json", tmp_path / "cost_policy.json",
        session_runner=lambda **k: runner.SessionResult(1, text=""),
        tracker_path=tmp_path / "tracker.json",
    )

    lost = [r for r in session_store.runs() if r["outcome"] == "lost"]
    assert len(lost) == 1 and lost[0]["task_uuid"] == uuid
    assert not (tmp_path / "inflight.json").exists()
    task = store.get(uuid)
    assert any("run lost" in a for a in task.annotations)
