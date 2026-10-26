"""What /stats computes: sessions, triggers, tasks and the unit itself.

Decided 2026-10-26 (stats-and-logs grilling). Mechanical numbers only --
never a verdict; judging them is au's job. Every numeric measure carries
count, sum, average, min and max; the windows (today, last 7 and 30 days)
add p50 and p95, which a running all-time rollup cannot keep.

Windows: all time, today, last 7 days, last 30 days. The per-type, per-role
and per-trigger breakdowns carry all time and 30 days only -- four windows
on every breakdown would quadruple the body for little gain.

All time comes from `rollup.json`, updated as each run is recorded, so a
poll does not reread every run record ever written. The windows scan the
last 30 days of records, a bounded set. Both are rebuildable from the run
records at any time.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

NUMERIC = {
    "cost": lambda r: r.get("cost_usd"),
    "duration_ms": lambda r: r.get("duration_ms"),
    "turns": lambda r: r.get("num_turns"),
    "queue_wait_s": lambda r: r.get("queued_seconds"),
}
TOKEN_KINDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens",
               "cache_read_input_tokens", "thinking_tokens")
SUCCESS = ("resolved", "resolved_unstructured")
HANDED = ("failed_twice", "needs_human")


# -- accumulating ---------------------------------------------------------


def empty() -> dict[str, Any]:
    return {"runs": 0, "outcomes": {}, "failures": {}, "measures": {},
            "tokens": {}}


def _fold(stat: dict | None, value) -> dict:
    stat = stat or {"n": 0, "sum": 0.0, "min": None, "max": None}
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        stat["n"] += 1
        stat["sum"] = round(stat["sum"] + value, 6)
        stat["min"] = value if stat["min"] is None else min(stat["min"], value)
        stat["max"] = value if stat["max"] is None else max(stat["max"], value)
    return stat


def add(acc: dict[str, Any], record: dict[str, Any]) -> None:
    """Fold one run record into an accumulator. Works for the rollup and
    for a window alike."""
    acc["runs"] += 1
    outcome = str(record.get("outcome") or "unknown")
    acc["outcomes"][outcome] = acc["outcomes"].get(outcome, 0) + 1
    cls = record.get("failure_class")
    if cls:
        acc["failures"][cls] = acc["failures"].get(cls, 0) + 1
    if outcome in HANDED:
        acc["failures"]["handed_to_you"] = acc["failures"].get("handed_to_you", 0) + 1
    for name, read in NUMERIC.items():
        acc["measures"][name] = _fold(acc["measures"].get(name), read(record))
    for kind in TOKEN_KINDS:
        acc["tokens"][kind] = _fold(acc["tokens"].get(kind),
                                    (record.get("tokens") or {}).get(kind))


def _render_stat(stat: dict | None, values: list | None = None) -> dict:
    if not stat or not stat["n"]:
        return {"n": 0}
    out = {"n": stat["n"], "sum": round(stat["sum"], 6),
           "avg": round(stat["sum"] / stat["n"], 6),
           "min": stat["min"], "max": stat["max"]}
    if values:
        ordered = sorted(values)
        out["p50"] = ordered[len(ordered) // 2]
        out["p95"] = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    return out


def render(acc: dict[str, Any], records: list[dict] | None = None) -> dict:
    """The measures as stats shows them. With the records (a window), also
    percentiles and throughput."""
    runs = acc["runs"]
    successes = sum(acc["outcomes"].get(o, 0) for o in SUCCESS)

    def values(read):
        return [v for v in (read(r) for r in records or ())
                if isinstance(v, (int, float)) and not isinstance(v, bool)]

    measures = {name: _render_stat(acc["measures"].get(name),
                                   values(read) if records is not None else None)
                for name, read in NUMERIC.items()}
    cost = measures.pop("cost")
    if successes:
        cost["per_success"] = round(cost.get("sum", 0) / successes, 6)
    tokens = {kind: _render_stat(acc["tokens"].get(kind)) for kind in TOKEN_KINDS}
    reads = acc["tokens"].get("cache_read_input_tokens", {}).get("sum") or 0
    fresh = sum((acc["tokens"].get(k, {}).get("sum") or 0) for k in (
        "input_tokens", "cache_creation_input_tokens"))
    if reads + fresh:
        tokens["cache_hit_ratio"] = round(reads / (reads + fresh), 4)
    out = {
        "runs": runs,
        "outcomes": dict(acc["outcomes"]),
        "outcome_shares": {k: round(v / runs, 4) for k, v in acc["outcomes"].items()}
        if runs else {},
        "failures": dict(acc["failures"]),
        "cost": cost,
        "tokens": tokens,
        "duration_ms": measures["duration_ms"],
        "turns": measures["turns"],
        "queue_wait_s": measures["queue_wait_s"],
    }
    if records is not None:
        out["throughput"] = throughput(records)
    return out


def throughput(records: list[dict]) -> dict[str, Any]:
    """Runs per day, the busiest hour, and the most sessions that were
    ever running at once -- swept from each run's start and end."""
    if not records:
        return {"runs_per_day": {"avg": 0, "max": 0}}
    per_day: dict[str, int] = {}
    per_hour: dict[int, int] = {}
    edges = []
    for r in records:
        end = _when(r.get("recorded_at"))
        if end is None:
            continue
        start = end - timedelta(milliseconds=int(r.get("duration_ms") or 0))
        per_day[end.date().isoformat()] = per_day.get(end.date().isoformat(), 0) + 1
        per_hour[start.hour] = per_hour.get(start.hour, 0) + 1
        edges += [(start, 1), (end, -1)]
    running = peak = 0
    for _, step in sorted(edges, key=lambda e: (e[0], e[1])):
        running += step
        peak = max(peak, running)
    days = max(len(per_day), 1)
    return {
        "runs_per_day": {"avg": round(sum(per_day.values()) / days, 3),
                         "max": max(per_day.values(), default=0)},
        "busiest_hour_utc": max(per_hour, key=per_hour.get) if per_hour else None,
        "peak_simultaneous": peak,
    }


