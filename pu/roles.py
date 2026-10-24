"""The agent-role registry, read from the gateway.

The gateway is the source of truth for which agent roles exist (cu writes
them; every unit reads them). `system` is the one that always exists, so
nothing here can stop system work. (pu vision, 2026-10-24; gateway
contract: `GET {bridge}/agent-roles`.)

Two readings, deliberately different:

- **At write time, strict.** A session type naming a role the registry
  does not hold is refused, and so is any non-system role while the
  registry cannot be reached -- that is where a mistake can still be
  fixed, by the person making it.
- **At launch, tolerant.** The last copy that was read stands in when the
  gateway is unreachable, so a blip does not stop work already approved.
  A role that has actually vanished switches its type off.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from pu import session_types

CACHE_FILE = "agent_roles.json"


class Unreachable(RuntimeError):
    """The registry could not be read. Not the same as "no such role"."""


def fetch(bridge_url: str | None, timeout: float = 3.0) -> dict[str, str]:
    """Every registered role, name -> description. Raises Unreachable."""
    if not bridge_url:
        raise Unreachable("no gateway bridge configured (PU_MCP_BRIDGE_URL)")
    try:
        with urllib.request.urlopen(
                f"{bridge_url.rstrip('/')}/agent-roles", timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
        raise Unreachable(f"the gateway's role registry did not answer: {exc}") from exc
    roles = {r["name"]: r.get("description") or ""
             for r in (payload.get("roles") or ()) if isinstance(r, dict) and r.get("name")}
    roles.setdefault(session_types.DEFAULT_AGENT_ROLE, "")
    return roles


def _save(state_dir: Path | None, roles: dict[str, str]) -> None:
    if state_dir is None:
        return
    try:
        Path(state_dir).mkdir(parents=True, exist_ok=True)
        (Path(state_dir) / CACHE_FILE).write_text(json.dumps(roles), encoding="utf-8")
    except OSError:
        pass


def _cached(state_dir: Path | None) -> dict[str, str] | None:
    if state_dir is None:
        return None
    try:
        parsed = json.loads((Path(state_dir) / CACHE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def require(role: str, bridge_url: str | None, state_dir: Path | None) -> str:
    """At write time: the role's description, or raise ValueError saying
    why the role cannot be used. `system` always passes, unasked."""
    if role == session_types.DEFAULT_AGENT_ROLE:
        return ""
    try:
        roles = fetch(bridge_url)
    except Unreachable as exc:
        raise ValueError(
            f"agent role {role!r} cannot be checked right now ({exc}); a "
            f"role other than system can only be set while the gateway's "
            f"registry answers") from None
    _save(state_dir, roles)
    if role not in roles:
        raise ValueError(
            f"agent role {role!r} is not in the gateway's registry "
            f"(known: {', '.join(sorted(roles))}). Roles are added from cu's "
            f"configuration")
    return roles[role]


def at_launch(role: str, bridge_url: str | None,
              state_dir: Path | None) -> tuple[bool, str]:
    """At launch: (still exists, description). Falls back to the last copy
    when the gateway does not answer; with no copy at all it assumes the
    role still exists, because the write that set it was checked."""
    if role == session_types.DEFAULT_AGENT_ROLE:
        return True, ""
    try:
        roles = fetch(bridge_url)
        _save(state_dir, roles)
    except Unreachable:
        roles = _cached(state_dir)
        if roles is None:
            return True, ""
    return role in roles, roles.get(role, "")


def as_list(roles: dict[str, str]) -> list[dict[str, Any]]:
    return [{"name": n, "description": d} for n, d in sorted(roles.items())]
