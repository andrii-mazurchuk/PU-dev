"""The HTTP surface, over a real socket."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from pu import server, triggers, typestore

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@pytest.fixture
def base_url(store, body_store, tmp_path):
    # The session-type store is wired here and not treated as optional:
    # `main` always creates one, the same as the body store, so a server
    # without one is not a configuration this unit ships. The handler keeps
    # its 503 for that case anyway, since a build_server caller can omit it.
    httpd = server.build_server(
        "127.0.0.1", 0, store, body_store, PROMPTS_DIR,
        type_store=typestore.TypeStore(tmp_path),
        trigger_store=triggers.TriggerStore(tmp_path),
    )
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


def test_every_declared_prompt_tier_ships_a_file():
    """A tier in PROMPT_TIERS with no file behind it serves a 404 to
    whoever asks for it -- and the caller that asks is another unit, so
    the failure is silent and remote. AU reads the insights tier."""
    from pu.server import PROMPT_TIERS

    prompts_dir = Path(__file__).resolve().parent.parent / "prompts"
    missing = [t for t in PROMPT_TIERS if not (prompts_dir / f"{t}.md").is_file()]
    assert not missing, f"declared but not shipped: {missing}"


# -- write door 3: session-type authoring --------------------------------


def a_type(**overrides):
    payload = {
        "name": "digest",
        "purpose": "Summarise what the watch brought in.",
        "prompt": "You summarise.\n",
        "grant": "read",
    }
    payload.update(overrides)
    return payload


def _refusal(url, payload):
    with pytest.raises(HTTPError) as caught:
        post(url, payload)
    assert caught.value.code == 400
    return json.loads(caught.value.read().decode("utf-8"))["error"]


def test_a_caller_cannot_claim_the_declared_origin(base_url):
    """The two origins differ in exactly one thing -- a declared type skips
    the first-enable gate, because it arrived inside reviewed software. So a
    caller able to set `origin` is a caller claiming that exemption."""
    error = _refusal(f"{base_url}/session-types", a_type(origin="declared"))
    assert "set by this unit" in error

    status, record = post(f"{base_url}/session-types", a_type())
    assert status == 201
    assert record["origin"] == "authored"


def test_a_type_is_created_inactive_and_activated_by_a_second_write(base_url):
    """Creating and activating are two acts, settled 2026-10-09: anyone may
    create one and it arrives switched off. `enabled` on a first write is
    overridden rather than refused, so no caller has to handle an error for
    something this unit can simply decide."""
    status, created = post(f"{base_url}/session-types", a_type(enabled=True))
    assert status == 201
    assert created["enabled"] is False

    status, activated = post(f"{base_url}/session-types",
                             a_type(enabled=True))
    assert activated["enabled"] is True

    _, stored = get(f"{base_url}/session-types/digest")
    assert stored["enabled"] is True


def test_editing_a_live_type_switches_it_off(base_url):
    """The exploit this closes is specific: `prompt` is the whole
    behaviour, so editing it on an approved type silently changes what an
    approved session does, with no new approval anywhere."""
    post(f"{base_url}/session-types", a_type())
    post(f"{base_url}/session-types", a_type(enabled=True))

    _, edited = post(f"{base_url}/session-types",
                     a_type(enabled=True, prompt="Do something else.\n"))

    assert edited["enabled"] is False
    assert edited["prompt"] == "Do something else.\n"


def test_a_field_dropped_from_a_live_type_also_switches_it_off(base_url):
    """Records are compared whole, not as a patch: the store holds a full
    record per line, so a field the caller left out has been removed. Treating
    that as unchanged would let an edit strip a field from a live type and
    leave it running."""
    post(f"{base_url}/session-types", a_type(model="opus"))
    _, live = post(f"{base_url}/session-types",
                   a_type(model="opus", enabled=True))
    assert live["enabled"] is True

    _, edited = post(f"{base_url}/session-types", a_type(enabled=True))
    assert "model" not in edited
    assert edited["enabled"] is False


def test_a_second_write_to_one_name_is_a_revision(base_url):
    """`name` is the identity and the store is append-only, so there is one
    endpoint for create and update rather than a POST/PUT pair -- the
    generic CRUD the unit contract rules out."""
    post(f"{base_url}/session-types", a_type(prompt="first\n"))
    post(f"{base_url}/session-types", a_type(prompt="second\n"))

    status, record = get(f"{base_url}/session-types/digest")
    assert status == 200
    assert record["prompt"] == "second\n"

    status, listing = get(f"{base_url}/session-types")
    assert [t["name"] for t in listing["session_types"]] == ["digest"]


def test_a_bad_grant_is_the_caller_s_mistake_not_a_server_fault(base_url):
    """`TypeStoreError` is raised below the HTTP layer, so without it in the
    handler's except clause this would surface as a 500 and a client would
    retry a request that can never succeed."""
    assert "unknown grant" in _refusal(
        f"{base_url}/session-types", a_type(grant="admin"))


def test_an_unwritten_name_is_a_404(base_url):
    """Well-formed request, absent thing -- the same shape as `get_task`."""
    with pytest.raises(HTTPError) as caught:
        get(f"{base_url}/session-types/never-written")
    assert caught.value.code == 404


def test_the_enable_flip_tells_the_owner_and_a_re_save_does_not(
        base_url, monkeypatch):
    """Nothing authenticates a caller, so notification is the only control
    that survives an agent reaching this unit on loopback. Both transitions
    that change what runs are told; an idempotent re-save is not, because a
    message per write is noise that teaches its reader to skip it."""
    from pu import notify

    sent = []
    monkeypatch.setattr(notify, "bridge_url", lambda *a, **k: "http://bridge")
    monkeypatch.setattr(
        notify, "notify_owner",
        lambda url, text, source_unit, **k: sent.append(text) or True)

    # Created inactive, and silent: nothing runs, so nothing happened.
    post(f"{base_url}/session-types", a_type(enabled=True))
    assert sent == []

    post(f"{base_url}/session-types", a_type(enabled=True))
    assert len(sent) == 1
    assert "digest" in sent[0]
    assert "nothing asked first" in sent[0]

    # Re-writing the identical record changes nothing and says nothing.
    post(f"{base_url}/session-types", a_type(enabled=True))
    assert len(sent) == 1

    # Editing it while live is the other transition worth telling: it was
    # switched off, and the owner has to know why it stopped running.
    post(f"{base_url}/session-types", a_type(enabled=True, purpose="reworded"))
    assert len(sent) == 2
    assert "deactivated a live session type because it was edited" in sent[1]


# -- write door 4: trigger authoring -------------------------------------


def a_watch(**overrides):
    payload = {
        "name": "failed-deploys",
        "purpose": "Watch the deploy feed for failures.",
        "source": {"kind": "http-json", "url": "http://example/feed.json"},
        "predicate": [["level", "==", "error"]],
        "emits": {"kind": "execution", "tags": ["digest"], "afk": True},
    }
    payload.update(overrides)
    return payload


def test_a_trigger_can_be_written_and_read_back(base_url):
    """Before this door existed the only way to create a watch was to
    append a line to the log with an editor -- which also skipped the
    validation, since append is its only caller."""
    status, created = post(f"{base_url}/triggers", a_watch())
    assert status == 201
    assert created["enabled"] is False

    status, listed = get(f"{base_url}/triggers")
    assert [t["name"] for t in listed["triggers"]] == ["failed-deploys"]

    status, one = get(f"{base_url}/triggers/failed-deploys")
    assert one["source"]["kind"] == "http-json"


def test_a_trigger_inherits_both_activation_rules(base_url):
    """Stronger here than for a session type rather than weaker: a type is
    inert until a task reaches it, while a trigger with afk work causes
    spend on an interval with nobody watching."""
    post(f"{base_url}/triggers", a_watch(enabled=True))
    _, activated = post(f"{base_url}/triggers", a_watch(enabled=True))
    assert activated["enabled"] is True

    _, edited = post(f"{base_url}/triggers",
                     a_watch(enabled=True, predicate=[["level", "==", "warn"]]))
    assert edited["enabled"] is False


def test_the_cursor_is_served_apart_from_the_trigger(base_url):
    """State, not config: they change on different clocks, and folding them
    together would imply a history the cursor does not have. It is also the
    evidence a watch is working at all."""
    post(f"{base_url}/triggers", a_watch())

    status, cursor = get(f"{base_url}/triggers/failed-deploys/cursor")
    assert status == 200
    assert cursor == {"seen": [], "last_new_at": None,
                      "pending": 0, "fired_at": None}

    with pytest.raises(HTTPError) as caught:
        get(f"{base_url}/triggers/never-written/cursor")
    assert caught.value.code == 404


def test_a_malformed_trigger_is_the_caller_s_mistake(base_url):
    """TriggerError is raised below the HTTP layer, so without it in both
    handlers' except clauses this surfaces as a 500 and a client retries a
    request that can never succeed."""
    for payload, message in [
        (a_watch(source={"kind": "ftp", "url": "u"}), "unknown source kind"),
        (a_watch(source={"kind": "http-json"}), "needs a url"),
        (a_watch(predicate=[["a", "~=", 1]]), "unknown operator"),
        (a_watch(emits={"kind": "nonsense"}), "kind"),
        (a_watch(source={"kind": "schedule", "every_seconds": 60}),
         "takes no predicate"),
    ]:
        with pytest.raises(HTTPError) as caught:
            post(f"{base_url}/triggers", payload)
        assert caught.value.code == 400
        assert message in json.loads(
            caught.value.read().decode("utf-8"))["error"]


def test_activating_a_trigger_tells_the_owner_that_it_will_spend(
        base_url, monkeypatch):
    """The only control there is. A trigger that emits agent-runnable work
    spends unattended, and the message has to say so -- the person reading
    it is deciding whether that was intended."""
    from pu import notify

    sent = []
    monkeypatch.setattr(notify, "bridge_url", lambda *a, **k: "http://bridge")
    monkeypatch.setattr(
        notify, "notify_owner",
        lambda url, text, source_unit, **k: sent.append(text) or True)

    post(f"{base_url}/triggers", a_watch())
    assert sent == []

    post(f"{base_url}/triggers", a_watch(enabled=True))
    assert len(sent) == 1
    assert "will spend" in sent[0]


def test_the_dry_run_route_previews_without_writing(base_url):
    """A read dressed as a POST. It has to answer, and the store has to be
    untouched afterwards -- otherwise pressing Test is a write."""
    status, body = post(f"{base_url}/triggers/dry-run", {
        "name": "unreachable",
        "source": {"kind": "http-json", "url": "http://127.0.0.1:1/none"},
        "emits": {"kind": "execution"},
    })
    assert status == 200
    assert body["ok"] is False and body["fired"] is False

    _, listed = get(f"{base_url}/triggers")
    assert listed["triggers"] == []