def _when(value) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _groups(record: dict) -> list[str]:
    return ["all",
            f"type:{record.get('session_type') or 'unknown'}",
            f"role:{record.get('agent_role') or 'system'}",
            f"trigger:{(record.get('started_by') or 'task').removeprefix('trigger:')}"]


# -- the rollup: all time, incrementally ----------------------------------

ROLLUP = "rollup.json"


def rebuild_rollup(records: Iterable[dict]) -> dict[str, Any]:
    rollup: dict[str, Any] = {}
    for record in records:
        for key in _groups(record):
            add(rollup.setdefault(key, empty()), record)
    return rollup


def fold_into_rollup(root: Path, record: dict, all_records) -> None:
    """Update the rollup with one new run. Missing or unreadable, it is
    rebuilt from every record -- it is a cache, never the truth."""
    path = Path(root) / ROLLUP
    try:
        rollup = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rollup = rebuild_rollup(all_records())
    else:
        for key in _groups(record):
            add(rollup.setdefault(key, empty()), record)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rollup), encoding="utf-8")
    except OSError:
        pass


def load_rollup(root: Path, all_records) -> dict[str, Any]:
    try:
        return json.loads((Path(root) / ROLLUP).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return rebuild_rollup(all_records())


# -- the sessions section -------------------------------------------------


def sessions(session_store, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    rollup = load_rollup(session_store.root, session_store._results)
    recent = session_store.recent(30, now)
    starts = {
        "today": now.replace(hour=0, minute=0, second=0, microsecond=0),
        "d7": now - timedelta(days=7),
        "d30": now - timedelta(days=30),
    }

    def window(since, key="all"):
        chosen = [r for r in recent
                  if (_when(r.get("recorded_at")) or now) >= since
                  and key in _groups(r)]
        acc = empty()
        for r in chosen:
            add(acc, r)
        return render(acc, chosen)

    all_time = render(rollup.get("all", empty()))
    out = {
        # Kept where the dashboard reads them.
        "runs": all_time["runs"],
        "cost_usd_total": all_time["cost"].get("sum", 0),
        "last_run_at": max((str(r.get("recorded_at") or "") for r in recent),
                           default=None) or None,
        "all": all_time,
        **{name: window(since) for name, since in starts.items()},
    }
    for prefix, label in (("type:", "by_type"), ("role:", "by_role"),
                          ("trigger:", "by_trigger")):
        out[label] = {
            key.removeprefix(prefix): {"all": render(acc),
                                       "d30": window(starts["d30"], key)}
            for key, acc in sorted(rollup.items()) if key.startswith(prefix)}
    return out


# -- the triggers section -------------------------------------------------


def triggers(trigger_store, store, session_store,
             now: datetime | None = None) -> dict[str, Any]:
    if trigger_store is None:
        return {}
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=30)
    try:
        made = [t for t in store.export(f"project:{_runs_project()}")
                if t.trigger]
    except Exception:  # noqa: BLE001 -- stats degrade, never fail
        made = []
    runs = [r for r in session_store.recent(30, now)
            if str(r.get("started_by") or "").startswith("trigger:")]
    out: dict[str, Any] = {}
    for name, record in sorted(trigger_store.current().items()):
        if record.get("retired"):
            continue
        cursor = trigger_store.cursor(name)
        tasks = [t for t in made if t.trigger == name
                 and (_tw_when(t.entry) or now) >= since]
        mine = [r for r in runs if r["started_by"] == f"trigger:{name}"]
        expired = [t for t in tasks if "expired" in t.tags]
        reasons: dict[str, int] = {}
        for t in expired:
            why = next((a.split("(", 1)[-1].split(")", 1)[0]
                        for a in t.annotations if a.startswith("expired:")), "unknown")
            reasons[why] = reasons.get(why, 0) + 1
        fires = int(cursor.get("fires") or 0)
        out[name] = {
            "enabled": bool(record.get("enabled")),
            "source": (record.get("source") or {}).get("kind"),
            "fires": {"all": fires, "d30": len(tasks)},
            "items_per_fire": {
                "avg": round(int(cursor.get("items_total") or 0) / fires, 3) if fires else 0,
                "max": int(cursor.get("items_max") or 0)},
            "conversion_d30": {
                "tasks": len(tasks), "runs": len(mine),
                "resolved": sum(1 for r in mine if r.get("outcome") in SUCCESS),
                "expired": len(expired)},
            "expired_d30": {"count": len(expired), "by_reason": reasons},
            "fetches": {"total": int(cursor.get("fetches") or 0),
                        "failed": int(cursor.get("fetch_failures") or 0),
                        "failing_now": int(cursor.get("consecutive_failures") or 0)},
            "backoff": {"episodes": int(cursor.get("backoffs") or 0),
                        "seconds_total": cursor.get("backoff_seconds_total") or 0,
                        "until": cursor.get("backoff_until")},
            "freshness": {
                "last_new_at": cursor.get("last_new_at"),
                "last_fired_at": cursor.get("fired_at"),
                "seconds_since_new": _since(cursor.get("last_new_at"), now),
                "seconds_since_fire": _since(cursor.get("fired_at"), now)},
        }
    return out


def _since(value, now) -> float | None:
    moment = _when(value)
    return round((now - moment).total_seconds(), 1) if moment else None


def _tw_when(value) -> datetime | None:
    try:
        return datetime.strptime(value or "", "%Y%m%dT%H%M%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None


def _runs_project() -> str:
    from pu import records
    return records.RUNS_PROJECT


# -- the tasks section ----------------------------------------------------


def tasks(store, now: datetime | None = None) -> dict[str, Any]:
    """Open work, age, flow and time to resolve, split three ways: agent
    work, work that is yours, and work that is blocked -- so au can tell
    how long you take from how long agent work waits."""
    from pu import guards, records

    now = now or datetime.now(timezone.utc)
    try:
        pending = [t for t in store.export("status:pending") if t.kind != "map"]
        blocked_ids = {t.uuid for t in store.export("status:pending", "+BLOCKED")}
        # Filtered here, not with `end.after:` -- on the live node's
        # Taskwarrior that filter returned nothing while the same query
        # worked locally, and every completed count read zero.
        month_ago = now - timedelta(days=30)
        done = [t for t in store.export("status:completed")
                if t.kind != "map" and (_tw_when(t.end) or month_ago) > month_ago]
        maps = store.export("status:pending", "kind:map")
    except Exception:  # noqa: BLE001
        return {}

    def audience(t) -> str:
        if t.kind in ("grilling", "prototype") or guards.NEEDS_HUMAN_TAG in t.tags:
            return "yours"
        if t.kind in records.SESSION_TYPE_FOR_KIND and records.AFK_TAG in t.tags:
            return "agent"
        return "other"

    def ages(ts):
        values = [round((now - w).total_seconds() / 86400, 3)
                  for w in (_tw_when(t.entry) for t in ts) if w]
        stat = {"n": 0, "sum": 0.0, "min": None, "max": None}
        for v in values:
            stat = _fold(stat, round(v, 3))
        out = _render_stat(stat, values)
        out["oldest"] = [t.description[:60] for t in sorted(
            ts, key=lambda t: t.entry or "")[:3]]
        return out

    def resolve_days(ts):
        values, stat = [], {"n": 0, "sum": 0.0, "min": None, "max": None}
        for t in ts:
            start, end = _tw_when(t.entry), _tw_when(t.end)
            if start and end:
                v = round((end - start).total_seconds() / 86400, 3)
                values.append(v)
                stat = _fold(stat, v)
        return _render_stat(stat, values)

    def flow(ts_open, ts_done, days):
        since = now - timedelta(days=days)
        created = sum(1 for t in ts_open + ts_done
                      if (_tw_when(t.entry) or now - timedelta(days=999)) >= since)
        completed = sum(1 for t in ts_done
                        if (_tw_when(t.end) or now - timedelta(days=999)) >= since)
        return {"created": created, "completed": completed,
                "net": created - completed}

    out: dict[str, Any] = {}
    for name in ("agent", "yours"):
        open_ = [t for t in pending if audience(t) == name]
        closed = [t for t in done if audience(t) == name]
        out[name] = {"open": len(open_), "age_days": ages(open_),
                     "flow": {"d7": flow(open_, closed, 7),
                              "d30": flow(open_, closed, 30)},
                     "time_to_resolve_days_d30": resolve_days(closed)}
    blocked = [t for t in pending if t.uuid in blocked_ids]
    out["blocked"] = {"open": len(blocked), "age_days": ages(blocked)}

    children: dict[str, list] = {}
    for t in pending + done:
        if t.parent:
            children.setdefault(t.parent, []).append(t)
    out["maps"] = {}
    for m in maps:
        kids = children.get(m.uuid, [])
        open_kids = [k for k in kids if k.status == "pending"]
        total = len(kids)
        out["maps"][m.uuid[:8]] = {
            "title": m.description[:60], "project": m.project,
            "open": len(open_kids), "done_d30": total - len(open_kids),
            "frontier": sum(1 for k in open_kids
                            if k.uuid not in blocked_ids and not k.active)}
    out["health"] = {
        "moved": sum(1 for t in pending if any(
            a.startswith("moved:") for a in t.annotations)),
        "expired_d30": sum(1 for t in done if "expired" in t.tags),
        "claimed": sum(1 for t in pending if t.active),
    }
    return out


# -- the unit -------------------------------------------------------------


def unit(state_dir: Path | None, gate: dict, now: datetime | None = None) -> dict:
    """Blocked now and why; time blocked per gate and switched off, per
    window, swept from the tick log; ceiling pressure; liveness."""
    from pu import control

    now = now or datetime.now(timezone.utc)
    entries = list(reversed(control.ticks(state_dir, limit=control.MAX_LINES)))

    def state(reason: str) -> str:
        reason = reason or ""
        if reason.startswith("paused"):
            return "sessions_off"
        if reason.startswith("daily cost cap"):
            return "cost"
        if "concurrency ceiling" in reason:
            return "ceiling"
        if "window" in reason or "usage" in reason or "ceiling" in reason:
            return "usage"
        return "clear"

    def time_in(since: datetime) -> dict[str, float]:
        totals: dict[str, float] = {}
        for i, entry in enumerate(entries):
            start = _when(entry.get("at"))
            end = _when(entries[i + 1].get("at")) if i + 1 < len(entries) else now
            if start is None or end is None or end <= since:
                continue
            seconds = (end - max(start, since)).total_seconds()
            key = state(entry.get("reason"))
            if key != "clear":
                totals[key] = round(totals.get(key, 0) + seconds, 1)
        return totals

    beat = control.heartbeat(state_dir)
    began = _when(beat.get("since"))
    days = max(((now - began).total_seconds() / 86400) if began else 1, 1 / 24)
    return {
        "blocked": gate.get("blocked", False),
        "reason": gate.get("reason", ""),
        "blocked_seconds": {
            "today": time_in(now.replace(hour=0, minute=0, second=0, microsecond=0)),
            "d7": time_in(now - timedelta(days=7)),
            "d30": time_in(now - timedelta(days=30)),
        },
        "runway_active": bool(_runway_reason(state_dir)),
        "liveness": {"ticks": int(beat.get("ticks") or 0),
                     "ticks_per_day": round(int(beat.get("ticks") or 0) / days, 1),
                     "silences": int(beat.get("silences") or 0),
                     "counting_since": beat.get("since")},
    }


def _runway_reason(state_dir: Path | None) -> str | None:
    if state_dir is None:
        return None
    try:
        return json.loads((Path(state_dir) / "headroom.json").read_text(
            encoding="utf-8")).get("blocked_reason")
    except (OSError, ValueError):
        return None
