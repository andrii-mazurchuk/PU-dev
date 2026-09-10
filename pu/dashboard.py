"""The panel spec this unit answers `GET /dashboard` with.

The contract is `holonic-node/docs/UNIT_STANDARDS.md`, section
"Dashboards". The short version, and the two rules that shape everything
in this file:

- **The node owns the chrome.** This unit declares scopes and panels and
  draws no navigation of its own, ever. The topbar says which unit you
  are looking at, the rail says which of the pages below you are on, and
  neither belongs to us.
- **A `source` is a path on this unit.** Never a URL, never a peer. The
  node fetches it through the read-only proxy it already owns, so a spec
  cannot make the node call anywhere else.

**Why this is built rather than stored as a JSON file.** Two values in
it are not constants: the account-usage ceilings come from the
environment the gateway injected, and a meter's `ceiling` is a literal
in the spec rather than something read from the data. A static file
would carry 70 and 80 as hard-coded numbers and drift silently the day
a manifest changed one -- the panel would then draw a line the gate does
not enforce, which is worse than drawing no line.

**Read-only.** Every source here is a GET, and the one panel that causes
anything to happen -- `ask` -- does it by naming a tool in this unit's
own `/tools`, which the console dispatches through the bridge's
`POST /route` like any other tool call. There is no private endpoint
here that only the dashboard knows about.
"""

from __future__ import annotations

from typing import Any

UNIT = "pu"

# Columns shared by every table that lists tasks. One definition because
# a task looks the same wherever it is listed, and four copies of this
# would drift the first time a field was renamed.
TASK_COLUMNS: list[dict[str, str]] = [
    {"key": "description", "label": "Task"},
    {"key": "kind", "label": "Kind", "format": "pill"},
    {"key": "project", "label": "Project"},
    {"key": "urgency", "label": "Urgency"},
]

# Opening a task from any table that lists one. `params` binds a field of
# the clicked row to a page parameter; the detail page's sources then
# interpolate it. Substitution happens before the node re-validates the
# result, so a row field cannot smuggle a path in.
TASK_DETAIL: dict[str, Any] = {"page": "task", "params": {"uuid": "uuid"}}

RUN_COLUMNS: list[dict[str, str]] = [
    {"key": "recorded_at", "label": "When", "format": "time"},
    {"key": "session_type", "label": "Type", "format": "pill"},
    {"key": "outcome", "label": "Outcome", "format": "pill"},
    {"key": "cost_usd", "label": "Cost"},
    {"key": "duration_ms", "label": "ms"},
]

RUN_DETAIL: dict[str, Any] = {
    "page": "run",
    "params": {"uuid": "task_uuid", "run_id": "run_id"},
}


def _overview(ceilings: dict[str, float]) -> dict[str, Any]:
    return {
        "id": "overview",
        "title": "Overview",
        "panels": [
            {
                "kind": "kpis",
                "title": "Queue",
                "span": 12,
                "items": [
                    {"label": "Pending", "source": "stats:metrics.pending_tasks"},
                    {"label": "Runnable now", "source": "stats:metrics.runnable_now",
                     "sub": "afk, and a session type behind the kind"},
                    {"label": "Claimed", "source": "stats:metrics.claimed"},
                    {"label": "Blocked", "source": "stats:metrics.blocked"},
                    {"label": "Bodies", "source": "stats:metrics.bodies"},
                    {"label": "Spend, all time", "source": "stats:metrics.sessions.cost_usd_total"},
                ],
            },
            {
                # The panel that answers "why has nothing run since this
                # morning" -- the question this unit gets asked most, and
                # until now the one that needed a log to answer.
                "kind": "rows",
                "title": "Gates",
                "note": "what would stop a session starting right now",
                "span": 6,
                "source": "gate",
                "path": "summary",
                "columns": [
                    {"key": "key", "label": "Gate"},
                    {"key": "value", "label": "State"},
                    {"key": "note", "label": ""},
                ],
            },
            # Two panels rather than one because the windows have
            # separate ceilings and a meter panel carries a single
            # `ceiling` for all its rows. Drawing both against the higher
            # of the two would show the five-hour window as clear while
            # the gate was blocking on it.
            {
                "kind": "meters",
                "title": "5-hour window",
                "note": f"ceiling {ceilings.get('five_hour', 0):.0f}%",
                "span": 3,
                "source": "gate",
                "path": "five_hour",
                "x": "name",
                "y": "percent",
                "ceiling": ceilings.get("five_hour", 70.0),
            },
            {
                "kind": "meters",
                "title": "7-day window",
                "note": f"ceiling {ceilings.get('seven_day', 0):.0f}%",
                "span": 3,
                "source": "gate",
                "path": "seven_day",
                "x": "name",
                "y": "percent",
                "ceiling": ceilings.get("seven_day", 80.0),
            },
            {
                "kind": "table",
                "title": "Recent sessions",
                "span": 12,
                "source": "sessions?limit=8",
                "path": "sessions",
                "columns": RUN_COLUMNS,
                "detail": RUN_DETAIL,
            },
        ],
    }


