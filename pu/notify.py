"""Telling a person when pu has stopped and needs one.

The first moment, and once silent: the repeat-dispatch breaker taking a
task away from agents. It wrote its reason onto the record and told nobody -- which in a unit designed to
run unattended is a stall you only discover by going to look.

**Addressed to the role `owner`, never to a unit name.** Which unit serves
that role, and which thread within it, is resolved at the bridge from
`delivery_policy.json`. So pu can notify a person without knowing that a
communication unit exists, let alone which one is installed -- the same
principle as `logs_client` finding the logs peer by capability.

**Through the bridge, unlike `logs_client`, and that difference is the
point.** The standard's default is direct HTTP to a peer's `base_url`, and
`logs_client` follows it. That is not available here: there is no
`base_url` for "the owner". Role addressing is exactly what `POST /route`
exists for, so this is the documented case for involving a third process
rather than an exception to the encapsulation rule.

The cost is real and accepted: a notification depends on the bridge being
up. Which is why **everything here returns False and never raises.** No
bridge configured, an unreachable one, an unconfigured owner, a tool the
target does not have -- all the same answer. A notification that cannot be
delivered must never turn a tick that did its work into a failure, and an
unconfigured owner is a normal state: a system can genuinely have no owner.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

ROUTE_PATH = "/route"
OWNER_ROLE = "owner"
TIMEOUT_SECONDS = 3.0

# The tool to call on whichever unit serves `owner`. pu cannot know this:
# `/route` requires a tool name, and the unit behind the role is chosen by
# the deployment. So it is config, for the same reason `PU_TASK_CMD` is --
# where a thing lives, or what it is called, is not an assumption to bake
# into code. A wrong name is safe and visible: the bridge answers
# `delivered: false` with a reason rather than raising.
DEFAULT_TOOL = "send_message"

# The argument the message travels in. Configurable for the same reason
# the tool name is, and it has to be: the two are one contract, and
# getting the tool right while getting this wrong is the worse failure.
# A wrong *tool* name is loud -- /route answers delivered:false with a
# reason. A wrong *argument* name is silent: the tool exists, dispatch
# succeeds, the receiving unit reads the key it expects, finds nothing,
# and queues an empty message. Measured live against cu, whose
# push_notification takes `content` while this defaulted to `text`.
DEFAULT_ARG = "text"


def bridge_url(environ: dict[str, str] | None = None) -> str | None:
    """The gateway's MCP bridge, or None when there isn't one.

    None is the ordinary state before registration, not an error -- and it
    is what makes every call here a no-op rather than a failure."""
    env = os.environ if environ is None else environ
    return (env.get("PU_MCP_BRIDGE_URL", "").strip() or None)


def notify_tool(environ: dict[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    return env.get("PU_NOTIFY_TOOL", "").strip() or DEFAULT_TOOL


def notify_arg(environ: dict[str, str] | None = None) -> str:
    """Which argument the target tool takes the message in."""
    env = os.environ if environ is None else environ
    return env.get("PU_NOTIFY_ARG", "").strip() or DEFAULT_ARG


def notify_owner(
    url: str | None,
    text: str,
    source_unit: str,
    tool: str | None = None,
    arg: str | None = None,
    opener=urllib.request.urlopen,
) -> bool:
    """One message to whoever holds the `owner` role. True if the bridge
    said it was delivered; False for every other outcome, including the
    entirely normal ones.

    `delivered: false` is a documented answer from `/route`, not an error
    condition -- so a 200 carrying it still returns False here, because the
    question this function answers is whether a person was told."""
    if not url or not text.strip():
        return False

    body = json.dumps({
        "from": source_unit,
        "to": OWNER_ROLE,
        "tool": tool or notify_tool(),
        "args": {arg or notify_arg(): text},
    }).encode("utf-8")

    request = urllib.request.Request(
        str(url).rstrip("/") + ROUTE_PATH,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener(request, timeout=TIMEOUT_SECONDS) as response:
            parsed: Any = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return False
    return bool(isinstance(parsed, dict) and parsed.get("delivered"))


# -- batched notices --------------------------------------------------------
#
# Andrey, 2026-11-04: switching several types and triggers on in a row sent
# one long message per flip, each rewritten by cu's agent. A flip is now one
# queued line; the tick sends the queue as one message once nothing new has
# arrived for QUIET_SECONDS, so a burst of switches is one message within a
# tick or so of its end. Warnings (a live record switched off by an edit)
# still go at once -- only routine news waits.

PENDING_FILE = "pending_notices.jsonl"
QUIET_SECONDS = 5.0


def queue_owner(state_dir, line: str, at: float | None = None) -> None:
    """Add one line to the next batched message. Best-effort: losing a
    notice must never fail the write that caused it."""
    import time
    from pathlib import Path
    try:
        path = Path(state_dir) / PENDING_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps({"at": time.time() if at is None else at,
                                     "line": line}) + "\n")
    except OSError:
        pass


def flush_owner(state_dir, url: str | None, source_unit: str,
                now: float | None = None, send=None) -> bool:
    """Send the queued lines as one message once the burst is over. The
    queue is cleared only when the message was delivered, so an unreachable
    owner keeps it for the next tick. True when something was sent."""
    import time
    from pathlib import Path
    path = Path(state_dir) / PENDING_FILE
    try:
        entries = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    except (OSError, ValueError):
        return False
    if not entries:
        return False
    moment = time.time() if now is None else now
    if moment - max(float(e.get("at") or 0) for e in entries) < QUIET_SECONDS:
        return False
    lines = [str(e.get("line") or "") for e in entries]
    text = lines[0] if len(lines) == 1 else (
        f"pu: {len(lines)} changes\n" + "\n".join(f"- {l}" for l in lines))
    if not (send or notify_owner)(url, text, source_unit):
        return False
    try:
        path.unlink()
    except OSError:
        pass
    return True
