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