QUEUE_PAGE: dict[str, Any] = {
    "id": "queue",
    "title": "Queue",
    "panels": [
        {
            "kind": "text",
            "span": 12,
            "body": (
                "These are not the same query, and the difference is the "
                "point. The frontier is what a person working a map sees: "
                "open, unblocked, unclaimed -- including the tickets only a "
                "human can resolve, because hiding those would make a map "
                "look finished when it is not. The queue is what pu may "
                "select for itself: strictly narrower, and default-deny on "
                "both halves -- an explicit afk tag, and a kind with a "
                "session type behind it."
            ),
        },
        {
            "kind": "table",
            "title": "Queue",
            "note": "what pu may select",
            "span": 6,
            "source": "queue",
            "path": "tasks",
            "columns": TASK_COLUMNS,
            "detail": TASK_DETAIL,
        },
        {
            "kind": "table",
            "title": "Frontier",
            "note": "what a person working a map sees",
            "span": 6,
            "source": "frontier",
            "path": "tasks",
            "columns": TASK_COLUMNS,
            "detail": TASK_DETAIL,
        },
    ],
}


TASKS_PAGE: dict[str, Any] = {
    "id": "tasks",
    "title": "Tasks",
    "panels": [
        {
            "kind": "table",
            "title": "Pending",
            "note": "every open task, whatever it came from",
            "span": 12,
            "source": "tasks",
            "path": "tasks",
            "columns": TASK_COLUMNS + [
                {"key": "tags", "label": "Tags"},
                {"key": "afk", "label": "afk"},
            ],
            "detail": TASK_DETAIL,
        },
    ],
}


# Hidden: reachable by drilling from any task table, absent from the rail.
# A detail page in the rail is a page you cannot open without a row to
# open it from.
TASK_PAGE: dict[str, Any] = {
    "id": "task",
    "title": "Task",
    "hidden": True,
    "panels": [
        {
            "kind": "rows",
            "title": "Task",
            "span": 12,
            "source": "panels/task/{uuid}",
            "path": "fields",
            "columns": [
                {"key": "key", "label": "Field"},
                {"key": "value", "label": "Value"},
                {"key": "note", "label": ""},
            ],
        },
        {
            "kind": "table",
            "title": "Children",
            "note": "tasks this one is the parent of",
            "span": 12,
            "source": "tasks?parent={uuid}",
            "path": "tasks",
            "columns": TASK_COLUMNS,
            "detail": TASK_DETAIL,
        },
        {
            "kind": "table",
            "title": "Runs against this task",
            "span": 12,
            "source": "sessions?task={uuid}",
            "path": "sessions",
            "columns": RUN_COLUMNS,
            "detail": RUN_DETAIL,
        },
    ],
}


