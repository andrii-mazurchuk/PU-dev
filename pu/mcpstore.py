"""The MCP server registry: named servers a session type can attach.

A session type lists server *names* (`mcp_servers: ["github"]`); the
definition lives here once, so a URL changes in one place and no type
carries a copy (pu vision follow-up, 2026-10-24). Attachment is per type:
servers are handed to that type's sessions only, at launch, never set up
for the whole system.

**Append-only JSONL, `current` is the last line** -- the same store shape
and the same rules as session types: agents may write here, and every
write tells the owner.

**http only.** A stdio server is a command pu would launch on its own host,
which is arbitrary code execution; it waits for the human-only endpoint
guardrail.

**No secret is ever stored.** A header value may be a `${ENV_VAR}`
placeholder, filled in from pu's own environment at launch. A literal
token in a header is refused, because this log is kept forever.

`mcp-bridge` is reserved: holonic MCP is attached to every session under
that name, hard-coded for now (see session_types.NO_PEER_REACH).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from pu import jsonl

_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_PLACEHOLDER = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")
RESERVED = frozenset({"mcp-bridge"})
FIELDS = ("name", "type", "url", "headers", "purpose")


class McpStoreError(ValueError):
    pass


def validate(record: dict[str, Any]) -> dict[str, Any]:
    unknown = set(record) - set(FIELDS)
    if unknown:
        raise McpStoreError(f"unknown field(s) {sorted(unknown)} (allowed: {list(FIELDS)})")
    name = record.get("name")
    # No dot: the name becomes `mcp__<name>__*` in a tool pattern, and the
    # CLI rewrites a dot there, so a dotted name would grant nothing.
    if not isinstance(name, str) or not _NAME.match(name):
        raise McpStoreError(f"not a usable server name: {name!r} "
                            f"(lowercase letters, digits, - and _)")
    if name in RESERVED:
        raise McpStoreError(f"{name!r} is reserved: holonic MCP is attached "
                            f"to every session under that name")
    if (record.get("type") or "http") != "http":
        raise McpStoreError("only http servers for now; a stdio server is a "
                            "command run on pu's host")
    url = record.get("url")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise McpStoreError(f"url must be http(s): {url!r}")
    headers = record.get("headers") or {}
    if not isinstance(headers, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
        raise McpStoreError("headers is an object of strings")
    for key, value in headers.items():
        if value.strip() and not _PLACEHOLDER.search(value):
            raise McpStoreError(
                f"header {key!r} holds a literal value; write it as a "
                f"${{ENV_VAR}} placeholder -- this log is kept forever")
    return dict(record, type="http", headers=headers)


def resolve(record: dict[str, Any],
            environ: dict[str, str] | None = None) -> dict[str, Any]:
    """The server as the CLI's --mcp-config wants it, placeholders filled.
    Raises McpStoreError naming a variable pu does not have."""
    env = os.environ if environ is None else environ

    def fill(value: str) -> str:
        def one(match: re.Match) -> str:
            if match.group(1) not in env:
                raise McpStoreError(
                    f"MCP server {record['name']!r} needs ${match.group(1)}, "
                    f"which is not set in pu's environment")
            return env[match.group(1)]
        return _PLACEHOLDER.sub(one, value)

    out: dict[str, Any] = {"type": "http", "url": record["url"]}
    if record.get("headers"):
        out["headers"] = {k: fill(v) for k, v in record["headers"].items()}
    return out


class McpStore:
    def __init__(self, root: Path) -> None:
        self.log = Path(root) / "mcp_servers.jsonl"

    def current(self) -> dict[str, dict[str, Any]]:
        return jsonl.current(self.log, McpStoreError)

    def get(self, name: str) -> dict[str, Any] | None:
        return self.current().get(name)

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        validated = validate(record)
        jsonl.append(self.log, validated)
        return validated
