"""Local run artifacts, and the aggregates `/stats` reports over them.

This is **not** where session logs live for the system. Those go to the
logs memory unit as `session_run` entries -- that is the standard, and
`logs_client.py` does it. What is kept here is the two things that entry
deliberately does not carry:

- **the raw stream**, because the parsed summary discards tool-call
  arguments, and this is the only on-disk record of what a session that
  died mid-run actually wrote;
- **enough history to compute aggregates**, so `/stats` can answer without
  a round-trip to another unit -- which would make this unit's own stats
  endpoint depend on a peer being up.

On what `/stats` may say: the standard is mechanical data, never agentic
judgement, and the intent is precomputed numbers an analytical unit can
judge. A derived aggregate is mechanical; a verdict is not. Cost per
session type, failure counts, durations -- yes. "The queue is unhealthy" --
never. That is AU's call, and computing it here would be PU grading its own
work.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pu import runner as runner_mod

# A run is addressed by two path segments that arrive from a URL. A
# Taskwarrior uuid and this module's own stamps both match this; `..`
# and anything carrying a separator do not.
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


class SessionStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def record(
        self,
        task_uuid: str,
        session_type: str,
        prompt: str,
        result: runner_mod.SessionResult,
        outcome: str,
        detail: str = "",
    ) -> Path:
        """Persist one run. Returns the directory it went to.

        Best-effort: a full disk must not turn a session that actually did
        its work into a failure, so a write problem is swallowed and the
        caller still gets a path back."""
        run_dir = self.root / task_uuid / _stamp()
        try:
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
            (run_dir / "stream.jsonl").write_text(result.raw_stream, encoding="utf-8")
            (run_dir / "result.json").write_text(
                json.dumps({
                    "task_uuid": task_uuid,
                    "session_type": session_type,
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                    "outcome": outcome,
                    "detail": detail,
                    "exit_code": result.exit_code,
                    "is_error": result.is_error,
                    "cost_usd": result.cost_usd,
                    "duration_ms": result.duration_ms,
                    "num_turns": result.num_turns,
                    "tool_calls": list(result.tool_calls),
                    "models_used": list(result.models_used),
                    "permission_denials": list(result.permission_denials),
                }, indent=1),
                encoding="utf-8",
            )
        except OSError:
            pass
        return run_dir

    def _results(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        try:
            paths = sorted(self.root.glob("*/*/result.json"))
        except OSError:
            return out
        for path in paths:
            try:
                parsed = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(parsed, dict):
                # Where it was found, so a caller can address one run
                # without re-deriving this layout. The directory wins
                # over the `task_uuid` in the file if they ever disagree:
                # the directory is what was actually looked in.
                parsed["task_uuid"] = path.parent.parent.name
                parsed["run_id"] = path.parent.name
                out.append(parsed)
        return out

    def runs(self, limit: int = 50) -> list[dict[str, Any]]:
        """Every recorded run, most recent first.

        Ordered on `recorded_at` rather than on the directory name: the
        stamp is the moment the directory was created, and a session
        that ran for four minutes finishes out of that order.
        """
        ordered = sorted(
            self._results(),
            key=lambda r: str(r.get("recorded_at") or ""),
            reverse=True,
        )
        return ordered[: max(0, limit)]

    def run(self, task_uuid: str, run_id: str) -> dict[str, Any] | None:
        """One run, with the prompt it was given.

        The raw stream is deliberately not returned. It is the largest
        thing this unit keeps on disk and nothing on the other end can
        render it -- whoever genuinely needs it reads the file, which is
        the reason it is kept at all.
        """
        if not (_SAFE_SEGMENT.match(task_uuid) and _SAFE_SEGMENT.match(run_id)):
            # Both halves arrive from a URL, and joining an unchecked
            # segment onto a root is the whole of a path traversal.
            return None
        run_dir = self.root / task_uuid / run_id
        try:
            parsed = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(parsed, dict):
            return None
        parsed["task_uuid"] = task_uuid
        parsed["run_id"] = run_id
        try:
            parsed["prompt"] = (run_dir / "prompt.txt").read_text(encoding="utf-8")
        except OSError:
            parsed["prompt"] = ""
        return parsed

    def cost_by_day(self, days: int = 14) -> list[dict[str, Any]]:
        """Daily spend, oldest first, with quiet days present as zero.

        The zeros are not padding. A series that omits the days nothing
        ran on renders as continuous work at an even rate, which is the
        opposite of what happened.
        """
        totals: dict[str, float] = {}
        for record in self._results():
            day = str(record.get("recorded_at") or "")[:10]
            if len(day) == 10:
                totals[day] = totals.get(day, 0.0) + float(record.get("cost_usd") or 0.0)
        today = datetime.now(timezone.utc).date()
        out: list[dict[str, Any]] = []
        for offset in range(max(1, days) - 1, -1, -1):
            day = (today - timedelta(days=offset)).isoformat()
            out.append({"day": day, "cost_usd": round(totals.get(day, 0.0), 6)})
        return out

    def cost_since(self, iso_prefix: str) -> float:
        """Total cost of runs whose `recorded_at` starts with `iso_prefix`
        -- a date string gives the day's spend. Used by the cost gate."""
        return round(sum(
            float(r.get("cost_usd") or 0.0)
            for r in self._results()
            if str(r.get("recorded_at", "")).startswith(iso_prefix)
        ), 6)

    def aggregates(self) -> dict[str, Any]:
        """Derived numbers, no verdicts."""
        results = self._results()
        by_type: dict[str, dict[str, Any]] = {}
        by_outcome: dict[str, int] = {}
        total_cost = 0.0

        for record in results:
            stype = str(record.get("session_type") or "unknown")
            bucket = by_type.setdefault(
                stype,
                {"runs": 0, "failures": 0, "cost_usd": 0.0, "duration_ms_total": 0},
            )
            bucket["runs"] += 1
            cost = float(record.get("cost_usd") or 0.0)
            bucket["cost_usd"] = round(bucket["cost_usd"] + cost, 6)
            total_cost += cost
            bucket["duration_ms_total"] += int(record.get("duration_ms") or 0)

            outcome = str(record.get("outcome") or "unknown")
            by_outcome[outcome] = by_outcome.get(outcome, 0) + 1
            if record.get("exit_code") or record.get("is_error"):
                bucket["failures"] += 1

        for bucket in by_type.values():
            runs = bucket["runs"] or 1
            bucket["avg_duration_ms"] = bucket.pop("duration_ms_total") // runs
            bucket["avg_cost_usd"] = round(bucket["cost_usd"] / runs, 6)

        last = max((str(r.get("recorded_at") or "") for r in results), default=None)
        return {
            "runs": len(results),
            "cost_usd_total": round(total_cost, 6),
            "by_session_type": by_type,
            "by_outcome": by_outcome,
            "last_run_at": last or None,
        }
