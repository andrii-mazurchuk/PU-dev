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


def test_near_the_cap_the_ceiling_drops_to_one_and_says_so_once(
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

    assert pipeline._ceiling(policy, session_store, today, tmp_path, None) == 1
    assert pipeline._ceiling(policy, session_store, today, tmp_path, None) == 1
    assert len(sent) == 1 and "one session at a time" in sent[0]

    # Without a cap there is no headroom rule: the configured ceiling holds.
    assert pipeline._ceiling({}, session_store, today, tmp_path, None) == 4
    assert "back to running up to 4" in sent[-1]


def test_the_default_is_one_session():
    assert pipeline.max_concurrent({}) == 1
    assert pipeline.max_concurrent({"PU_MAX_CONCURRENT": "nonsense"}) == 1
    assert pipeline.max_concurrent({"PU_MAX_CONCURRENT": "3"}) == 3
