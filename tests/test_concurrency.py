"""Concurrency: N sessions, one per repository, and one at a time near
the cost cap. Default N is 1, so none of this changes behaviour until the
gateway raises PU_MAX_CONCURRENT."""

from __future__ import annotations

import json
from pathlib import Path

from pu import pipeline, runner, sessions

UNIT_ROOT = Path(__file__).resolve().parent.parent


def _tick(store, body_store, tmp_path, session_runner, **kw):
    return pipeline.tick(
        store, body_store, sessions.SessionStore(tmp_path / "sessions"),
        UNIT_ROOT, tmp_path / "peers.json", kw.pop("cost", tmp_path / "cost.json"),
        session_runner=session_runner,
        tracker_path=tmp_path / "repeat_tracker.json", **kw)


def test_two_sessions_run_at_once_when_the_ceiling_allows(
        store, body_store, tmp_path, monkeypatch):
    monkeypatch.setenv("PU_MAX_CONCURRENT", "2")
    store.add("first", tags=["afk"], kind="research")
    store.add("second", tags=["afk"], kind="research")
    inner = {}

    def outer_session(**kwargs):
        inner["result"] = _tick(store, body_store, tmp_path,
                                lambda **k: runner.SessionResult(0, text="b"))
        return runner.SessionResult(0, text="a", is_error=False)

    assert _tick(store, body_store, tmp_path, outer_session).ran is True
    assert inner["result"].ran is True
    assert inner["result"].task_uuid != None


def test_two_tasks_on_one_repo_never_run_at_once(
        store, body_store, tmp_path, monkeypatch):
    """A session is handed the repo itself as cwd; two would share a tree,
    an index and a branch. Whatever the ceiling."""
    monkeypatch.setenv("PU_MAX_CONCURRENT", "5")
    repo = tmp_path / "target"
    (repo / ".git").mkdir(parents=True)
    store.add("build a", tags=["afk"], kind="execution", repo=str(repo))
    store.add("build b", tags=["afk"], kind="execution", repo=str(repo))
    inner = {}

    def outer_session(**kwargs):
        inner["result"] = _tick(store, body_store, tmp_path,
                                lambda **k: runner.SessionResult(0, text="b"))
        return runner.SessionResult(0, text="a", is_error=False)

    assert _tick(store, body_store, tmp_path, outer_session).ran is True
    assert inner["result"].ran is False
    # Waiting for the repo is not being stuck: nothing was auto-blocked.
    assert inner["result"].outcome is None


def test_within_the_runway_of_the_cap_the_ceiling_drops_to_one_once(
        tmp_path, monkeypatch):
    monkeypatch.setenv("PU_MAX_CONCURRENT", "4")
    sent = []
    monkeypatch.setattr(pipeline.notify, "notify_owner",
                        lambda url, text, **k: sent.append(text) or True)
    session_store = sessions.SessionStore(tmp_path / "s")
    session_store.record("t", "research", "", runner.SessionResult(0, cost_usd=8.5),
                         "resolved")
    today = session_store.runs()[0]["recorded_at"][:10]
    policy = {"daily_cost_cap_usd": 10}

    # 8.50 of 10 is inside the default 15% runway.
    assert pipeline._ceiling(policy, session_store, today, tmp_path, None) == 1
    assert pipeline._ceiling(policy, session_store, today, tmp_path, None) == 1
    assert len(sent) == 1 and "budget is running low" in sent[0]

    # Without a cap there is no budget runway: the configured ceiling holds.
    assert pipeline._ceiling({}, session_store, today, tmp_path, None) == 4
    assert "recovered" in sent[-1]


def test_a_usage_window_inside_its_runway_also_drops_to_one(tmp_path, monkeypatch):
    monkeypatch.setenv("PU_MAX_CONCURRENT", "3")
    monkeypatch.setattr(pipeline.notify, "notify_owner", lambda *a, **k: True)
    session_store = sessions.SessionStore(tmp_path / "s")
    # 0.62 against a 0.70 ceiling is past 85% of it.
    assert pipeline._ceiling({}, session_store, "2026-10-25", tmp_path, None,
                             {"five_hour": {"utilization": 0.62}},
                             {"five_hour": 0.70}) == 1
    assert pipeline._ceiling({}, session_store, "2026-10-25", tmp_path, None,
                             {"five_hour": {"utilization": 0.30}},
                             {"five_hour": 0.70}) == 3


