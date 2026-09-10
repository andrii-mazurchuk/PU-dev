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

**Nothing in here is computed, and that is the point.** An earlier draft
built the meters' ceilings into the spec from the environment, because a
meter carried one ceiling for a whole panel and the two account-usage
windows have different ones. `ceiling_field` removed that -- the ceiling
now travels with the row it belongs to, read from `/gate` like the
reading beside it -- so the spec went back to being a description of what
to show rather than a snapshot of what was true when it was asked for.
It should stay that way: a value that has to be current belongs in the
data a panel fetches, never in the spec that names it.

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


def _overview() -> dict[str, Any]:
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
            {
                # One panel, two rows, a ceiling each. The windows are
                # separate limits rather than an average, and a row drawn
                # against the other's ceiling reads clear while the gate
                # is blocking on it.
                "kind": "meters",
                "title": "Account usage",
                "note": "each window against its own ceiling",
                "span": 6,
                "source": "gate",
                "path": "windows",
                "x": "name",
                "y": "utilization",
                "ceiling_field": "ceiling",
                "format": "percent",
            },
            {
                # Submit is a tool call the node dispatches through the
                # bridge; the poll is a GET through the read proxy. Two
                # doors that already exist, no third.
                "kind": "ask",
                "title": "Ask pu",
                "note": "each question starts a session and spends",
                "span": 12,
                "tool": "ask_unit",
                "poll": "ask/{id}",
                "placeholder": "How is the queue looking? Why has nothing run today?",
                "timeout_seconds": 180,
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
            "kind": "record",
            "title": "Task",
            "span": 6,
            "source": "tasks/{uuid}",
            "columns": [
                {"key": "description", "label": "Description"},
                {"key": "kind", "label": "Kind", "format": "pill"},
                {"key": "session_type", "label": "Session type"},
                {"key": "status", "label": "Status"},
                {"key": "project", "label": "Project"},
                {"key": "urgency", "label": "Urgency"},
                {"key": "runnable", "label": "Runnable"},
                {"key": "active", "label": "Claimed"},
                {"key": "afk", "label": "afk"},
                {"key": "tags", "label": "Tags"},
                {"key": "depends", "label": "Blocked by"},
                {"key": "parent", "label": "Parent"},
                {"key": "url", "label": "URL"},
                {"key": "repo", "label": "Repo"},
                {"key": "uuid", "label": "uuid"},
            ],
        },
        {
            # The body is the content, for a wayfinder map especially.
            # Served as text/markdown and rendered as escaped
            # preformatted text -- never parsed, because it was written
            # by whoever could reach this unit.
            "kind": "text",
            "title": "Body",
            "note": "as written; not rendered as markup",
            "span": 6,
            "source": "tasks/{uuid}/body",
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
            "kind": "record",
            "title": "Run",
            "span": 6,
            "source": "sessions/{uuid}/{run_id}",
            "columns": [
                {"key": "recorded_at", "label": "When", "format": "time"},
                {"key": "session_type", "label": "Session type", "format": "pill"},
                {"key": "outcome", "label": "Outcome", "format": "pill"},
                {"key": "detail", "label": "Detail"},
                {"key": "cost_usd", "label": "Cost"},
                {"key": "duration_ms", "label": "Duration, ms"},
                {"key": "num_turns", "label": "Turns"},
                {"key": "exit_code", "label": "Exit code"},
                {"key": "models_used", "label": "Models"},
                # The ordered sequence, not counts -- which is the
                # question the field exists to answer.
                {"key": "tool_calls", "label": "Tool calls"},
                {"key": "permission_denials", "label": "Denied"},
                {"key": "task_uuid", "label": "Task"},
            ],
        },
        {
            "kind": "text",
            "title": "Prompt",
            "note": "what this session was actually given",
            "span": 6,
            "source": "sessions/{uuid}/{run_id}",
            "field": "prompt",
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


def spec() -> dict[str, Any]:
    """The whole dashboard.

    A fresh dict each call rather than a module constant: this is handed
    straight to a JSON encoder, and a shared mutable structure that
    everything serialises is one edit away from a bug nobody looks for.
    """
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
            _overview(),
            QUEUE_PAGE,
            TASKS_PAGE,
            TASK_PAGE,
            MAPS_PAGE,
            SESSIONS_PAGE,
            RUN_PAGE,
            SPEND_PAGE,
        ],
    }
