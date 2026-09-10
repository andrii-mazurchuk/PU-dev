"""The panel spec, and the reads behind it.

The spec is data the node renders without asking us anything, so the
failure mode is not an exception -- it is a page that renders blank or a
panel that silently shows nothing. That is what most of this file is
for: the rules the node enforces, restated here, so a spec that would
not render fails in our own suite instead of on someone's screen.

`_is_safe_source` below mirrors `isSafeSource` in the node's
`web/src/lib/spec.ts`. Two statements of one rule is a real cost, and
the alternative -- finding out from a blank panel -- is worse.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import pytest

from pu import dashboard, pipeline, sessions, server

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
UNIT_ROOT = Path(__file__).resolve().parent.parent

# The node's closed vocabulary. A kind outside this renders as an
# "unsupported panel" note rather than failing -- which is the right
# behaviour on their side and a bug on ours.
KNOWN_KINDS = {"kpis", "table", "bars", "rows", "meters", "text"}

STATS_PREFIX = "stats:"


def get(url, **params):
    if params:
        url = f"{url}?{urlencode(params)}"
    with urlopen(url) as resp:
        raw = resp.read().decode("utf-8")
        ctype = resp.headers.get("Content-Type", "")
        return resp.status, (json.loads(raw) if "json" in ctype else raw)


def _is_safe_source(source: str) -> bool:
    """Mirror of the node's isSafeSource."""
    if not isinstance(source, str) or not source:
        return False
    if source.startswith(STATS_PREFIX):
        return bool(re.fullmatch(r"[A-Za-z0-9_.]+", source[len(STATS_PREFIX):]))
    if source.startswith("/") or "\\" in source or "//" in source:
        return False
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", source):
        return False
    return ".." not in source.split("?")[0].split("/")


@pytest.fixture
def spec():
    return dashboard.spec({"five_hour": 70.0, "seven_day": 80.0})


def _panels(spec):
    for page in spec["pages"]:
        for panel in page["panels"]:
            yield page, panel


# -- the spec renders ----------------------------------------------------


def test_spec_declares_this_unit(spec):
    assert spec["unit"] == "pu"
    assert spec["pages"]


def test_every_panel_kind_is_in_the_node_vocabulary(spec):
    for _, panel in _panels(spec):
        assert panel["kind"] in KNOWN_KINDS, panel.get("title")


def test_every_source_is_a_path_on_this_unit(spec):
    """The rule that keeps a spec a description of what to show rather
    than a way to make the node fetch somewhere else."""
    for _, panel in _panels(spec):
        for source in [panel.get("source")] + [
            item.get("source") for item in panel.get("items", [])
        ]:
            if source is not None:
                assert _is_safe_source(source), source


def test_drill_parameters_survive_substitution(spec):
    """A `{param}` is replaced before the node re-checks the result, so a
    source has to still be safe once a uuid is in it."""
    for _, panel in _panels(spec):
        source = panel.get("source")
        if source and "{" in source:
            filled = source.format(uuid="1a2b-3c4d", run_id="20260910T120000000000Z")
            assert _is_safe_source(filled), filled


def test_every_drill_target_is_a_declared_page(spec):
    ids = {page["id"] for page in spec["pages"]}
    for _, panel in _panels(spec):
        detail = panel.get("detail")
        if detail:
            assert detail["page"] in ids, detail


def test_drill_targets_supply_every_parameter_their_page_needs(spec):
    """The failure this catches renders an empty detail page: a page
    whose source wants `{run_id}` opened from a table that binds only
    `{uuid}`."""
    pages = {page["id"]: page for page in spec["pages"]}
    for _, panel in _panels(spec):
        detail = panel.get("detail")
        if not detail:
            continue
        supplied = set(detail.get("params", {}))
        for target_panel in pages[detail["page"]]["panels"]:
            wanted = set(re.findall(r"\{(\w+)\}", target_panel.get("source") or ""))
            assert wanted <= supplied, (detail["page"], wanted - supplied)


def test_detail_pages_are_hidden_and_the_rest_are_not(spec):
    """A detail page in the rail is a page you cannot open, because
    there is no row to open it from."""
    hidden = {p["id"] for p in spec["pages"] if p.get("hidden")}
    assert hidden == {"task", "run"}


