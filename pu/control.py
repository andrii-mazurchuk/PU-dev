"""Two operator facts kept in private storage: the kill switch, and the
why-not log.

**The kill switch** is one flag. While it is set the tick still evaluates
triggers -- cursors keep moving and work is still created, so nothing is
lost while paused -- but it launches nothing. Pausing one session type is
already `enabled: false` on its record. Whether pressing this must be
human-only falls under the standards ticket on human-only endpoints; a
pause is the safe direction, so it is not held back waiting for that.

**The why-not log** answers "why didn't X run?", which until now had an
answer only for runs that happened. Every tick's decision is appended --
but a decision identical to the previous one is not, so a gate blocked
for six hours is one line and an idle unit writes nothing.
(pu vision, 2026-10-24.)
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PAUSE_FILE = "paused.json"
TICK_LOG = "ticks.jsonl"

# ponytail: trimmed by line count on write, not by age. A deduplicated log
# at a 30s heartbeat stays far under this; move to dated files if it ever
# does not.
MAX_LINES = 5000
KEEP_LINES = 4000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# -- the kill switch ------------------------------------------------------


def paused(state_dir: Path | None) -> dict[str, Any] | None:
    """The pause record while set, else None. Unreadable means not paused:
    a corrupt file must not stop the unit, and must not start it either --
    it simply was never paused."""
    if state_dir is None:
        return None
    try:
        record = json.loads((Path(state_dir) / PAUSE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) and record.get("paused") else None


def set_paused(state_dir: Path, value: bool, reason: str = "") -> dict[str, Any]:
    record = {"paused": bool(value), "since": _now(), "reason": reason or ""}
    path = Path(state_dir) / PAUSE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")
    return record


# -- the concurrency ceiling, as a live setting ---------------------------

CONCURRENCY_FILE = "concurrency.json"


def configured_max(state_dir: Path | None) -> int | None:
    """The owner's live ceiling, or None to fall back to the env var."""
    if state_dir is None:
        return None
    try:
        value = int(json.loads((Path(state_dir) / CONCURRENCY_FILE)
                               .read_text(encoding="utf-8"))["max"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return value if value >= 1 else None


def set_max_concurrent(state_dir: Path, value: int) -> dict[str, Any]:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError("max is a whole number of sessions, at least 1")
    record = {"max": value, "since": _now()}
    path = Path(state_dir) / CONCURRENCY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")
    return record


# -- the why-not log ------------------------------------------------------


def _key(entry: dict[str, Any]) -> tuple:
    return (entry.get("ran"), entry.get("reason"), entry.get("outcome"),
            entry.get("task_uuid"), json.dumps(entry.get("emitted") or []))


def _lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def note_tick(state_dir: Path, result: dict[str, Any]) -> bool:
    """Append this tick's decision unless it repeats the last one. A tick
    that ran a session is always written. Best-effort: losing a log line
    must never fail a tick. Returns whether a line was written."""
    path = Path(state_dir) / TICK_LOG
    lines = _lines(path)
    if not result.get("ran") and lines:
        try:
            if _key(json.loads(lines[-1])) == _key(result):
                return False
        except ValueError:
            pass
    entry = {"at": _now(), **result}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if len(lines) >= MAX_LINES:
            path.write_text("\n".join(lines[-KEEP_LINES:]) + "\n",
                            encoding="utf-8", newline="\n")
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(entry) + "\n")
    except OSError:
        return False
    return True


def ticks(state_dir: Path | None, limit: int = 50) -> list[dict[str, Any]]:
    """Most recent first."""
    if state_dir is None:
        return []
    out: list[dict[str, Any]] = []
    for line in reversed(_lines(Path(state_dir) / TICK_LOG)):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
        if len(out) >= limit:
            break
    return out


# -- silence --------------------------------------------------------------
#
# A dead loop looks exactly like a quiet day: no runs, no errors, nothing.
# So every tick stamps when it happened, and a tick that finds the previous
# stamp far older than the heartbeat says so -- once, on the tick that ends
# the silence. Slots a schedule missed in the gap fire late on this tick
# (triggers walk forward from their last fire), which is why the message
# says so. pu being dead outright is the gateway's to notice; it polls
# health. (pu vision, 2026-10-24.)

HEARTBEAT_FILE = "heartbeat.json"
DEFAULT_HEARTBEAT_SECONDS = 30
# Ten missed beats, and never less than ten minutes: a slow tick or one
# restart is not silence.
SILENT_BEATS = 10
SILENT_FLOOR_SECONDS = 600


def heartbeat_seconds(environ: dict[str, str] | None = None) -> float:
    """The interval the gateway pokes at. One value, set where the gateway
    sets everything else; this only reads it."""
    raw = (environ if environ is not None else os.environ).get(
        "PU_HEARTBEAT_SECONDS", "")
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_HEARTBEAT_SECONDS
    return value if value > 0 else DEFAULT_HEARTBEAT_SECONDS


def silence(state_dir: Path, moment: datetime,
            interval: float = DEFAULT_HEARTBEAT_SECONDS) -> float | None:
    """Stamp this tick. Returns how many seconds pu went unticked, when
    that gap counts as silence, else None."""
    path = Path(state_dir) / HEARTBEAT_FILE
    gap = None
    state: dict[str, Any] = {}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        gap = (moment - datetime.fromisoformat(state["at"])).total_seconds()
    except (OSError, ValueError, KeyError, TypeError):
        pass
    silent = gap is not None and gap > max(SILENT_BEATS * interval,
                                           SILENT_FLOOR_SECONDS)
    # Counters that only grow, for stats: ticks, silences, and when the
    # count started.
    record = {
        "at": moment.isoformat(),
        "since": state.get("since") or moment.isoformat(),
        "ticks": int(state.get("ticks") or 0) + 1,
        "silences": int(state.get("silences") or 0) + (1 if silent else 0),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record), encoding="utf-8")
    except OSError:
        pass
    return gap if silent else None


def heartbeat(state_dir: Path | None) -> dict[str, Any]:
    if state_dir is None:
        return {}
    try:
        return json.loads((Path(state_dir) / HEARTBEAT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
