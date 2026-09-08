#!/usr/bin/env python3
"""End-to-end smoke check against a running pu.

    python3 scripts/smoke.py [--base-url http://127.0.0.1:9001]

Read-mostly, but it does write: it pushes one inbox message and creates a
throwaway map and ticket, then reports what it saw. Point it at a scratch
`--taskdata`, not at a store you care about.

This exercises the HTTP surface a peer actually sees. It does **not** spend
a real session -- that costs money and is a separate, deliberate step:

    curl -X POST http://127.0.0.1:9001/trigger
"""

import argparse
import json
import sys
import urllib.error
import urllib.request

PASS, FAIL = "  ok  ", " FAIL "


def call(base, path, payload=None):
    url = base.rstrip("/") + path
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={"Content-Type": "application/json"} if payload is not None else {},
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read().decode("utf-8")
        if "json" in response.headers.get("Content-Type", ""):
            return response.status, json.loads(raw)
        return response.status, raw


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:9001")
    args = parser.parse_args()
    base = args.base_url

    failures = 0

    def check(label, condition, detail=""):
        nonlocal failures
        if not condition:
            failures += 1
        print(f"[{PASS if condition else FAIL}] {label}" + (f"  {detail}" if detail else ""))

    try:
        _, health = call(base, "/health")
    except (urllib.error.URLError, OSError) as exc:
        print(f"[{FAIL}] cannot reach {base}: {exc}")
        print("\nStart it with:  python -m pu.main --taskdata /tmp/pu-smoke")
        return 1

    check("health", health == {"status": "ok"}, str(health))

    _, tools = call(base, "/tools")
    names = {t["name"] for t in tools["tools"]}
    check("tools manifest", tools["unit"] == "pu" and names, f"{len(names)} tools")
    check("no /health or /tools in the manifest",
          not {t["path"] for t in tools["tools"]} & {"/health", "/tools"})

    _, stats = call(base, "/stats")
    check("stats envelope",
          set(stats) >= {"unit", "computed_at", "metrics"},
          f"pending={stats['metrics'].get('pending_tasks')}")

    prompt_status, prompt = call(base, "/prompts/default")
    check("prompts/default is text", prompt_status == 200 and isinstance(prompt, str))

    _, catalogue = call(base, "/sops")
    check("sop catalogue readable",
          "sops" in catalogue and not catalogue.get("broken"),
          f"{len(catalogue.get('sops', []))} sops, broken={catalogue.get('broken')}")

    # -- the wayfinding door
    _, created = call(base, "/maps", {"destination": "smoke check map",
                                      "project": "smoke",
                                      "body": "## Destination\n\nsmoke\n"})
    map_uuid = created["uuid"]
    check("create a map", bool(map_uuid), map_uuid)

    _, hitl = call(base, "/tickets", {"parent": map_uuid, "title": "talk it over",
                                      "kind": "grilling", "afk": True})
    _, afk = call(base, "/tickets", {"parent": map_uuid, "title": "find the fact",
                                     "kind": "research", "afk": True})

    _, frontier = call(base, f"/frontier?parent={map_uuid}")
    front_uuids = {t["uuid"] for t in frontier["tasks"]}
    check("frontier shows the human-in-the-loop ticket",
          hitl["uuid"] in front_uuids)

    _, queue = call(base, "/queue")
    queue_uuids = {t["uuid"] for t in queue["tasks"]}
    check("queue never offers a grilling ticket to an agent",
          hitl["uuid"] not in queue_uuids)
    check("queue does offer the research ticket", afk["uuid"] in queue_uuids)

    # -- the inbox door
    _, accepted = call(base, "/inbox", {"source": "smoke",
                                        "message": "have a look at the thing"})
    _, intake_task = call(base, f"/tasks/{accepted['uuid']}")
    check("inbox mints exactly one intake record",
          intake_task["kind"] == "intake" and intake_task["runnable"] is True)

    # -- the refusals
    try:
        call(base, "/tickets", {"parent": map_uuid, "title": "x", "kind": "nonsense"})
        check("an unknown kind is refused", False, "it was accepted")
    except urllib.error.HTTPError as exc:
        check("an unknown kind is refused", exc.code == 400, f"HTTP {exc.code}")

    print()
    if failures:
        print(f"{failures} check(s) failed.")
        return 1
    print("all checks passed.")
    print(f"\nLeft behind in the store: map {map_uuid} and its tickets, "
          f"plus intake record {accepted['uuid']}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
