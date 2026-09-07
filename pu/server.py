"""Routing and the tool manifest. The only module that knows HTTP.

The four standard endpoints are reimplemented here rather than inherited
from anywhere. That duplication across units is a standing decision, not an
oversight -- do not factor it out.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from pu import bodies, records, service, taskstore

UNIT_NAME = "pu"
PROMPT_TIERS = ("default", "reference")

# Hand-written, never generated from the routing table: every entry needs a
# description written for a model to read. Excludes /health and /tools.
#
# Names are action-style rather than path echoes -- one route served under
# two methods is two tools.
TOOLS = [
    {
        "name": "get_stats",
        "description": "Mechanical counts for this unit's task queue: pending tasks, a breakdown by kind, how many are runnable right now, how many are claimed or blocked.",
        "method": "GET",
        "path": "/stats",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "query_tasks",
        "description": "List tasks. Every task in this unit is the same universal record whatever its source -- a wayfinder decision ticket, a mirrored GitHub issue, an inbox message. Filter by project (matches nested projects by prefix), kind, status, or parent.",
        "method": "GET",
        "path": "/tasks",
        "input_schema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "dotted project scope; matches nested scopes by prefix"},
                "kind": {"type": "string", "enum": list(records.KINDS)},
                "status": {"type": "string", "description": "pending (default), completed, deleted, waiting"},
                "parent": {"type": "string", "description": "uuid of a map, to list only its children"},
            },
        },
    },
    {
        "name": "get_task",
        "description": "One task by uuid, including its body if it has one. Tasks are addressed by uuid only -- Taskwarrior renumbers the short id as work completes, so an id held across two calls addresses a different task.",
        "method": "GET",
        "path": "/tasks/{uuid}",
        "input_schema": {
            "type": "object",
            "properties": {"uuid": {"type": "string"}},
            "required": ["uuid"],
        },
    },
    {
        "name": "list_projects",
        "description": "Every project that has open work, with its open task count. Derived from the tasks themselves; there is no separate project record.",
        "method": "GET",
        "path": "/projects",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_frontier",
        "description": "The frontier: open, unblocked, unclaimed work, most urgent first, scoped to one map's children when parent is given. Includes human-in-the-loop tickets, because whoever is working the map needs to see them. Use this when charting or working a map.",
        "method": "GET",
        "path": "/frontier",
        "input_schema": {
            "type": "object",
            "properties": {"parent": {"type": "string", "description": "uuid of a map"}},
        },
    },
    {
        "name": "get_queue",
        "description": "What this unit may run on its own: the frontier narrowed to work explicitly marked agent-runnable whose kind has a session type. Strictly narrower than the frontier -- a grilling or prototype ticket never appears here.",
        "method": "GET",
        "path": "/queue",
        "input_schema": {
            "type": "object",
            "properties": {"parent": {"type": "string", "description": "uuid of a map"}},
        },
    },
    {
        "name": "create_map",
        "description": "Create a wayfinder map: a record that holds a destination and has child tickets, and is never itself worked. Its body is the map markdown (Destination, Notes, Decisions so far, Not yet specified, Out of scope).",
        "method": "POST",
        "path": "/maps",
        "input_schema": {
            "type": "object",
            "properties": {
                "destination": {"type": "string", "description": "what reaching the end of this map looks like; becomes the map's title"},
                "project": {"type": "string"},
                "body": {"type": "string", "description": "the map markdown"},
            },
            "required": ["destination"],
        },
    },
    {
        "name": "create_ticket",
        "description": "Create a child ticket on a map. kind is one of research, prototype, grilling, task, execution. afk defaults to false and must be set explicitly for an agent to be allowed to run it -- grilling and prototype are human-in-the-loop and must never be marked afk.",
        "method": "POST",
        "path": "/tickets",
        "input_schema": {
            "type": "object",
            "properties": {
                "parent": {"type": "string", "description": "uuid of the map"},
                "title": {"type": "string"},
                "kind": {"type": "string", "enum": list(records.KINDS)},
                "body": {"type": "string", "description": "the question this ticket resolves"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "each tag selects a mandatory SOP by exact name"},
                "afk": {"type": "boolean", "description": "may an agent resolve this alone"},
                "project": {"type": "string"},
            },
            "required": ["parent", "title", "kind"],
        },
    },
    {
        "name": "wire_blocking",
        "description": "Declare that a task is blocked by others. Blocking is native, so a blocked task drops out of every frontier query until its blockers close.",
        "method": "POST",
        "path": "/tasks/{uuid}/blocking",
        "input_schema": {
            "type": "object",
            "properties": {
                "uuid": {"type": "string"},
                "blocked_by": {"type": "array", "items": {"type": "string"}, "description": "uuids that must close first"},
            },
            "required": ["uuid", "blocked_by"],
        },
    },
    {
        "name": "claim_task",
        "description": "Claim a task before working it, so a concurrent session skips it. This should be a session's first write, before any work.",
        "method": "POST",
        "path": "/tasks/{uuid}/claim",
        "input_schema": {
            "type": "object",
            "properties": {"uuid": {"type": "string"}},
            "required": ["uuid"],
        },
    },
    {
        "name": "resolve_task",
        "description": "Record a task's answer and close it. Does not touch the parent map's Decisions-so-far: choosing the one-line gist for that is judgement, and the caller writes it with set_body.",
        "method": "POST",
        "path": "/tasks/{uuid}/resolve",
        "input_schema": {
            "type": "object",
            "properties": {
                "uuid": {"type": "string"},
                "answer": {"type": "string", "description": "the resolution, in full"},
            },
            "required": ["uuid", "answer"],
        },
    },
    {
        "name": "set_body",
        "description": "Replace a task's long-form markdown body. Used to rewrite a map after a decision lands.",
        "method": "POST",
        "path": "/tasks/{uuid}/body",
        "input_schema": {
            "type": "object",
            "properties": {
                "uuid": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["uuid", "body"],
        },
    },
    {
        "name": "push_inbox_message",
        "description": "Push work at this unit for consideration. This does not create a task to be done -- it creates one intake record, which a session later converts into real work or bounces with what was missing. It is the only write available to a peer that is not a wayfinding operation.",
        "method": "POST",
        "path": "/inbox",
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "who is asking"},
                "message": {"type": "string", "description": "the request, as worded"},
                "project": {"type": "string"},
            },
            "required": ["source", "message"],
        },
    },
]


def make_handler(
    store: taskstore.TaskStore,
    body_store: bodies.BodyStore,
    prompts_dir: Path,
):
    class Handler(BaseHTTPRequestHandler):
        server_version = "pu/0.1"

        # BaseHTTPRequestHandler logs every request to stderr by default.
        def log_message(self, fmt, *args):  # noqa: A003
            pass

        # -- plumbing -------------------------------------------------

        def _json(self, status: int, payload) -> None:
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _text(self, status: int, text: str) -> None:
            raw = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _not_found(self) -> None:
            self._json(404, {"error": "not found"})

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            try:
                parsed = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                raise service.ServiceError("body is not valid JSON")
            if not isinstance(parsed, dict):
                raise service.ServiceError("body must be a JSON object")
            return parsed

        def _path_parts(self) -> tuple[list[str], dict[str, list[str]]]:
            parsed = urlparse(self.path)
            # Percent-decode every segment. A uuid is safe, but a project
            # scope is not, and a path that is not decoded silently 404s
            # for every identifier containing a reserved character.
            parts = [unquote(p) for p in parsed.path.strip("/").split("/") if p]
            return parts, parse_qs(parsed.query)

        # -- routing --------------------------------------------------

        def do_GET(self):  # noqa: N802
            parts, query = self._path_parts()
            one = lambda k: (query.get(k) or [None])[0]  # noqa: E731

            try:
                if parts == ["health"]:
                    return self._json(200, {"status": "ok"})

                if parts == ["stats"]:
                    return self._json(200, {
                        "unit": UNIT_NAME,
                        "computed_at": datetime.now(timezone.utc).isoformat(),
                        "metrics": service.stats(store, body_store),
                    })

                if parts == ["tools"]:
                    return self._json(200, {"unit": UNIT_NAME, "tools": TOOLS})

                if len(parts) == 2 and parts[0] == "prompts":
                    tier = parts[1]
                    if tier not in PROMPT_TIERS:
                        return self._not_found()
                    try:
                        return self._text(
                            200, (prompts_dir / f"{tier}.md").read_text(encoding="utf-8")
                        )
                    except OSError:
                        return self._not_found()

                if parts == ["tasks"]:
                    return self._json(200, {"tasks": service.list_tasks(
                        store,
                        project=one("project"),
                        kind=one("kind"),
                        status=one("status") or "pending",
                        parent=one("parent"),
                    )})

                if len(parts) == 2 and parts[0] == "tasks":
                    found = service.get_task(store, body_store, parts[1])
                    return self._json(200, found) if found else self._not_found()

                if parts == ["projects"]:
                    return self._json(200, {"projects": service.list_projects(store)})

                if parts == ["frontier"]:
                    return self._json(200, {
                        "tasks": service.frontier(store, parent=one("parent"))
                    })

                if parts == ["queue"]:
                    return self._json(200, {
                        "tasks": service.queue(store, parent=one("parent"))
                    })

                return self._not_found()

            except (service.ServiceError, records.RecordError) as exc:
                return self._json(400, {"error": str(exc)})
            except taskstore.TaskError as exc:
                return self._json(502, {"error": str(exc)})

        def do_POST(self):  # noqa: N802
            parts, _ = self._path_parts()

            try:
                payload = self._body()

                if parts == ["maps"]:
                    return self._json(201, service.create_map(
                        store, body_store,
                        destination=payload.get("destination"),
                        project=payload.get("project"),
                        body=payload.get("body"),
                    ))

                if parts == ["tickets"]:
                    return self._json(201, service.create_ticket(
                        store, body_store,
                        parent=payload.get("parent"),
                        title=payload.get("title"),
                        kind=payload.get("kind"),
                        body=payload.get("body"),
                        tags=payload.get("tags") or (),
                        afk=bool(payload.get("afk")),
                        project=payload.get("project"),
                    ))

                if parts == ["inbox"]:
                    return self._json(202, service.push_inbox(
                        store, body_store,
                        source=payload.get("source"),
                        message=payload.get("message"),
                        project=payload.get("project"),
                    ))

                if len(parts) == 3 and parts[0] == "tasks":
                    uuid, action = parts[1], parts[2]
                    if action == "blocking":
                        return self._json(200, service.wire_blocking(
                            store, uuid, payload.get("blocked_by") or ()
                        ))
                    if action == "claim":
                        return self._json(200, service.claim(store, uuid))
                    if action == "resolve":
                        return self._json(200, service.resolve(
                            store, uuid, payload.get("answer")
                        ))
                    if action == "body":
                        return self._json(200, service.set_body(
                            store, body_store, uuid, payload.get("body") or ""
                        ))

                return self._not_found()

            except (service.ServiceError, records.RecordError, bodies.BodyError) as exc:
                return self._json(400, {"error": str(exc)})
            except taskstore.TaskError as exc:
                return self._json(502, {"error": str(exc)})

    return Handler


def build_server(
    host: str,
    port: int,
    store: taskstore.TaskStore,
    body_store: bodies.BodyStore,
    prompts_dir: Path,
) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(
        (host, port), make_handler(store, body_store, prompts_dir)
    )