MAPS_PAGE: dict[str, Any] = {
    "id": "maps",
    "title": "Maps",
    "panels": [
        {
            "kind": "table",
            "title": "Maps",
            "note": "a record with children, never itself worked",
            "span": 7,
            "source": "tasks?kind=map",
            "path": "tasks",
            "columns": [
                {"key": "description", "label": "Map"},
                {"key": "project", "label": "Project"},
                {"key": "urgency", "label": "Urgency"},
            ],
            "detail": TASK_DETAIL,
        },
        {
            "kind": "table",
            "title": "Projects",
            "note": "derived from open tasks; there is no project record",
            "span": 5,
            "source": "projects",
            "path": "projects",
            "columns": [
                {"key": "project", "label": "Project"},
                {"key": "open_tasks", "label": "Open"},
            ],
        },
    ],
}


SESSIONS_PAGE: dict[str, Any] = {
    "id": "sessions",
    "title": "Sessions",
    "panels": [
        {
            "kind": "kpis",
            "span": 12,
            "items": [
                {"label": "Runs", "source": "stats:metrics.sessions.runs"},
                {"label": "Total cost", "source": "stats:metrics.sessions.cost_usd_total"},
                {"label": "Last run", "source": "stats:metrics.sessions.last_run_at",
                 "format": "since"},
            ],
        },
        {
            "kind": "table",
            "title": "Runs",
            "span": 12,
            "source": "sessions?limit=50",
            "path": "sessions",
            "columns": [{"key": "task_uuid", "label": "Task"}] + RUN_COLUMNS,
            "detail": RUN_DETAIL,
        },
    ],
}


RUN_PAGE: dict[str, Any] = {
    "id": "run",
    "title": "Run",
    "hidden": True,
    "panels": [
        {
            "kind": "rows",
            "title": "Run",
            "span": 12,
            "source": "panels/run/{uuid}/{run_id}",
            "path": "fields",
            "columns": [
                {"key": "key", "label": "Field"},
                {"key": "value", "label": "Value"},
                {"key": "note", "label": ""},
            ],
        },
        {
            "kind": "table",
            "title": "Tool calls",
            "note": "what the session actually reached for",
            "span": 12,
            "source": "panels/run/{uuid}/{run_id}",
            "path": "tool_calls",
            "columns": [{"key": "tool", "label": "Tool"}],
        },
    ],
}


SPEND_PAGE: dict[str, Any] = {
    "id": "spend",
    "title": "Spend",
    "panels": [
        {
            "kind": "kpis",
            "span": 12,
            "items": [
                {"label": "Spent today", "source": "stats:metrics.gate.cost.spent_today_usd"},
                {"label": "Daily cap", "source": "stats:metrics.gate.cost.daily_cap_usd",
                 "sub": "unit-side; the gateway carries the policy and never reads it"},
                {"label": "Total", "source": "stats:metrics.sessions.cost_usd_total"},
            ],
        },
        {
            "kind": "bars",
            "title": "Cost per day",
            "note": "quiet days are present as zero, not omitted",
            "span": 12,
            "source": "spend",
            "path": "days",
            "x": "day",
            "y": "cost_usd",
        },
    ],
}


def spec(ceilings: dict[str, float]) -> dict[str, Any]:
    """The whole dashboard. `ceilings` is percentages, as `gate_state`
    reports them, so the meters draw the line the gate actually
    enforces."""
    return {
        "unit": UNIT,
        "title": "Processing unit",
        "lede": (
            "The task queue, and the sessions that work it. Every task here "
            "is the same record whatever it came from -- a wayfinder ticket, "
            "a mirrored issue, a message pushed at the inbox -- and ordering "
            "across all of them is one urgency calculation, not a ladder."
        ),
        "pages": [
            _overview(ceilings),
            QUEUE_PAGE,
            TASKS_PAGE,
            TASK_PAGE,
            MAPS_PAGE,
            SESSIONS_PAGE,
            RUN_PAGE,
            SPEND_PAGE,
        ],
    }