def test_running_sessions_count_at_their_type_s_average(tmp_path):
    """The reserve: N sessions in flight cannot carry spend past the cap
    unseen."""
    session_store = sessions.SessionStore(tmp_path / "s")
    for cost in (2.0, 4.0):
        session_store.record("t", "research", "", runner.SessionResult(0, cost_usd=cost),
                             "resolved")
    with pipeline._RUNNING_LOCK:
        pipeline._RUNNING["a"] = {"session_type": "research"}
        pipeline._RUNNING["b"] = {"session_type": "brand-new"}
    try:
        # research averages 3.0; a type with no history counts at the overall 3.0.
        assert pipeline.reserved_cost(session_store) == 6.0
        today = session_store.runs()[0]["recorded_at"][:10]
        assert pipeline.check_cost_gate({"daily_cost_cap_usd": 10}, session_store,
                                        today, reserved=6.0) is not None
    finally:
        with pipeline._RUNNING_LOCK:
            pipeline._RUNNING.clear()


def test_the_live_ceiling_outranks_the_env_and_survives_a_restart(
        tmp_path, monkeypatch):
    from pu import control

    monkeypatch.setenv("PU_MAX_CONCURRENT", "2")
    assert pipeline.max_concurrent(state_dir=tmp_path) == 2
    control.set_max_concurrent(tmp_path, 5)
    assert pipeline.max_concurrent(state_dir=tmp_path) == 5


def test_queue_runs_high_before_medium_before_low_then_oldest_first(store):
    low = store.add("flexible", tags=["afk"], kind="research", priority="L")
    first = store.add("first fired", tags=["afk"], kind="execution")
    high = store.add("the 08:00 brief", tags=["afk"], kind="research", priority="H")
    second = store.add("second fired", tags=["afk"], kind="research")
    assert [t.uuid for t in store.runnable()] == [high, first, second, low]


def test_stale_trigger_work_expires_unstarted_and_says_so(
        store, body_store, tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    sent = []
    monkeypatch.setattr(pipeline.notify, "notify_owner",
                        lambda url, text, **k: sent.append(text) or True)
    now = datetime(2026, 10, 25, 14, tzinfo=timezone.utc)
    old = store.add("08:00 brief", tags=["afk"], kind="research", project="pu.runs",
                    expires=(now - timedelta(hours=5)).isoformat())
    started = store.add("long run", tags=["afk"], kind="research", project="pu.runs",
                        expires=(now - timedelta(hours=5)).isoformat())
    store.claim(started)
    fresh = store.add("still fine", tags=["afk"], kind="research", project="pu.runs",
                      expires=(now + timedelta(hours=1)).isoformat())

    pipeline._expire_stale(store, now, None)
    assert store.get(old).status == "completed"
    assert any(a.startswith("expired:") for a in store.get(old).annotations)
    assert store.get(started).status == "pending", "a started run is never cut off"
    assert store.get(fresh).status == "pending"
    assert len(sent) == 1


def test_the_default_is_one_session():
    assert pipeline.max_concurrent({}) == 1
    assert pipeline.max_concurrent({"PU_MAX_CONCURRENT": "nonsense"}) == 1
    assert pipeline.max_concurrent({"PU_MAX_CONCURRENT": "3"}) == 3


def test_expired_work_is_tagged_never_run_and_counted(store, body_store, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from pu import service

    monkeypatch.setattr(pipeline.notify, "notify_owner", lambda *a, **k: True)
    now = datetime(2026, 10, 25, 14, tzinfo=timezone.utc)
    old = store.add("08:00 brief", tags=["afk"], kind="research", project="pu.runs",
                    trigger="morning", expires=(now - timedelta(hours=5)).isoformat())
    pipeline._expire_stale(store, now, None)
    task = store.get(old)
    assert pipeline.EXPIRED_TAG in task.tags and task.status == "completed"
    assert service.stats(store, body_store)["expired_runs"] == {"morning": 1}


def test_high_priority_goes_stale_in_an_hour_by_default(store, body_store, tmp_path):
    from datetime import datetime, timezone

    from pu import triggers

    trigger_store = triggers.TriggerStore(tmp_path)
    trigger_store.append({"name": "brief", "enabled": True,
                          "source": {"kind": "schedule", "every_seconds": 86400},
                          "emits": {"kind": "research", "priority": "high"}})
    at = lambda: datetime(2026, 10, 25, 8, tzinfo=timezone.utc)
    [made] = pipeline.emit_due_work(trigger_store, store, body_store, now=at)
    assert store.get(made["task_uuid"]).expires.startswith("2026-10-25T09:00")
