"""What pu exposes, as decided 2026-10-25: running sessions, what waits on
a person, spend by period, and no spending clock in agents' hands."""

from __future__ import annotations

from pathlib import Path

from pu import guards, pipeline, runner, server, service, sessions, typestore


def test_agents_cannot_drive_the_spending_clock():
    names = {t["name"] for t in server.TOOLS}
    assert "run_tick" not in names
    assert {"list_running", "needs_attention", "get_spend"} <= names


def test_list_running_reads_the_registry():
    with pipeline._RUNNING_LOCK:
        pipeline._RUNNING["abc"] = {"session_type": "research", "agent_role": "system",
                                    "started_at": "2026-10-25T10:00:00+00:00",
                                    "repo": "/secret/path"}
    try:
        [run] = pipeline.running()
        assert run["task_uuid"] == "abc" and run["session_type"] == "research"
        assert run["elapsed_seconds"] > 0
        assert "repo" not in run
    finally:
        with pipeline._RUNNING_LOCK:
            pipeline._RUNNING.clear()


def test_handing_a_task_to_a_person_tags_it(store):
    uuid = store.add("stuck", tags=["afk"], kind="research")
    guards.block(store, uuid, "failed twice")
    assert guards.NEEDS_HUMAN_TAG in store.get(uuid).tags


def test_needs_attention_keeps_human_work_and_faults_apart(store, tmp_path):
    m = store.add("a map", kind="map", project="hub")
    decision = store.add("decide it", kind="grilling", project="hub", parent_uuid=m)
    handed = store.add("stuck", tags=["afk"], kind="research", project="hub",
                       parent_uuid=m)
    guards.block(store, handed, "failed twice")
    store.add("loose", kind="task", project="hub")          # a placement fault
    types = typestore.TypeStore(tmp_path)
    types.append({"name": "draft", "purpose": "p", "prompt": "x\n",
                  "grant": "read", "origin": "authored"})

    got = service.needs_attention(store, types, None)
    waiting = got["waiting_on_you"]
    assert [r["uuid"] for r in waiting["decisions"]] == [decision]
    assert [r["uuid"] for r in waiting["handed_to_you"]] == [handed]
    assert [r["name"] for r in waiting["never_activated"]] == ["draft"]
    assert [r["title"] for r in got["faults"]["mapless"]] == ["loose"]


def test_spend_counts_each_period_so_no_session_counts_by_hand(tmp_path):
    """Live 2026-11-05: the brief counted a day's runs from rows too large to
    read and reported 10 where pu had 9. The count is pu's now."""
    store = sessions.SessionStore(tmp_path)
    for outcome in ("resolved", "resolved", "failed", "needs_human"):
        store.record("t", "research", "", runner.SessionResult(0, cost_usd=1.0), outcome)
    today = store.runs()[0]["recorded_at"][:10]
    period = store.cost_by_period("day", today, today)["periods"][0]
    assert period["runs"] == 4
    assert period["outcomes"] == {"resolved": 2, "failed": 1, "needs_human": 1}
    empty = store.cost_by_period("day", "2026-10-01", "2026-10-01")["periods"][0]
    assert empty["runs"] == 0 and empty["outcomes"] == {}


def test_runs_filter_by_session_type_before_the_limit(tmp_path):
    store = sessions.SessionStore(tmp_path)
    store.record("a", "ai-news", "", runner.SessionResult(0), "resolved")
    for _ in range(3):
        store.record("b", "other", "", runner.SessionResult(0), "resolved")
    got = store.runs(limit=1, session_type="ai-news")
    assert [r["session_type"] for r in got] == ["ai-news"]


def test_spend_totals_by_week_with_empty_weeks_as_zero(tmp_path):
    store = sessions.SessionStore(tmp_path)
    store.record("t", "research", "", runner.SessionResult(0, cost_usd=2.5), "resolved")
    today = store.runs()[0]["recorded_at"][:10]
    got = store.cost_by_period("week", None, today)
    assert len(got["periods"]) == 8
    assert got["periods"][-1]["cost_usd"] == 2.5
    assert sum(p["cost_usd"] for p in got["periods"][:-1]) == 0
    assert got["total_usd"] == 2.5
    assert len(store.cost_by_period("month", None, today)["periods"]) == 6
    assert len(store.cost_by_period("day", "2026-10-01", "2026-10-07")["periods"]) == 7


def test_a_summary_row_leaves_out_what_makes_a_row_large(store, body_store, tmp_path):
    import json
    import threading
    from pathlib import Path
    from urllib.request import urlopen
    from pu import server

    runs = sessions.SessionStore(tmp_path / "s")
    runs.record("t", "ai-news", "", runner.SessionResult(0, text="long report"), "resolved")
    runs.record("u", "other", "", runner.SessionResult(0), "resolved")
    httpd = server.build_server(
        "127.0.0.1", 0, store, body_store,
        Path(__file__).resolve().parent.parent / "prompts", session_store=runs)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        rows = json.load(urlopen(f"{base}/sessions?session_type=ai-news&summary=true"))["sessions"]
        full = json.load(urlopen(f"{base}/sessions?session_type=ai-news"))["sessions"]
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert [r["session_type"] for r in rows] == ["ai-news"]
    assert not set(server.SUMMARY_LEAVES_OUT) & set(rows[0])
    assert "tool_calls" in full[0]