def test_tables_declare_columns_and_a_path(spec):
    """A table with no `path` finds no array and renders "nothing
    recorded yet" against data that is present."""
    for _, panel in _panels(spec):
        if panel["kind"] in {"table", "rows"}:
            assert panel.get("columns"), panel.get("title")
            assert panel.get("path"), panel.get("title")


def test_series_panels_name_their_value_field(spec):
    for _, panel in _panels(spec):
        if panel["kind"] in {"bars", "meters"}:
            assert panel.get("y"), panel.get("title")
            assert panel.get("path"), panel.get("title")


def test_meters_carry_the_ceiling_the_gate_enforces():
    """The reason this spec is built and not stored. A static file would
    hard-code the ceilings and draw a line the gate does not enforce the
    day a manifest changed one."""
    built = dashboard.spec({"five_hour": 55.0, "seven_day": 91.0})
    ceilings = [
        panel["ceiling"] for _, panel in _panels(built) if panel["kind"] == "meters"
    ]
    assert ceilings == [55.0, 91.0]


def test_spec_is_json(spec):
    json.dumps(spec)


# -- the reads the panels make -------------------------------------------


@pytest.fixture
def session_store(tmp_path):
    return sessions.SessionStore(tmp_path / "sessions")


def _write_run(store, task_uuid, run_id, **fields):
    run_dir = store.root / task_uuid / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "result.json").write_text(json.dumps(fields), encoding="utf-8")
    (run_dir / "prompt.txt").write_text("do the thing", encoding="utf-8")


def test_runs_are_ordered_by_when_they_finished(session_store):
    """Not by directory name. The stamp is when the directory was made,
    and a session that ran for four minutes finishes out of that order."""
    _write_run(session_store, "aaa", "20260101T000000000000Z",
               recorded_at="2026-01-01T00:04:00Z", cost_usd=1.0)
    _write_run(session_store, "bbb", "20260101T000100000000Z",
               recorded_at="2026-01-01T00:02:00Z", cost_usd=2.0)
    assert [r["run_id"] for r in session_store.runs()] == [
        "20260101T000000000000Z", "20260101T000100000000Z",
    ]


def test_a_run_carries_where_it_was_found(session_store):
    _write_run(session_store, "aaa", "stamp1", recorded_at="2026-01-01T00:00:00Z")
    run = session_store.run("aaa", "stamp1")
    assert run["task_uuid"] == "aaa" and run["run_id"] == "stamp1"
    assert run["prompt"] == "do the thing"


def test_a_run_id_cannot_climb_out_of_the_store(session_store):
    """Both halves arrive from a URL."""
    session_store.root.mkdir(parents=True, exist_ok=True)
    (session_store.root.parent / "result.json").write_text("{}", encoding="utf-8")
    assert session_store.run("..", "..") is None
    assert session_store.run("aaa", "../..") is None
    assert session_store.run("a/b", "c") is None


def test_missing_run_is_none_not_an_error(session_store):
    assert session_store.run("nope", "nope") is None


def test_cost_by_day_keeps_the_quiet_days(session_store):
    """A series that omits them renders as continuous work."""
    days = session_store.cost_by_day(days=5)
    assert len(days) == 5
    assert all(d["cost_usd"] == 0.0 for d in days)
    assert [d["day"] for d in days] == sorted(d["day"] for d in days)


# -- the gate ------------------------------------------------------------


def test_gate_state_reports_clear_when_nothing_blocks(tmp_path, session_store):
    state = pipeline.gate_state(session_store, tmp_path, tmp_path / "absent.json")
    assert state["blocked"] is False
    assert state["reason"] == ""
    assert state["summary"][0]["value"] == "clear"


def test_gate_state_reports_the_cost_cap(tmp_path, session_store):
    policy = tmp_path / "cost_policy.json"
    policy.write_text(json.dumps({"daily_cost_cap_usd": 0.0}), encoding="utf-8")
    state = pipeline.gate_state(session_store, tmp_path, policy)
    assert state["blocked"] is True
    assert "daily cost cap" in state["reason"]


