"""MCP servers: a registry by name, attached per session type, http only,
no secret ever stored, and holonic attached to every queue session."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pu import mcpstore, pipeline, runner, service, sessions, typestore

UNIT_ROOT = Path(__file__).resolve().parent.parent


def test_a_literal_token_is_refused_and_a_placeholder_is_kept(tmp_path):
    store = mcpstore.McpStore(tmp_path)
    with pytest.raises(mcpstore.McpStoreError, match="placeholder"):
        store.append({"name": "gh", "url": "https://x/mcp",
                      "headers": {"Authorization": "Bearer abc123"}})
    kept = store.append({"name": "gh", "url": "https://x/mcp",
                         "headers": {"Authorization": "Bearer ${GH_TOKEN}"}})
    assert "${GH_TOKEN}" in kept["headers"]["Authorization"]


def test_only_http_and_never_the_reserved_name(tmp_path):
    store = mcpstore.McpStore(tmp_path)
    with pytest.raises(mcpstore.McpStoreError, match="http"):
        store.append({"name": "local", "type": "stdio", "url": "https://x"})
    with pytest.raises(mcpstore.McpStoreError, match="reserved"):
        store.append({"name": "mcp-bridge", "url": "https://x"})


def test_placeholders_are_filled_at_launch_or_the_missing_one_is_named():
    record = {"name": "gh", "url": "https://x/mcp",
              "headers": {"Authorization": "Bearer ${GH_TOKEN}"}}
    assert mcpstore.resolve(record, {"GH_TOKEN": "t"})["headers"] == \
        {"Authorization": "Bearer t"}
    with pytest.raises(mcpstore.McpStoreError, match="GH_TOKEN"):
        mcpstore.resolve(record, {})


def test_a_type_can_only_attach_registered_servers(tmp_path, monkeypatch):
    monkeypatch.setattr(service.notify, "notify_owner", lambda *a, **k: True)
    types = typestore.TypeStore(tmp_path)
    draft = {"name": "digest", "purpose": "p", "prompt": "x\n", "grant": "read",
             "mcp_servers": ["notion"]}
    with pytest.raises(service.ServiceError, match="not in the registry"):
        service.write_session_type(types, draft)
    service.write_mcp_server(mcpstore.McpStore(tmp_path),
                             {"name": "notion", "url": "https://n/mcp"})
    assert service.write_session_type(types, draft)["mcp_servers"] == ["notion"]


def test_argv_allows_every_attached_server_through_a_file_not_inline():
    argv = runner.build_argv(allowed_tools="Read", mcp_bridge_url="http://b",
                             mcp_servers=["notion"],
                             mcp_config_path="/s/digest/.mcp-session.json")
    assert argv[argv.index("--mcp-config") + 1] == "/s/digest/.mcp-session.json"
    tools = argv[argv.index("--allowedTools") + 1].split(",")
    assert {"mcp__mcp-bridge__*", "mcp__notion__*"} <= set(tools)


def test_a_launch_attaches_holonic_and_the_type_s_servers_only(
        store, body_store, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.notify, "notify_owner", lambda *a, **k: True)
    monkeypatch.setenv("NOTION_TOKEN", "secret")
    state = tmp_path / "state"
    mcpstore.McpStore(state).append({
        "name": "notion", "url": "https://n/mcp",
        "headers": {"Authorization": "Bearer ${NOTION_TOKEN}"}})
    types = typestore.TypeStore(state)
    types.append({"name": "digest", "purpose": "p", "prompt": "x\n",
                  "grant": "read", "origin": "authored", "enabled": True,
                  "mcp_servers": ["notion"],
                  "claims": {"kind": "research", "tags": ["digest"]}})
    store.add("digest it", tags=["afk", "digest"], kind="research")

    seen = {}

    def fake(**kwargs):
        seen.update(kwargs)
        return runner.SessionResult(0, text="done", is_error=False)

    pipeline.tick(store, body_store, sessions.SessionStore(tmp_path / "s"),
                  UNIT_ROOT, tmp_path / "peers.json", tmp_path / "cost.json",
                  session_runner=fake, tracker_path=tmp_path / "t.json",
                  type_store=types, mcp_bridge_url="http://bridge")

    assert seen["mcp_bridge_url"] == "http://bridge"
    assert seen["mcp_servers"] == ("notion",)
    config = json.loads(Path(seen["mcp_config_path"]).read_text())
    assert set(config["mcpServers"]) == {"mcp-bridge", "notion"}
    assert config["mcpServers"]["notion"]["headers"]["Authorization"] == "Bearer secret"
    # In the type's own folder, not system-wide.
    assert Path(seen["mcp_config_path"]).parent.name == "digest"
