"""Stats and logs, as decided 2026-10-26: measures with min/max/avg over
windows, an incremental rollup, trigger and task sections, the unit's
blocked time, pu_event entries, and every attempt recorded."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pu import control, logs_client, pipeline, runner, sessions, stats, triggers

UNIT_ROOT = Path(__file__).resolve().parent.parent


def _run(store, cost, outcome="resolved", tokens=None, role="system",
         started_by="task", queued=None, turns=3):
    store.record("t", "research", "", runner.SessionResult(
        0, cost_usd=cost, duration_ms=60_000, num_turns=turns,
        tokens=tokens or {}), outcome, agent_role=role,
        extra={"started_by": started_by, "queued_seconds": queued})


def test_session_measures_carry_min_max_avg_and_windows(tmp_path):
    s = sessions.SessionStore(tmp_path)
    _run(s, 1.0, tokens={"input_tokens": 10, "cache_read_input_tokens": 90},
         queued=30)
    _run(s, 3.0, outcome="failed", role="finance", started_by="trigger:brief",
         queued=90)
    got = stats.sessions(s)
    assert (got["runs"], got["cost_usd_total"]) == (2, 4.0)
    assert got["all"]["cost"]["min"] == 1.0 and got["all"]["cost"]["max"] == 3.0
    assert got["all"]["cost"]["per_success"] == 4.0
    assert got["today"]["queue_wait_s"]["avg"] == 60.0
    assert got["d7"]["cost"]["p95"] == 3.0
    assert got["all"]["tokens"]["cache_hit_ratio"] == 0.9
    assert set(got["by_role"]) == {"system", "finance"}
    assert got["by_trigger"]["brief"]["all"]["runs"] == 1
    assert got["today"]["throughput"]["runs_per_day"]["max"] == 2


def test_the_rollup_folds_forward_and_rebuilds_to_the_same_numbers(tmp_path):
    s = sessions.SessionStore(tmp_path)
    for cost in (1.0, 2.0, 4.0):
        _run(s, cost)
    folded = json.loads((tmp_path / stats.ROLLUP).read_text())
    assert folded == json.loads(json.dumps(stats.rebuild_rollup(s._results())))
    (tmp_path / stats.ROLLUP).unlink()
    assert stats.sessions(s)["all"]["cost"]["sum"] == 7.0, "rebuilt when missing"


def test_old_transcripts_are_purged_and_records_kept(tmp_path):
    s = sessions.SessionStore(tmp_path)
    old = tmp_path / "t" / "20260101T000000000000Z"
    old.mkdir(parents=True)
    (old / "stream.jsonl").write_text("{}")
    (old / "result.json").write_text("{}")
    _run(s, 1.0)
    assert s.purge_streams(30) == 1
    assert not (old / "stream.jsonl").exists()
    assert (old / "result.json").exists()
    assert s.purge_streams(30) == 0, "once a day"


def test_recent_reads_only_the_window(tmp_path):
    s = sessions.SessionStore(tmp_path)
    old = tmp_path / "t" / "20250101T000000000000Z"
    old.mkdir(parents=True)
    (old / "result.json").write_text(json.dumps({"outcome": "resolved"}))
    _run(s, 1.0)
    assert len(s.recent(30)) == 1


def test_trigger_counters_reach_stats(tmp_path, store, body_store):
    ts = triggers.TriggerStore(tmp_path)
    ts.append({"name": "feed", "enabled": True,
               "source": {"kind": "http-json", "url": "http://x/f.json"},
               "emits": {"kind": "research"}})
    at = lambda: datetime(2026, 10, 26, 8, tzinfo=timezone.utc)
    pipeline.emit_due_work(ts, store, body_store, now=at,
                           fetch=lambda u: (200, json.dumps([{"id": 1}, {"id": 2}]).encode()))
    got = stats.triggers(ts, store, sessions.SessionStore(tmp_path / "s"),
                         now=at())["feed"]
    assert got["fires"]["all"] == 1
    assert got["items_per_fire"] == {"avg": 2.0, "max": 2}
    assert got["fetches"] == {"total": 1, "failed": 0, "failing_now": 0}


def test_tasks_split_agent_work_from_yours(store):
    m = store.add("map", kind="map", project="hub")
    store.add("run me", tags=["afk"], kind="research", project="hub", parent_uuid=m)
    done = store.add("decide", kind="grilling", project="hub", parent_uuid=m)
    store.complete(done)
    got = stats.tasks(store)
    assert got["agent"]["open"] == 1
    assert got["yours"]["flow"]["d30"]["completed"] == 1
    assert got["yours"]["time_to_resolve_days_d30"]["n"] == 1
    assert got["maps"][m[:8]]["open"] == 1


def test_unit_blocked_time_is_swept_from_the_tick_log(tmp_path):
    now = datetime.now(timezone.utc)
    lines = [{"at": (now - timedelta(hours=2)).isoformat(), "ran": False,
              "reason": "paused since x"},
             {"at": (now - timedelta(hours=1)).isoformat(), "ran": False,
              "reason": "nothing runnable"}]
    (tmp_path / control.TICK_LOG).write_text("\n".join(json.dumps(l) for l in lines) + "\n")
    got = stats.unit(tmp_path, {"blocked": False}, now)
    assert 3500 < got["blocked_seconds"]["d7"]["sessions_off"] < 3700


def test_pu_event_has_the_agreed_shape(tmp_path):
    peers = tmp_path / "peers.json"
    peers.write_text(json.dumps({"units": [
        {"base_url": "http://logs", "capabilities": ["log_write"]}]}))
    sent = {}

    class Ok:
        status = 201
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def opener(req, timeout):
        sent.update(json.loads(req.data))
        return Ok()

    assert logs_client.record_event("work_expired", "task", "abc", "not started",
                                    detail={"trigger": "t"}, peers_path=peers,
                                    opener=opener)
    assert sent["entry_type"] == "pu_event"
    assert set(sent["payload"]) == {"event", "at", "subject", "reason", "detail"}
    with pytest.raises(ValueError):
        logs_client.record_event("made_up", "task", "x", "y", peers_path=peers)


def test_a_launch_failure_leaves_a_run_record(store, body_store, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.notify, "notify_owner", lambda *a, **k: True)
    store.add("work", tags=["afk"], kind="research")
    s = sessions.SessionStore(tmp_path / "sessions")

    def explode(**kwargs):
        raise OSError("claude not found")

    pipeline.tick(store, body_store, s, UNIT_ROOT, tmp_path / "peers.json",
                  tmp_path / "cost.json", session_runner=explode,
                  tracker_path=tmp_path / "t.json")
    [run] = s.runs()
    assert run["outcome"] == "launch_failed"
    assert run["failure_class"] == "infrastructure"
