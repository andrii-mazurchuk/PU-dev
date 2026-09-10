"""The HTTP surface, over a real socket."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from pu import server

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@pytest.fixture
def base_url(store, body_store):
    httpd = server.build_server("127.0.0.1", 0, store, body_store, PROMPTS_DIR)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def get(url, **params):
    if params:
        url = f"{url}?{urlencode({k: v for k, v in params.items() if v is not None})}"
    with urlopen(url) as resp:
        raw = resp.read().decode("utf-8")
        ctype = resp.headers.get("Content-Type", "")
        return resp.status, (json.loads(raw) if "json" in ctype else raw)


def post(url, payload):
    req = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


# -- the four standard endpoints -----------------------------------------


def test_health_returns_ok(base_url):
    assert get(f"{base_url}/health") == (200, {"status": "ok"})


def test_stats_envelope(base_url):
    status, payload = get(f"{base_url}/stats")
    assert status == 200
    assert payload["unit"] == "pu"
    assert "computed_at" in payload
    assert isinstance(payload["metrics"], dict)
    assert payload["metrics"]["pending_tasks"] == 0


def test_prompts_default_is_plain_text(base_url):
    status, body = get(f"{base_url}/prompts/default")
    assert status == 200
    assert isinstance(body, str) and body.strip()


def test_prompts_unknown_tier_is_404(base_url):
    with pytest.raises(HTTPError) as excinfo:
        get(f"{base_url}/prompts/nope")
    assert excinfo.value.code == 404


def test_tools_manifest_shape(base_url):
    status, payload = get(f"{base_url}/tools")
    assert status == 200
    assert payload["unit"] == "pu"
    names = set()
    for tool in payload["tools"]:
        assert set(tool) >= {"name", "description", "method", "path", "input_schema"}
        assert tool["method"] in ("GET", "POST")
        assert tool["path"].startswith("/")
        assert isinstance(tool["input_schema"], dict)
        assert tool["description"].strip()
        names.add(tool["name"])
    # /health and /tools are excluded from the manifest by the standard.
    assert not {t["path"] for t in payload["tools"]} & {"/health", "/tools"}
    assert "push_inbox_message" in names


def test_every_declared_GET_tool_path_answers(base_url):
    """A tool whose path 404s is a manifest that lies to a model."""
    for tool in server.TOOLS:
        if tool["method"] != "GET" or "{" in tool["path"]:
            continue
        status, _ = get(f"{base_url}{tool['path']}")
        assert status == 200, tool["name"]


# -- the wayfinding operations -------------------------------------------


def test_map_ticket_frontier_claim_resolve(base_url):
    _, created = post(f"{base_url}/maps", {
        "destination": "a spec for the thing",
        "project": "wf.thing",
        "body": "## Destination\n\nthe spec\n",
    })
    map_uuid = created["uuid"]

    _, blocker = post(f"{base_url}/tickets", {
        "parent": map_uuid, "title": "what does it cost",
        "kind": "research", "afk": True, "body": "## Question\n\ncost?\n",
    })
    _, dependent = post(f"{base_url}/tickets", {
        "parent": map_uuid, "title": "decide the shape",
        "kind": "research", "afk": True,
    })

    post(f"{base_url}/tasks/{dependent['uuid']}/blocking",
         {"blocked_by": [blocker["uuid"]]})

    _, front = get(f"{base_url}/frontier", parent=map_uuid)
    assert [t["uuid"] for t in front["tasks"]] == [blocker["uuid"]]

    _, claimed = post(f"{base_url}/tasks/{blocker['uuid']}/claim", {})
    assert claimed["claimed"] is True
    _, front = get(f"{base_url}/frontier", parent=map_uuid)
    assert front["tasks"] == []

    post(f"{base_url}/tasks/{blocker['uuid']}/resolve", {"answer": "it costs nothing"})

    _, front = get(f"{base_url}/frontier", parent=map_uuid)
    assert [t["uuid"] for t in front["tasks"]] == [dependent["uuid"]]

    _, task = get(f"{base_url}/tasks/{blocker['uuid']}")
    assert task["status"] == "completed"
    assert task["body"].startswith("## Question")


def test_a_map_is_never_runnable(base_url):
    _, created = post(f"{base_url}/maps", {"destination": "somewhere"})
    _, task = get(f"{base_url}/tasks/{created['uuid']}")
    assert task["kind"] == "map"
    assert task["runnable"] is False
    assert task["session_type"] is None


def test_hitl_ticket_is_stored_but_never_runnable(base_url):
    """A grilling ticket must be holdable without ever being selectable,
    even if a caller marks it afk."""
    _, created = post(f"{base_url}/maps", {"destination": "somewhere"})
    _, ticket = post(f"{base_url}/tickets", {
        "parent": created["uuid"], "title": "talk it through",
        "kind": "grilling", "afk": True,
    })
    _, task = get(f"{base_url}/tasks/{ticket['uuid']}")
    assert task["afk"] is True
    assert task["runnable"] is False

    # A person working the map must still see it -- hiding it would make
    # the map look finished when it is not.
    _, front = get(f"{base_url}/frontier", parent=created["uuid"])
    assert ticket["uuid"] in [t["uuid"] for t in front["tasks"]]

    # But PU may never select it.
    _, q = get(f"{base_url}/queue")
    assert q["tasks"] == []


def test_ticket_defaults_to_not_afk(base_url):
    _, created = post(f"{base_url}/maps", {"destination": "somewhere"})
    _, ticket = post(f"{base_url}/tickets", {
        "parent": created["uuid"], "title": "a thing", "kind": "research",
    })
    _, task = get(f"{base_url}/tasks/{ticket['uuid']}")
    assert task["afk"] is False


def test_unknown_kind_is_400(base_url):
    _, created = post(f"{base_url}/maps", {"destination": "somewhere"})
    with pytest.raises(HTTPError) as excinfo:
        post(f"{base_url}/tickets", {
            "parent": created["uuid"], "title": "x", "kind": "nonsense",
        })
    assert excinfo.value.code == 400
    assert "nonsense" in excinfo.value.read().decode("utf-8")


def test_ticket_on_a_missing_parent_is_400(base_url):
    with pytest.raises(HTTPError) as excinfo:
        post(f"{base_url}/tickets", {
            "parent": "11111111-2222-3333-4444-555555555555",
            "title": "x", "kind": "research",
        })
    assert excinfo.value.code == 400


def test_claiming_twice_reports_not_claimed(base_url):
    _, created = post(f"{base_url}/maps", {"destination": "somewhere"})
    _, ticket = post(f"{base_url}/tickets", {
        "parent": created["uuid"], "title": "x", "kind": "research", "afk": True,
    })
    post(f"{base_url}/tasks/{ticket['uuid']}/claim", {})
    _, second = post(f"{base_url}/tasks/{ticket['uuid']}/claim", {})
    assert second["claimed"] is False


# -- the inbox ------------------------------------------------------------


def test_inbox_mints_exactly_one_intake_record(base_url):
    status, accepted = post(f"{base_url}/inbox", {
        "source": "cu",
        "message": "please look at the deploy\nit has been failing since friday",
    })
    assert status == 202
    _, task = get(f"{base_url}/tasks/{accepted['uuid']}")
    assert task["kind"] == "intake"
    assert task["runnable"] is True
    assert "please look at the deploy" in task["body"]

    _, listed = get(f"{base_url}/tasks")
    assert len(listed["tasks"]) == 1


def test_inbox_requires_a_message(base_url):
    with pytest.raises(HTTPError) as excinfo:
        post(f"{base_url}/inbox", {"source": "cu"})
    assert excinfo.value.code == 400


# -- reads ----------------------------------------------------------------


def test_projects_are_derived_and_nest(base_url):
    _, m = post(f"{base_url}/maps", {"destination": "d", "project": "thing.behaviour"})
    post(f"{base_url}/tickets", {
        "parent": m["uuid"], "title": "x", "kind": "research",
        "project": "thing.architecture",
    })
    _, projects = get(f"{base_url}/projects")
    assert {p["project"] for p in projects["projects"]} == {
        "thing.behaviour", "thing.architecture"
    }
    # a parent scope returns everything nested under it
    _, nested = get(f"{base_url}/tasks", project="thing")
    assert len(nested["tasks"]) == 2


def post_text(url, text, content_type="text/markdown"):
    req = Request(url, data=text.encode("utf-8"),
                  headers={"Content-Type": content_type}, method="POST")
    with urlopen(req) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def test_a_body_round_trips_as_raw_markdown(base_url):
    """The path a session outside this unit uses: one curl out, one curl
    in, no JSON escaping and no jq."""
    _, created = post(f"{base_url}/maps", {"destination": "somewhere"})
    uuid = created["uuid"]

    body = (
        '## Destination\n\n'
        'A spec with "quotes", `backticks`, $(echo NOPE) and : colons.\n\n'
        '## Decisions so far\n\n- none yet\n'
    )
    status, wrote = post_text(f"{base_url}/tasks/{uuid}/body", body)
    assert status == 200 and wrote["uuid"] == uuid

    status, got = get(f"{base_url}/tasks/{uuid}/body")
    assert status == 200
    assert got == body, "byte-identical, including everything hostile"


def test_a_raw_body_replaces_rather_than_appends(base_url):
    """Replace-in-place is why the body store exists at all: a map is
    rewritten as decisions land and fog graduates."""
    _, created = post(f"{base_url}/maps", {"destination": "somewhere",
                                           "body": "first"})
    uuid = created["uuid"]
    post_text(f"{base_url}/tasks/{uuid}/body", "second")
    _, got = get(f"{base_url}/tasks/{uuid}/body")
    assert got == "second"


def test_the_json_body_form_still_works(base_url):
    """An absent or unrecognised content type must fall through to JSON,
    so no existing caller is silently reinterpreted."""
    _, created = post(f"{base_url}/maps", {"destination": "somewhere"})
    uuid = created["uuid"]
    post(f"{base_url}/tasks/{uuid}/body", {"body": "via json"})
    _, got = get(f"{base_url}/tasks/{uuid}/body")
    assert got == "via json"


def test_a_task_with_no_body_is_404_not_empty(base_url):
    _, created = post(f"{base_url}/maps", {"destination": "no body here"})
    with pytest.raises(HTTPError) as excinfo:
        get(f"{base_url}/tasks/{created['uuid']}/body")
    assert excinfo.value.code == 404


def test_unknown_task_is_404(base_url):
    with pytest.raises(HTTPError) as excinfo:
        get(f"{base_url}/tasks/11111111-2222-3333-4444-555555555555")
    assert excinfo.value.code == 404


def test_unknown_route_is_404(base_url):
    with pytest.raises(HTTPError) as excinfo:
        get(f"{base_url}/nope")
    assert excinfo.value.code == 404


def test_docs_index_is_derived_from_the_files_this_repo_ships(base_url):
    status, payload = get(f"{base_url}/docs")
    assert status == 200
    assert payload["unit"] == "pu"
    names = [d["name"] for d in payload["docs"]]
    # Both roots: UNIT_CONTRACT.md at the top, docs/OPERATIONS.md inside.
    assert "unit_contract" in names and "operations" in names
    entry = next(d for d in payload["docs"] if d["name"] == "operations")
    assert entry["bytes"] and entry["title"]


def test_get_doc_returns_markdown(base_url):
    status, body = get(f"{base_url}/docs/operations")
    assert status == 200
    assert isinstance(body, str) and body.startswith("#")


def test_get_doc_refuses_anything_not_in_the_index(base_url):
    for name in ["nope", "..%2F..%2Funits.yaml", "pyproject"]:
        with pytest.raises(HTTPError) as excinfo:
            get(f"{base_url}/docs/{name}")
        assert excinfo.value.code == 404
