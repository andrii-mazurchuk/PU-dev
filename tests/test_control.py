"""The kill switch and the why-not log."""

from __future__ import annotations

import threading
from pathlib import Path
from urllib.request import urlopen

import json

from pu import control, pipeline, runner, server, sessions

UNIT_ROOT = Path(__file__).resolve().parent.parent


def _tick(store, body_store, tmp_path, runner_fn):
    return pipeline.tick(
        store, body_store, sessions.SessionStore(tmp_path / "sessions"),
        UNIT_ROOT, tmp_path / "peers.json", tmp_path / "cost_policy.json",
        session_runner=runner_fn, tracker_path=tmp_path / "tracker.json",
    )


def test_paused_launches_nothing_and_says_so(store, body_store, tmp_path):
    store.add("work", tags=["afk"], kind="research")
    control.set_paused(tmp_path, True, "testing the switch")

    result = _tick(store, body_store, tmp_path,
                   lambda **k: (_ for _ in ()).throw(AssertionError("ran")))
    assert result.ran is False
    assert "paused" in result.reason and "testing the switch" in result.reason

    control.set_paused(tmp_path, False)
    resumed = _tick(store, body_store, tmp_path,
                    lambda **k: runner.SessionResult(0, text="ok", is_error=False))
    assert resumed.ran is True


def test_the_gate_reading_reports_a_pause_first(tmp_path):
    control.set_paused(tmp_path, True, "maintenance")
    gate = pipeline.gate_state(sessions.SessionStore(tmp_path / "s"), UNIT_ROOT,
                               tmp_path / "cost_policy.json", state_dir=tmp_path)
    assert gate["blocked"] is True
    assert gate["summary"][0]["value"] == "paused"
    assert "maintenance" in gate["reason"]


def test_the_log_records_decisions_not_repeats(tmp_path):
    blocked = {"ran": False, "reason": "daily cost cap reached"}
    assert control.note_tick(tmp_path, blocked) is True
    assert control.note_tick(tmp_path, blocked) is False, "a repeat is not news"
    assert control.note_tick(tmp_path, {"ran": False, "reason": "nothing runnable"})
    reasons = [t["reason"] for t in control.ticks(tmp_path)]
    assert reasons == ["nothing runnable", "daily cost cap reached"]


def test_every_tick_leaves_its_decision(store, body_store, tmp_path):
    _tick(store, body_store, tmp_path, lambda **k: None)
    [entry] = control.ticks(tmp_path)
    assert entry["reason"] == "nothing runnable" and "at" in entry


def test_pause_and_ticks_over_http(store, body_store, tmp_path):
    httpd = server.build_server("127.0.0.1", 0, store, body_store,
                                UNIT_ROOT / "prompts", state_dir=tmp_path)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        from urllib.request import Request
        req = Request(f"{base}/pause", data=json.dumps({"paused": True}).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(req) as resp:
            assert json.loads(resp.read())["paused"] is True
        assert control.paused(tmp_path)
        control.note_tick(tmp_path, {"ran": False, "reason": "x"})
        with urlopen(f"{base}/ticks") as resp:
            assert json.loads(resp.read())["ticks"][0]["reason"] == "x"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_long_gap_between_ticks_is_reported_once(tmp_path):
    from datetime import datetime, timedelta, timezone

    t0 = datetime(2026, 10, 24, 8, 0, tzinfo=timezone.utc)
    assert control.silence(tmp_path, t0) is None, "first tick: nothing to compare"
    assert control.silence(tmp_path, t0 + timedelta(seconds=30)) is None
    gap = control.silence(tmp_path, t0 + timedelta(hours=2))
    assert gap is not None and gap > 3600
    assert control.silence(tmp_path, t0 + timedelta(hours=2, seconds=30)) is None


def test_the_heartbeat_is_read_not_assumed():
    assert control.heartbeat_seconds({"PU_HEARTBEAT_SECONDS": "15"}) == 15
    assert control.heartbeat_seconds({}) == 30
    assert control.heartbeat_seconds({"PU_HEARTBEAT_SECONDS": "nope"}) == 30