# -- presentation payloads for the two detail pages ----------------------
#
# These exist because no panel kind renders a field of a fetched object.
# `kpis` reads dotted paths out of /stats only, and `rows` needs an array
# -- so a detail view of one record has nowhere to put it. Rather than
# reshape the real API for the dashboard's convenience, the shaping lives
# here and is served under its own prefix, clearly marked as being for
# this and nothing else.
#
# If the node gains a way to read a field of a named source, both of
# these collapse to nothing and should be deleted rather than kept.


def _one_line(text: str, limit: int = 400) -> str:
    """A body flattened to fit a table cell.

    Nothing renders markdown here, so a 5KB wayfinder map in a `value`
    column would be a wall. The full text is a GET away at
    `tasks/<uuid>/body`, which is where anyone who wants it should go.
    """
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def task_fields(task: dict[str, Any]) -> dict[str, Any]:
    """One task as label/value/note rows."""
    body = task.get("body")
    fields = [
        {"key": "Description", "value": task.get("description") or "", "note": ""},
        {"key": "Kind", "value": task.get("kind") or "unset",
         "note": f"session type: {task.get('session_type') or 'none -- pu cannot run this'}"},
        {"key": "Status", "value": task.get("status") or "", "note": ""},
        {"key": "Project", "value": task.get("project") or "—", "note": ""},
        {"key": "Urgency", "value": str(task.get("urgency") or 0), "note": ""},
        {"key": "Runnable", "value": "yes" if task.get("runnable") else "no",
         "note": "afk tag, and a kind with a session type behind it"},
        {"key": "Claimed", "value": "yes" if task.get("active") else "no", "note": ""},
        {"key": "Tags", "value": ", ".join(task.get("tags") or []) or "—",
         "note": "tags decide which procedures are mandatory"},
        {"key": "Blocked by", "value": ", ".join(task.get("depends") or []) or "—",
         "note": ""},
        {"key": "Parent", "value": task.get("parent") or "—", "note": ""},
        {"key": "URL", "value": task.get("url") or "—",
         "note": "external authority; empty means this record is the authority"},
        {"key": "Repo", "value": task.get("repo") or "—", "note": ""},
        {"key": "uuid", "value": task.get("uuid") or "", "note": ""},
    ]
    if body:
        fields.append({
            "key": "Body",
            "value": _one_line(body),
            "note": "truncated; the whole of it is at tasks/<uuid>/body",
        })
    return {"fields": fields}


def run_fields(run: dict[str, Any]) -> dict[str, Any]:
    """One session run as label/value/note rows, plus its tool calls."""
    denials = run.get("permission_denials") or []
    fields = [
        {"key": "Recorded", "value": run.get("recorded_at") or "", "note": ""},
        {"key": "Session type", "value": run.get("session_type") or "", "note": ""},
        {"key": "Outcome", "value": run.get("outcome") or "", "note": run.get("detail") or ""},
        {"key": "Cost", "value": f"${float(run.get('cost_usd') or 0):.4f}", "note": ""},
        {"key": "Duration", "value": f"{int(run.get('duration_ms') or 0) / 1000:.1f}s", "note": ""},
        {"key": "Turns", "value": str(run.get("num_turns") or 0), "note": ""},
        {"key": "Exit code", "value": str(run.get("exit_code")),
         "note": "error" if run.get("is_error") else ""},
        {"key": "Models", "value": ", ".join(run.get("models_used") or []) or "—", "note": ""},
        {"key": "Permission denials", "value": str(len(denials)),
         "note": ", ".join(str(d) for d in denials[:5]) if denials else "none"},
        {"key": "Task", "value": run.get("task_uuid") or "", "note": ""},
        {"key": "Prompt", "value": _one_line(run.get("prompt") or ""),
         "note": "truncated; the whole of it is on disk with the raw stream"},
    ]
    return {
        "fields": fields,
        "tool_calls": [{"tool": str(name)} for name in (run.get("tool_calls") or [])],
    }
