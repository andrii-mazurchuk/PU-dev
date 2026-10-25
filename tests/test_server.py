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
    # Deleted 2026-10-24: wired into cu and au, never used.
    assert "push_inbox_message" not in names


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
    _, created = post(f"{base_url}/maps", {"project": "hub", "destination": "somewhere"})
    _, task = get(f"{base_url}/tasks/{created['uuid']}")
    assert task["kind"] == "map"
    assert task["runnable"] is False
    assert task["session_type"] is None


def test_hitl_ticket_is_stored_but_never_runnable(base_url):
    """A grilling ticket must be holdable without ever being selectable,
    even if a caller marks it afk."""
    _, created = post(f"{base_url}/maps", {"project": "hub", "destination": "somewhere"})
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
    _, created = post(f"{base_url}/maps", {"project": "hub", "destination": "somewhere"})
    _, ticket = post(f"{base_url}/tickets", {
        "parent": created["uuid"], "title": "a thing", "kind": "research",
    })
    _, task = get(f"{base_url}/tasks/{ticket['uuid']}")
    assert task["afk"] is False


def test_unknown_kind_is_400(base_url):
    _, created = post(f"{base_url}/maps", {"project": "hub", "destination": "somewhere"})
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
    _, created = post(f"{base_url}/maps", {"project": "hub", "destination": "somewhere"})
    _, ticket = post(f"{base_url}/tickets", {
        "parent": created["uuid"], "title": "x", "kind": "research", "afk": True,
    })
    post(f"{base_url}/tasks/{ticket['uuid']}/claim", {})
    _, second = post(f"{base_url}/tasks/{ticket['uuid']}/claim", {})
    assert second["claimed"] is False


# -- reads ----------------------------------------------------------------


