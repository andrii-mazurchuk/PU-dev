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

from pu import ask, dashboard, pipeline, sessions, server

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
UNIT_ROOT = Path(__file__).resolve().parent.parent

# The node's closed vocabulary. A kind outside this renders as an
# "unsupported panel" note rather than failing -- which is the right
# behaviour on their side and a bug on ours.
KNOWN_KINDS = {"kpis", "table", "record", "bars", "rows", "meters", "text", "ask"}

# `ask` is the one kind here that a node may not have learned yet -- it
# was added to the standard for this unit. An older node renders it as an
# "unsupported panel" note and draws every other panel normally, which is
# the degradation rule working as intended and not a reason to hold the
# panel back.
EXTENSION_KINDS = {"ask"}

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
    return dashboard.spec()


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


def test_record_panels_read_an_object_not_an_array(spec):
    """`record` renders one object. A `path` on it would be a leftover
    from the array-shaped panel it replaced."""
    for _, panel in _panels(spec):
        if panel["kind"] == "record":
            assert panel.get("source"), panel.get("title")
            assert "path" not in panel, panel.get("title")


def test_no_panel_reads_a_document_without_saying_which_part(spec):
    """A source with no `field` on a panel that wants one value renders
    the whole document -- the bug that put two presentation endpoints in
    this unit before `field` existed."""
    for _, panel in _panels(spec):
        for item in panel.get("items", []):
            source = item.get("source") or ""
            if source and not source.startswith(STATS_PREFIX):
                assert item.get("field"), item.get("label")


def test_series_panels_name_their_value_field(spec):
    for _, panel in _panels(spec):
        if panel["kind"] in {"bars", "meters"}:
            assert panel.get("y"), panel.get("title")
            assert panel.get("path"), panel.get("title")


def test_meters_take_their_ceiling_from_the_row(spec):
    """The two account-usage windows are separate limits, not an
    average. Under one panel-wide ceiling the five-hour window read
    "clear" while the gate was actively blocking on it."""
    meters = [panel for _, panel in _panels(spec) if panel["kind"] == "meters"]
    assert meters, "the gate panel is the reason this dashboard exists"
    for panel in meters:
        assert panel.get("ceiling_field"), panel.get("title")
        assert "ceiling" not in panel, "a panel-wide ceiling would win over nothing"


def test_the_spec_carries_no_reading_of_its_own(spec):
    """A spec describes what to show. Anything that has to be current
    belongs in the data a panel fetches -- otherwise it is a snapshot of
    what was true when the spec was asked for."""
    assert dashboard.spec() == spec


def test_spec_is_json(spec):
    json.dumps(spec)


def test_the_ask_panel_names_a_declared_tool(spec):
    """The node validates `tool` against this unit's own /tools before
    rendering, and shows a disabled button if it is not there."""
    from pu.server import TOOLS

    names = {tool["name"] for tool in TOOLS}
    for _, panel in _panels(spec):
        if panel["kind"] == "ask":
            assert panel["tool"] in names, panel["tool"]
            assert _is_safe_source(panel["poll"].format(id="x")), panel["poll"]
            assert panel.get("note"), "an ask panel must say that it spends"


def test_only_the_ask_panel_causes_anything_to_happen(spec):
    """Everything else here is a GET. If a second write ever appears in
    this spec it should be a deliberate act, not a diff nobody noticed."""
    causing = {
        panel["kind"] for _, panel in _panels(spec) if panel.get("tool")
    }
    assert causing <= EXTENSION_KINDS


def test_the_published_fixture_matches_the_spec():
    """`docs/dashboard-spec.example.json` is a copy of generated data.

    It exists because the node's renderer is otherwise only ever checked
    against a spec its own author wrote, which cannot find what that
    author did not think of. Ours found three real bugs on first render,
    two of them in the node and one in shipped console code -- a `rows`
    panel with no data drew an empty box where every other kind says
    "nothing recorded yet".

    A copy drifts. This is the guard on ours; the message below is the
    only guard on **theirs**, and it is deliberately in the failure path
    rather than in a document, because this is where somebody lands the
    moment `spec()` changes.
    """
    fixture_path = UNIT_ROOT / "docs" / "dashboard-spec.example.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert fixture == dashboard.spec(), (
        f"\n{fixture_path.name} is out of date. Regenerate it:\n"
        f"    python -c \"import json;from pu import dashboard;"
        f"print(json.dumps(dashboard.spec(),indent=2))\" > {fixture_path}\n\n"
        "Then send the new copy to whoever maintains the node. It vendors\n"
        "this spec and lints it in their CI, so their snapshot goes stale\n"
        "silently -- their build stays green while rendering a shape this\n"
        "unit no longer serves. Nothing automates that hand-off: the two\n"
        "repos are deliberately uncoupled, so this message is the whole\n"
        "of the mechanism."
    )


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
    # The ceiling travels with the row, so a meter can draw each window
    # against its own limit rather than against a shared one.
    assert state["windows"] == [
        {"name": "five-hour", "utilization": 0.9, "ceiling": 0.5}
    ]


def test_gate_state_survives_an_absent_usage_file(tmp_path, session_store):
    """Absence means allow, deliberately -- a node that has never run a
    session knows nothing, and blocking on that would mean it could never
    run the first."""
    state = pipeline.gate_state(session_store, tmp_path, tmp_path / "absent.json")
    assert state["windows"] == []
    assert state["blocked"] is False


# -- over the wire -------------------------------------------------------


@pytest.fixture
def dash_url(store, body_store, tmp_path):
    httpd = server.build_server(
        "127.0.0.1", 0, store, body_store, PROMPTS_DIR,
        unit_root=UNIT_ROOT,
        session_store=sessions.SessionStore(tmp_path / "sessions"),
        ask_store=ask.AskStore(tmp_path / "asks"),
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


def test_asking_over_the_wire_returns_an_id_and_polls(dash_url, monkeypatch):
    """The two halves the panel uses: a tool call that returns at once,
    and a GET the read proxy can carry."""
    from urllib.request import Request, urlopen

    monkeypatch.setattr(
        "pu.ask.runner_mod.run_session",
        lambda **kw: __import__("pu.runner", fromlist=["x"]).SessionResult(
            exit_code=0, text="the queue is empty", cost_usd=0.0
        ),
    )
    req = Request(
        f"{dash_url}/ask",
        data=json.dumps({"question": "how is the queue?"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req) as resp:
        assert resp.status == 202
        submitted = json.loads(resp.read().decode("utf-8"))

    # Exactly {"id": ...}: a fixed shape, so there is no field name for a
    # spec to declare and get wrong.
    assert list(submitted) == ["id"]

    status, record = get(f"{dash_url}/ask/{submitted['id']}")
    assert status == 200
    assert record["status"] in {"running", "done"}


def test_a_windows_meter_has_both_a_reading_and_its_ceiling(dash_url):
    """The meter panel reads `gate`; if either half of a row went
    missing the gauge would draw against nothing."""
    _, gate = get(f"{dash_url}/gate")
    for row in gate["windows"]:
        assert "utilization" in row and "ceiling" in row, row


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
