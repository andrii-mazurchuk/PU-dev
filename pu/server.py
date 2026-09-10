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

from pu import ask, bodies, dashboard, docs, pipeline, records, service, sops, taskstore

UNIT_NAME = "pu"
PROMPT_TIERS = ("default", "reference", "insights")

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
        "name": "list_sops",
        "description": "The catalogue of procedures available as tags. Attaching one of these tags to a task makes that procedure mandatory for whoever works it. Consult this before creating a task so the tags you attach are real ones -- an invented tag carries no procedure.",
        "method": "GET",
        "path": "/sops",
        "input_schema": {"type": "object", "properties": {}},
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
        "name": "get_body",
        "description": "A task's long-form markdown body as raw text, not wrapped in JSON. This is what a session outside this unit reads to load a wayfinder map or a ticket's question: `curl -s <pu>/tasks/<uuid>/body > map.md`. Returns 404 when the task has no body.",
        "method": "GET",
        "path": "/tasks/{uuid}/body",
        "input_schema": {
            "type": "object",
            "properties": {"uuid": {"type": "string"}},
            "required": ["uuid"],
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
    {
        # Declared, and therefore callable by every peer and every MCP
        # client that can reach the bridge -- not only by the dashboard
        # panel that wanted it. That widening was decided on its own
        # merits; see DECISIONS.md.
        "name": "ask_unit",
        "description": "Ask this unit a question about its own queue, gates and recent sessions. Starts a session and returns at once with an id; poll GET /ask/{id} for the answer. Single-turn: each question carries its own context and knows nothing of the last. Spends money, and is refused rather than queued when the cost or account-usage gates are blocking.",
        "method": "POST",
        "path": "/ask",
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "a question about this unit's current state",
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "run_tick",
        "description": "Do at most one unit of work now: gate, select the most urgent runnable task, claim it, run a session against it, record the result. Spends money. The gate runs inside the tick, so there is no path in that routes around it; a blocked gate returns ran=false with the reason and changes no task's state.",
        "method": "POST",
        "path": "/trigger",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_docs",
        "description": "Index of this unit's own documentation: name, title, summary and size, so a caller can decide what not to fetch.",
        "method": "GET",
        "path": "/docs",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_doc",
        "description": "One of this unit's documents, as Markdown. Names come from list_docs.",
        "method": "GET",
        "path": "/docs/{name}",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
]


def make_handler(
    store: taskstore.TaskStore,
    body_store: bodies.BodyStore,
    prompts_dir: Path,
    unit_root: Path,
    session_store=None,
    tick=None,
    ask_store=None,
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

        def _text(self, status: int, text: str,
                  content_type: str = "text/plain; charset=utf-8") -> None:
            raw = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
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

        def _raw_if_text(self) -> str | None:
            """The request body as text when it was not sent as JSON.

            Returns None for a JSON request, which is what tells the
            handlers to parse it normally. Only `text/*` is accepted -- an
            absent or unrecognised content type falls through to JSON, so
            an existing caller cannot be silently reinterpreted."""
            content_type = (self.headers.get("Content-Type") or "").lower()
            if not content_type.startswith("text/"):
                return None
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return ""
            return self.rfile.read(length).decode("utf-8", errors="replace")

        def _gate(self) -> dict:
            """What the gates would decide right now. Degrades to an
            empty reading rather than taking /stats down with it: this is
            a diagnostic, and a diagnostic that can break the endpoint it
            is reported on is worse than an absent one."""
            if session_store is None:
                return {}
            try:
                return pipeline.gate_state(
                    session_store, unit_root, unit_root / "cost_policy.json"
                )
            except OSError:
                return {}

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
                    metrics = service.stats(store, body_store)
                    if session_store is not None:
                        metrics["sessions"] = session_store.aggregates()
                        metrics["gate"] = self._gate()
                    return self._json(200, {
                        "unit": UNIT_NAME,
                        "computed_at": datetime.now(timezone.utc).isoformat(),
                        "metrics": metrics,
                    })

                if parts == ["dashboard"]:
                    # The spec tier. Built per request rather than stored,
                    # so the meters carry the ceilings this process was
                    # actually given -- see dashboard.py.
                    ceilings = self._gate().get("ceilings", {})
                    return self._json(200, dashboard.spec(ceilings))

                if parts == ["gate"]:
                    return self._json(200, self._gate())

                if parts == ["spend"]:
                    if session_store is None:
                        return self._json(200, {"days": []})
                    days = int(one("days") or 14)
                    return self._json(200, {
                        "days": session_store.cost_by_day(min(max(days, 1), 90))
                    })

                if parts == ["sessions"]:
                    if session_store is None:
                        return self._json(200, {"sessions": []})
                    limit = int(one("limit") or 50)
                    runs = session_store.runs(limit=min(max(limit, 1), 500))
                    task = one("task")
                    if task:
                        runs = [r for r in runs if r.get("task_uuid") == task]
                    return self._json(200, {"sessions": runs})

                if len(parts) == 3 and parts[0] == "sessions":
                    if session_store is None:
                        return self._not_found()
                    found = session_store.run(parts[1], parts[2])
                    return self._json(200, found) if found else self._not_found()

                # Presentation payloads for the dashboard's two detail
                # pages. Under their own prefix because that is what they
                # are -- no panel kind can render a field of a fetched
                # object, so the shaping has to happen somewhere, and
                # reshaping the real API for it would have been worse.
                if len(parts) == 3 and parts[:2] == ["panels", "task"]:
                    found = service.get_task(store, body_store, parts[2])
                    if found is None:
                        return self._not_found()
                    return self._json(200, dashboard.task_fields(found))

                if len(parts) == 2 and parts[0] == "ask":
                    # The poll half of the ask panel. A GET, so it comes
                    # through the node's read proxy like every other
                    # source; the submit half is a tool call.
                    if ask_store is None:
                        return self._not_found()
                    found = ask_store.read(parts[1])
                    return self._json(200, found) if found else self._not_found()

                if len(parts) == 4 and parts[:2] == ["panels", "run"]:
                    if session_store is None:
                        return self._not_found()
                    found = session_store.run(parts[2], parts[3])
                    if found is None:
                        return self._not_found()
                    return self._json(200, dashboard.run_fields(found))

                if parts == ["tools"]:
                    return self._json(200, {"unit": UNIT_NAME, "tools": TOOLS})

                if parts == ["docs"]:
                    return self._json(
                        200, {"unit": UNIT_NAME, "docs": docs.build_index()}
                    )

                if len(parts) == 2 and parts[0] == "docs":
                    # Looked up in the derived index, never joined onto a
                    # path -- so "../../units.yaml" is simply not a key.
                    text = docs.read_doc(parts[1])
                    if text is None:
                        return self._not_found()
                    return self._text(200, text, "text/markdown; charset=utf-8")

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

                if len(parts) == 3 and parts[0] == "tasks" and parts[2] == "body":
                    # Raw markdown, not JSON. A caller outside this unit
                    # reads a map with one curl and no jq -- which is not
                    # a nicety: jq is not present by default on Windows,
                    # and piping JSON through a shell to extract a field
                    # is where the quoting bugs live.
                    body = body_store.get(parts[1])
                    if body is None:
                        return self._not_found()
                    return self._text(200, body, "text/markdown; charset=utf-8")

                if parts == ["sops"]:
                    return self._json(200, {
                        "sops": sops.catalogue(unit_root),
                        "broken": sops.validate(unit_root),
                    })

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
                # A body posted as raw markdown skips JSON entirely, so a
                # 5KB map with quotes and backticks in it needs no escaping
                # from whoever is sending it:
                #   curl -X POST --data-binary @map.md                 #        -H "Content-Type: text/markdown" <pu>/tasks/<id>/body
                raw_body = self._raw_if_text()
                payload = {} if raw_body is not None else self._body()

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

                if parts == ["trigger"]:
                    # The gateway pokes an already-running unit here rather
                    # than restarting it. One tick, synchronously: the cost
                    # gate runs inside it, so there is no path in that can
                    # route around the gate.
                    if tick is None:
                        return self._json(200, {"ran": False,
                                                "reason": "no pipeline wired"})
                    return self._json(200, tick())

                if parts == ["ask"]:
                    if ask_store is None or session_store is None:
                        return self._json(503, {"error": "no session layer wired"})
                    gate = self._gate()
                    ask_id = ask.submit(
                        ask_store,
                        question=payload.get("question") or "",
                        context=ask.build_context(
                            gate=gate,
                            stats=service.stats(store, body_store),
                            queue=service.queue(store),
                            frontier=service.frontier(store),
                            projects=service.list_projects(store),
                            runs=session_store.runs(limit=10),
                        ),
                        instructions=(unit_root / "session_types" / "ask"
                                      / "CLAUDE.md").read_text(encoding="utf-8"),
                        cwd=unit_root / "session_types" / "ask",
                        # Blocked does not queue and does not raise: the
                        # record is written already in error, and the poll
                        # reports it. One submit shape, whatever happened.
                        gate_reason=gate.get("reason", ""),
                    )
                    return self._json(202, {"id": ask_id})

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
                            store, body_store, uuid,
                            raw_body if raw_body is not None
                            else (payload.get("body") or ""),
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
    unit_root: Path | None = None,
    session_store=None,
    tick=None,
    ask_store=None,
) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(
        (host, port),
        make_handler(
            store, body_store, prompts_dir,
            Path(unit_root) if unit_root else Path("."),
            session_store, tick, ask_store,
        ),
    )