def test_projects_are_derived_and_nest(base_url):
    _, m = post(f"{base_url}/maps", {"destination": "d", "project": "thing"})
    post(f"{base_url}/tickets", {
        "parent": m["uuid"], "title": "x", "kind": "research",
        "project": "thing.architecture",
    })
    _, projects = get(f"{base_url}/projects")
    assert {p["project"] for p in projects["projects"]} == {
        "thing", "thing.architecture"
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
    _, created = post(f"{base_url}/maps", {"project": "hub", "destination": "somewhere"})
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
    _, created = post(f"{base_url}/maps", {"project": "hub", "destination": "somewhere",
                                           "body": "first"})
    uuid = created["uuid"]
    post_text(f"{base_url}/tasks/{uuid}/body", "second")
    _, got = get(f"{base_url}/tasks/{uuid}/body")
    assert got == "second"


def test_the_json_body_form_still_works(base_url):
    """An absent or unrecognised content type must fall through to JSON,
    so no existing caller is silently reinterpreted."""
    _, created = post(f"{base_url}/maps", {"project": "hub", "destination": "somewhere"})
    uuid = created["uuid"]
    post(f"{base_url}/tasks/{uuid}/body", {"body": "via json"})
    _, got = get(f"{base_url}/tasks/{uuid}/body")
    assert got == "via json"


def test_a_task_with_no_body_is_404_not_empty(base_url):
    _, created = post(f"{base_url}/maps", {"project": "hub", "destination": "no body here"})
    with pytest.raises(HTTPError) as excinfo:
        get(f"{base_url}/tasks/{created['uuid']}/body")
    assert excinfo.value.code == 404


def test_unknown_task_is_404(base_url):
    with pytest.raises(HTTPError) as excinfo:
        get(f"{base_url}/tasks/11111111-2222-3333-4444-555555555555")
    assert excinfo.value.code == 404


def test_a_short_uuid_names_its_task_on_reads_and_writes(base_url):
    """pu's own listings show 8 characters. A prefix of a permanent uuid is
    not a renumbering short id, so it is accepted wherever a uuid is."""
    _, created = post(f"{base_url}/maps", {"project": "hub", "destination": "short",
                                           "body": "the body"})
    full, prefix = created["uuid"], created["uuid"][:8]

    _, task = get(f"{base_url}/tasks/{prefix}")
    assert task["uuid"] == full
    assert get(f"{base_url}/tasks/{prefix}/body")[1] == "the body"
    _, ticket = post(f"{base_url}/tickets", {"parent": prefix, "title": "t",
                                            "kind": "task"})
    assert post(f"{base_url}/tasks/{ticket['uuid'][:8]}/resolve",
                {"answer": "done"})[1]["uuid"] == ticket["uuid"]


def test_a_prefix_too_short_is_refused_not_guessed(base_url):
    _, created = post(f"{base_url}/maps", {"project": "hub", "destination": "short"})
    with pytest.raises(HTTPError) as excinfo:
        get(f"{base_url}/tasks/{created['uuid'][:6]}")
    assert excinfo.value.code == 400


def test_an_ambiguous_prefix_is_refused_with_the_candidates(store, monkeypatch):
    """Never first-match: a resolve sent to the wrong task loses an answer."""
    import uuid as uuid_module

    from pu import service, taskstore

    chosen = iter([uuid_module.UUID("abcdef01-0000-4000-8000-000000000001"),
                   uuid_module.UUID("abcdef01-0000-4000-8000-000000000002")])
    monkeypatch.setattr(taskstore.uuid_module, "uuid4", lambda: next(chosen))
    a = store.add("one", kind="task")
    store.add("two", kind="task")

    with pytest.raises(service.ServiceError, match="matches 2 tasks"):
        service.full_uuid(store, "abcdef01")
    # The full uuid is unambiguous by definition.
    assert service.full_uuid(store, a) == a


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


def test_a_type_without_an_agent_role_runs_as_system(base_url):
    _, written = post(f"{base_url}/session-types", a_type())
    assert written["agent_role"] == "system"


@pytest.fixture
def registry(monkeypatch):
    """The gateway's role registry, stubbed: system plus finance."""
    from pu import roles
    known = {"system": "", "finance": "Andrey's personal finance agent."}
    monkeypatch.setattr(roles, "fetch", lambda url, timeout=3.0: dict(known))
    return known


def test_moving_a_live_type_to_another_agent_role_switches_it_off(
        base_url, registry):
    """Once a role carries MCP permissions, a role change is a permission
    change -- so it needs approval again, like any other edit."""
    post(f"{base_url}/session-types", a_type())
    _, live = post(f"{base_url}/session-types", a_type(enabled=True))
    assert live["enabled"] is True

    # Spelling out the default is not an edit...
    _, same = post(f"{base_url}/session-types",
                   a_type(enabled=True, agent_role="system"))
    assert same["enabled"] is True

    # ...but a different role is.
    _, moved = post(f"{base_url}/session-types",
                    a_type(enabled=True, agent_role="finance"))
    assert moved["enabled"] is False
    assert moved["agent_role"] == "finance"


def test_a_role_the_registry_does_not_hold_is_refused(base_url, registry):
    with pytest.raises(HTTPError) as excinfo:
        post(f"{base_url}/session-types", a_type(agent_role="cto"))
    assert excinfo.value.code == 400
    assert "not in the gateway's registry" in excinfo.value.read().decode()


def test_a_non_system_role_is_refused_while_the_registry_is_unreachable(
        base_url, monkeypatch):
    from pu import roles

    def down(url, timeout=3.0):
        raise roles.Unreachable("connection refused")
    monkeypatch.setattr(roles, "fetch", down)
    with pytest.raises(HTTPError) as excinfo:
        post(f"{base_url}/session-types", a_type(agent_role="finance"))
    assert excinfo.value.code == 400
    # System work never depends on the gateway.
    assert post(f"{base_url}/session-types", a_type())[0] == 201


def test_an_agent_role_is_a_name_not_a_path(base_url):
    with pytest.raises(HTTPError) as excinfo:
        post(f"{base_url}/session-types", a_type(agent_role="../etc"))
    assert excinfo.value.code == 400


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


def test_the_rename_routes_answer_and_the_old_name_leaves_the_listing(base_url):
    post(f"{base_url}/session-types", {
        "name": "digest", "purpose": "p", "prompt": "x", "grant": "read"})
    status, body = post(f"{base_url}/session-types/digest/rename",
                        {"to": "deploy-digest"})
    assert status == 200 and body["name"] == "deploy-digest"
    _, listed = get(f"{base_url}/session-types")
    assert [t["name"] for t in listed["session_types"]] == ["deploy-digest"]

    post(f"{base_url}/triggers", {
        "name": "posts",
        "source": {"kind": "schedule", "every_seconds": 60},
        "emits": {"kind": "execution"}})
    status, body = post(f"{base_url}/triggers/posts/rename", {"to": "nightly"})
    assert status == 200 and body["name"] == "nightly"
    _, listed = get(f"{base_url}/triggers")
    assert [t["name"] for t in listed["triggers"]] == ["nightly"]


def test_resolving_a_finished_task_is_refused_and_keeps_the_first_answer(base_url):
    """Live 2026-09-27: a retried resolve appended a new answer to an
    already-completed task, then 502'd -- inviting the next retry. The
    considered answer was buried under a one-word probe."""
    _, created = post(f"{base_url}/maps", {"project": "hub", "destination": "resolve twice"})
    uuid = created["uuid"]
    assert post(f"{base_url}/tasks/{uuid}/resolve",
                {"answer": "the considered answer"})[0] == 200

    with pytest.raises(HTTPError) as excinfo:
        post(f"{base_url}/tasks/{uuid}/resolve", {"answer": "probe"})
    assert excinfo.value.code == 409

    _, task = get(f"{base_url}/tasks/{uuid}")
    assert task["status"] == "completed"
    assert task["annotations"] == ["the considered answer"]


def test_a_stdout_only_refusal_carries_its_message():
    from pu import taskstore
    err = taskstore.TaskError(["task", "done"], 1, "",
                              "Task c54bf6c3 'b' is neither pending nor waiting.\n")
    assert "neither pending nor waiting" in str(err)


# -- the run namespace ----------------------------------------------------


def test_run_tasks_are_hidden_from_every_view_but_the_queue(store, body_store):
    """The rule that matters most: hidden from people's and agents' views,
    never from the queue -- or trigger work silently never runs."""
    from pu import service

    run = store.add("digest: fired", tags=["afk"], kind="research",
                    project="pu.runs")
    mine = store.add("real work", tags=["afk"], kind="research",
                     project="hub")

    listed = {t["uuid"] for t in service.list_tasks(store)}
    assert listed == {mine}
    assert {t["uuid"] for t in service.frontier(store)} == {mine}
    assert [p["project"] for p in service.list_projects(store)] == ["hub"]
    assert {t["uuid"] for t in service.queue(store)} == {run, mine}

    # Asked for by name, it is there.
    assert {t["uuid"] for t in service.list_tasks(store, project="pu.runs")} \
        == {run}

    stats = service.stats(store, body_store)
    assert (stats["pending_tasks"], stats["runs_pending"],
            stats["runnable_now"]) == (1, 1, 2)


def test_nobody_but_a_trigger_writes_into_the_run_namespace(base_url):
    _, created = post(f"{base_url}/maps", {"project": "hub", "destination": "a map"})
    for path, payload in (
        ("/maps", {"destination": "sneaky", "project": "pu.runs"}),
        ("/tickets", {"parent": created["uuid"], "title": "t",
                      "kind": "task", "project": "pu.runs.x"}),
    ):
        with pytest.raises(HTTPError) as excinfo:
            post(f"{base_url}{path}", payload)
        assert excinfo.value.code == 400, path


def test_a_finished_run_task_is_pruned_after_thirty_days(store, tmp_path):
    from datetime import datetime, timedelta, timezone

    from pu import guards

    old = store.add("old run", kind="research", project="pu.runs")
    store.complete(old)
    keep = store.add("other work", kind="research", project="hub")
    store.complete(keep)

    later = datetime.now(timezone.utc) + timedelta(days=31)
    state = tmp_path / "prune.json"
    assert guards.prune_runs(store, state, later) == [old]
    assert store.get(old).status == "deleted"
    assert store.get(keep).status == "completed"
    # And not again within the day.
    assert guards.prune_runs(store, state, later) == []


# -- dry-running a session type -------------------------------------------


def test_a_session_type_dry_run_shows_everything_and_writes_nothing(
        base_url, tmp_path):
    _, task = post(f"{base_url}/maps", {"project": "hub", "destination": "what does it cost"})
    status, preview = post(f"{base_url}/session-types/dry-run",
                           a_type(agent_role="finance", task=task["uuid"]))
    assert status == 200
    assert preview["agent_role"] == "finance"
    assert "You summarise." in preview["claude_md"]
    assert preview["argv"][:2][1] == "-p"
    assert "- agent role: `finance`" in preview["prompt"]
    assert "OUTCOME:" in preview["prompt"]

    # Nothing was written: no record, no generated directory.
    _, listed = get(f"{base_url}/session-types")
    assert listed["session_types"] == []
    assert not (tmp_path / "session_types" / "digest").exists()


def test_a_dry_run_refuses_what_a_write_would_refuse(base_url):
    with pytest.raises(HTTPError) as excinfo:
        post(f"{base_url}/session-types/dry-run", a_type(grant="root"))
    assert excinfo.value.code == 400


# -- placement: every ticket hangs off a map in its own project ----------


def test_a_map_needs_a_project(base_url):
    with pytest.raises(HTTPError) as excinfo:
        post(f"{base_url}/maps", {"destination": "nowhere"})
    assert excinfo.value.code == 400


def test_a_ticket_inherits_its_maps_project_or_nests_under_it(base_url):
    _, v1 = post(f"{base_url}/maps", {"destination": "v1", "project": "node"})
    _, t = post(f"{base_url}/tickets", {"parent": v1["uuid"], "title": "t",
                                        "kind": "task"})
    assert get(f"{base_url}/tasks/{t['uuid']}")[1]["project"] == "node"
    # Nested under the map's project: the v1-map pattern.
    _, nested = post(f"{base_url}/tickets", {
        "parent": v1["uuid"], "title": "pu vision", "kind": "grilling",
        "project": "node.pu"})
    assert get(f"{base_url}/tasks/{nested['uuid']}")[1]["project"] == "node.pu"


def test_a_ticket_from_another_project_is_refused(base_url):
    """What went wrong: cu's dashboard hanging off the gateway's map."""
    _, gw = post(f"{base_url}/maps", {"destination": "gw", "project": "node.gateway"})
    with pytest.raises(HTTPError) as excinfo:
        post(f"{base_url}/tickets", {"parent": gw["uuid"], "title": "cu dash",
                                     "kind": "task", "project": "node.cu"})
    assert excinfo.value.code == 400


def test_move_task_relocates_and_keeps_history(base_url):
    _, gw = post(f"{base_url}/maps", {"destination": "gw", "project": "node.gateway"})
    _, cu = post(f"{base_url}/maps", {"destination": "cu", "project": "node.cu"})
    _, t = post(f"{base_url}/tickets", {"parent": gw["uuid"], "title": "cu dash",
                                        "kind": "task", "body": "evidence"})
    _, moved = post(f"{base_url}/tasks/{t['uuid'][:8]}/move",
                    {"parent": cu["uuid"], "title": "fix cu dash"})
    assert set(moved["changed"]) == {"parent", "project", "title"}
    _, task = get(f"{base_url}/tasks/{t['uuid']}")
    assert (task["parent"], task["project"], task["description"]) == \
        (cu["uuid"], "node.cu", "fix cu dash")
    assert task["body"] == "evidence"
    assert any(a.startswith("moved: was") for a in task["annotations"])


def test_a_move_that_breaks_the_rule_is_refused(base_url):
    _, gw = post(f"{base_url}/maps", {"destination": "gw", "project": "node.gateway"})
    _, t = post(f"{base_url}/tickets", {"parent": gw["uuid"], "title": "t",
                                        "kind": "task"})
    with pytest.raises(HTTPError) as excinfo:
        post(f"{base_url}/tasks/{t['uuid']}/move", {"project": "node.cu"})
    assert excinfo.value.code == 400


def test_a_map_can_be_retitled_but_not_moved_away_from_its_tickets(base_url):
    _, m = post(f"{base_url}/maps", {"destination": "The node runs on x",
                                     "project": "node"})
    post(f"{base_url}/tickets", {"parent": m["uuid"], "title": "t", "kind": "task"})
    post(f"{base_url}/tasks/{m['uuid']}/move", {"title": "The node runs on y"})
    assert get(f"{base_url}/tasks/{m['uuid']}")[1]["description"] == "The node runs on y"
    with pytest.raises(HTTPError) as excinfo:
        post(f"{base_url}/tasks/{m['uuid']}/move", {"project": "elsewhere"})
    assert excinfo.value.code == 400


def test_a_map_with_open_tickets_cannot_be_closed(base_url):
    """Closing it first hides its tickets from every frontier."""
    _, m = post(f"{base_url}/maps", {"destination": "m", "project": "node"})
    _, t = post(f"{base_url}/tickets", {"parent": m["uuid"], "title": "t",
                                        "kind": "grilling"})
    with pytest.raises(HTTPError) as excinfo:
        post(f"{base_url}/tasks/{m['uuid']}/resolve", {"answer": "done"})
    assert excinfo.value.code == 409
    post(f"{base_url}/tasks/{t['uuid']}/resolve", {"answer": "decided"})
    assert post(f"{base_url}/tasks/{m['uuid']}/resolve", {"answer": "done"})[0] == 200


def test_the_audit_names_faults_made_around_the_api(store):
    from pu import service

    m = store.add("gw", kind="map", project="node.gateway")
    store.add("cu dash", kind="task", project="node.cu", parent_uuid=m)
    store.add("loose", kind="task", project="node")
    faults = service.audit(store)
    assert [r["title"] for r in faults["misplaced"]] == ["cu dash"]
    assert [r["title"] for r in faults["mapless"]] == ["loose"]


def test_the_daily_audit_tells_each_new_fault_once(store, tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from pu import pipeline

    sent = []
    monkeypatch.setattr(pipeline.notify, "notify_owner",
                        lambda url, text, **k: sent.append(text) or True)
    store.add("loose", kind="task", project="node")
    t0 = datetime(2026, 10, 25, 8, tzinfo=timezone.utc)
    state = tmp_path / "audit.json"

    pipeline._daily_audit(store, state, t0, None)
    assert len(sent) == 1 and "loose" in sent[0]
    pipeline._daily_audit(store, state, t0 + timedelta(hours=1), None)
    pipeline._daily_audit(store, state, t0 + timedelta(days=1, hours=1), None)
    assert len(sent) == 1, "told once, not daily"
