"""A best-effort copy of every session run, to whoever stores logs.

Two rules from the standard shape this whole module.

**Address by capability, never by name.** The logs unit is found by looking
for a peer in `peers.json` declaring `log_write`. A literal unit name in
code makes that unit un-renameable and un-swappable, which is the opposite
of what a holonic system is for.

**Go direct, not through the bridge.** A unit's ability to record what it
did must not depend on a third unit being up. `peers.json` gives the
`base_url`; that is all that is needed.

Everything here degrades. No peers file, no logs peer, an unreachable one,
a rejected payload -- each resolves to `False`, never an exception. A
broken logs unit can never turn a session that did its work into a
failure.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

LOG_WRITE_CAPABILITY = "log_write"
CREATE_ENTRY_PATH = "/entries"
TIMEOUT_SECONDS = 3.0


def find_logs_peer(peers_path: Path) -> dict[str, Any] | None:
    """The peer that stores logs, or None if there isn't one.

    None is a normal, expected state -- running standalone, or before the
    logs unit is registered -- not an error to report."""
    try:
        parsed = json.loads(Path(peers_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    for peer in parsed.get("units") or ():
        if not isinstance(peer, dict):
            continue
        if LOG_WRITE_CAPABILITY in (peer.get("capabilities") or ()) and peer.get("base_url"):
            return peer
    return None


def record_session_run(
    peers_path: Path,
    source_unit: str,
    outcome: str,
    duration_seconds: float | None,
    cost: float | None,
    extra: dict[str, Any] | None = None,
    opener=urllib.request.urlopen,
) -> bool:
    """One `session_run` entry. True if it landed, False for every other
    outcome including the entirely normal ones.

    The payload's three core fields are the ones the logs unit rolls up
    (`avg_duration_seconds`, `failure_count`, `total_cost_usd`); anything
    in `extra` rides along for later reading but is not aggregated."""
    peer = find_logs_peer(peers_path)
    if peer is None:
        return False

    payload: dict[str, Any] = {
        "outcome": outcome,
        "duration_seconds": duration_seconds,
        "cost": cost,
    }
    if extra:
        payload.update(extra)

    body = json.dumps({
        "source_unit": source_unit,
        "entry_type": "session_run",
        "payload": payload,
    }).encode("utf-8")

    url = str(peer["base_url"]).rstrip("/") + CREATE_ENTRY_PATH
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with opener(request, timeout=TIMEOUT_SECONDS) as response:
            return 200 <= getattr(response, "status", 200) < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False