def test_gate_state_reads_the_usage_windows(tmp_path, session_store, monkeypatch):
    monkeypatch.setenv("HOLONIC_CONTEXT_CEILING_FIVE_HOUR", "0.5")
    (tmp_path / "usage.json").write_text(
        json.dumps({"windows": {"five_hour": {"utilization": 0.9}}}), encoding="utf-8"
    )
    state = pipeline.gate_state(session_store, tmp_path, tmp_path / "absent.json")
    assert state["blocked"] is True
    assert state["five_hour"] == [{"name": "five-hour", "percent": 90.0}]
    assert state["ceilings"]["five_hour"] == 50.0


def test_gate_state_survives_an_absent_usage_file(tmp_path, session_store):
    """Absence means allow, deliberately -- a node that has never run a
    session knows nothing, and blocking on that would mean it could never
    run the first."""
    state = pipeline.gate_state(session_store, tmp_path, tmp_path / "absent.json")
    assert state["five_hour"] == []
    assert state["blocked"] is False


# -- the presentation payloads -------------------------------------------


def test_task_fields_flattens_a_body_into_one_line():
    fields = dashboard.task_fields({
        "uuid": "abc", "description": "chart the thing",
        "body": "# A map\n\nwith\nseveral\nlines", "tags": ["afk"],
    })
    body = [f for f in fields["fields"] if f["key"] == "Body"][0]
    assert "\n" not in body["value"]
    assert "tasks/<uuid>/body" in body["note"]


def test_task_fields_omits_the_body_row_when_there_is_none():
    fields = dashboard.task_fields({"uuid": "abc", "description": "x"})
    assert not [f for f in fields["fields"] if f["key"] == "Body"]


def test_run_fields_lists_tool_calls_as_rows():
    payload = dashboard.run_fields({
        "recorded_at": "2026-01-01T00:00:00Z", "cost_usd": 0.5,
        "tool_calls": ["Read", "Edit"], "permission_denials": [],
    })
    assert payload["tool_calls"] == [{"tool": "Read"}, {"tool": "Edit"}]


def test_run_fields_survives_a_torn_record():
    """A result.json written by a process that died mid-write still has
    to render -- this is the view someone opens *because* a run failed."""
    payload = dashboard.run_fields({})
    assert payload["fields"]
    assert payload["tool_calls"] == []


# -- over the wire -------------------------------------------------------


@pytest.fixture
def dash_url(store, body_store, tmp_path):
    httpd = server.build_server(
        "127.0.0.1", 0, store, body_store, PROMPTS_DIR,
        unit_root=UNIT_ROOT,
        session_store=sessions.SessionStore(tmp_path / "sessions"),
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_dashboard_is_served_as_json(dash_url):
    status, payload = get(f"{dash_url}/dashboard")
    assert status == 200
    # The tier is read off the response, never declared -- so the
    # content type is the whole of how the node knows this is a spec.
    assert payload["unit"] == "pu"
    assert payload["pages"]


def test_every_non_parameterised_source_in_the_spec_answers(dash_url):
    """The test that would have caught a panel pointed at a path this
    unit does not serve -- which renders as an empty panel, not an
    error."""
    _, spec_payload = get(f"{dash_url}/dashboard")
    for page in spec_payload["pages"]:
        for panel in page["panels"]:
            source = panel.get("source")
            if not source or "{" in source or source.startswith(STATS_PREFIX):
                continue
            status, _ = get(f"{dash_url}/{source}")
            assert status == 200, source


def test_every_stats_source_in_the_spec_resolves(dash_url):
    """A `stats:` source naming a field that does not exist renders an
    em-dash, not an error -- so the panel looks fine and says nothing.
    This is the only thing that catches it, and it caught two."""
    _, spec_payload = get(f"{dash_url}/dashboard")
    _, stats = get(f"{dash_url}/stats")

    for page in spec_payload["pages"]:
        for panel in page["panels"]:
            sources = [panel.get("source")] + [
                item.get("source") for item in panel.get("items", [])
            ]
            for source in sources:
                if not source or not source.startswith(STATS_PREFIX):
                    continue
                cursor = stats
                for key in source[len(STATS_PREFIX):].split("."):
                    assert isinstance(cursor, dict) and key in cursor, source
                    cursor = cursor[key]
