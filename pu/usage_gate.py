"""The account-usage ceilings, unit side.

The standard is `holonic-node/docs/UNIT_STANDARDS.md`, "Account-usage
ceilings". The short version: the Claude subscription has two rolling
windows, and above either one's ceiling no unit starts a new session.

**Why this is reimplemented here rather than imported.** Units share no
code by design -- see the standard's note on there being no shared
unit-server base class. The gateway owns the *number*; each unit owns its
own decision to stop. What must not drift is the meaning of "over the
line", so the two rules below are stated the same way in every unit and
pinned by tests in each.

**Two rules that read like bugs and are the opposite:**

- **No reading allows.** The figures only exist as a by-product of a
  session that has already run -- there is no way to ask for them. A node
  that has never run one knows nothing, and blocking on absence would
  mean it could never run the first.
- **A reading past its `resets_at` allows.** A blocked node runs no
  sessions, so nothing refreshes the reading. Without expiry, a node that
  once touched the ceiling would stay blocked for good.

Neither is leniency. Both are what keeps the gate from latching shut.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

USAGE_FILENAME = "usage.json"

FIVE_HOUR = "five_hour"
SEVEN_DAY = "seven_day"
WINDOWS = (FIVE_HOUR, SEVEN_DAY)

# Matches the gateway's own defaults. Present so the unit still has a
# ceiling when started by hand with no gateway to inject one -- never so
# that a missing variable means "no limit".
DEFAULT_CEILINGS: dict[str, float] = {FIVE_HOUR: 0.70, SEVEN_DAY: 0.80}

CEILING_ENV = {
    FIVE_HOUR: "HOLONIC_CONTEXT_CEILING_FIVE_HOUR",
    SEVEN_DAY: "HOLONIC_CONTEXT_CEILING_SEVEN_DAY",
}


def ceilings_from_env(environ: dict[str, str] | None = None) -> dict[str, float]:
    """What the gateway injected. An absent or unparseable value falls
    back to the default rather than to no limit: a typo in a manifest
    must not silently remove the ceiling."""
    env = os.environ if environ is None else environ
    result = dict(DEFAULT_CEILINGS)
    for window, key in CEILING_ENV.items():
        raw = env.get(key)
        if raw is None:
            continue
        try:
            result[window] = float(raw)
        except (TypeError, ValueError):
            continue
    return result


def read_usage(path: Path) -> dict[str, Any]:
    """The current reading, written into this unit's own directory by the
    gateway and refreshed every tick. Missing or torn degrades to {} --
    indistinguishable from "nothing has run yet", and both mean allow."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    windows = raw.get("windows") if isinstance(raw, dict) else None
    return windows if isinstance(windows, dict) else {}


def check_usage_gate(
    windows: dict[str, Any],
    ceilings: dict[str, float],
    now: float | None = None,
) -> str | None:
    """The reason no session may start, or None to proceed. Either window
    over its own ceiling blocks -- they are separate limits, not an
    average."""
    now = time.time() if now is None else now
    for window in WINDOWS:
        ceiling = ceilings.get(window)
        reading = windows.get(window)
        if ceiling is None or not isinstance(reading, dict):
            continue
        utilization = reading.get("utilization")
        if not isinstance(utilization, (int, float)):
            continue
        resets_at = reading.get("resets_at")
        if isinstance(resets_at, (int, float)) and now >= resets_at:
            continue
        if utilization >= ceiling:
            return f"{window} usage at {utilization:.0%}, ceiling {ceiling:.0%}"
    return None


def parse_rate_limit_event(event: dict[str, Any]) -> dict[str, Any]:
    """Pull the windows out of a CLI `rate_limit_event`.

    Tolerant on purpose: this reads output from a tool we do not version,
    during the handling of a session that has already succeeded. An
    unrecognised payload contributes nothing rather than raising.
    """
    info = event.get("rate_limit_info") or {}
    raw = info.get("unifiedWindows") or {}
    parsed: dict[str, Any] = {}
    for window in WINDOWS:
        reading = raw.get(window)
        if not isinstance(reading, dict):
            continue
        utilization = reading.get("utilization")
        if not isinstance(utilization, (int, float)):
            continue
        entry: dict[str, Any] = {"utilization": float(utilization)}
        resets_at = reading.get("resetsAt")
        if isinstance(resets_at, (int, float)):
            entry["resets_at"] = int(resets_at)
        parsed[window] = entry
    return parsed


def report_usage(
    bridge_url: str | None, windows: dict[str, Any], timeout: float = 3.0
) -> bool:
    """Post what this session saw to the gateway, which records it once
    for the whole node.

    Best-effort in both directions, like every other audit write here: the
    session it came from has already succeeded, and a bridge that is down
    or slow must never turn that into a failure. Returns whether it
    landed, for tests and logs -- no caller is expected to act on it.
    """
    if not bridge_url or not windows:
        return False
    body = json.dumps({"windows": windows}).encode()
    request = urllib.request.Request(
        f"{bridge_url.rstrip('/')}/usage",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


# --- notify-once ---------------------------------------------------------
#
# Being blocked is a state, not an event. A message every tick would be
# one every few minutes for as long as the window stays full, which
# trains its own reader to ignore it -- so the transition is what gets
# reported, in both directions, and the flag that remembers which side we
# are on lives in this unit's own state.

def load_blocked_state(path: Path) -> str | None:
    """The reason last reported, or None if the last thing reported was
    recovery. Missing or torn reads as "not blocked", which at worst
    causes one extra notification rather than silence."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    reason = raw.get("blocked_reason") if isinstance(raw, dict) else None
    return reason if isinstance(reason, str) else None


def save_blocked_state(path: Path, reason: str | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"blocked_reason": reason}), encoding="utf-8", newline="\n"
    )


def blocked_transition(previous: str | None, reason: str | None) -> str | None:
    """The message to send, or None to stay quiet.

    Quiet covers both steady states: still blocked for the same reason,
    and still running. A *changed* reason is worth saying -- the five-hour
    window giving way to the seven-day one is a different situation with a
    different horizon.
    """
    if reason and reason != previous:
        return (
            f"Sessions paused: {reason}.\n"
            f"Nothing is lost -- the queue is untouched and work resumes "
            f"by itself once the window refills."
        )
    if previous and not reason:
        return "Usage is back under the ceiling; sessions have resumed."
    return None
