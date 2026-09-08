"""Entrypoint. `python -m pu.main`.

Config comes from `os.environ`, which the gateway assembles from
`units.yaml` and injects at process start. There is deliberately no
config-file convention here.
"""

from __future__ import annotations

import argparse
import os
import threading
from pathlib import Path

from pu import bodies, pipeline, server, sessions, taskstore

# Matches this unit's registered base_url. Keeping the default equal to the
# registered port is deliberate: a sibling unit ships a main.py defaulting
# to a port its own units.yaml entry does not use, and nothing catches it.
DEFAULT_PORT = 9001


def main() -> None:
    parser = argparse.ArgumentParser(description="PU -- the processing unit")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PU_PORT", DEFAULT_PORT)))
    parser.add_argument(
        "--taskdata",
        default=os.environ.get("PU_TASKDATA", "~/.pu-taskdata"),
        help="Taskwarrior data location. When the binary is reached through "
             "WSL this is a path on the WSL side, not a Windows path.",
    )
    parser.add_argument(
        "--state-dir",
        default=os.environ.get("PU_STATE_DIR", "state"),
        help="this unit's private storage; nothing outside this repo reads it",
    )
    parser.add_argument("--prompts-dir", default="prompts")
    args = parser.parse_args()

    unit_root = Path(__file__).resolve().parent.parent
    state = Path(args.state_dir)

    store = taskstore.TaskStore(data_location=args.taskdata)
    body_store = bodies.BodyStore(state / "bodies")
    session_store = sessions.SessionStore(state / "sessions")

    def tick():
        return pipeline.tick(
            store, body_store, session_store,
            unit_root=unit_root,
            peers_path=unit_root / "peers.json",
            cost_policy_path=unit_root / "cost_policy.json",
            tracker_path=state / "repeat_tracker.json",
        ).as_dict()

    # The store may live behind WSL, whose first call after idle pays to
    # boot the distro. Warm it off the request path.
    threading.Thread(target=store.warm, daemon=True).start()

    httpd = server.build_server(
        args.host, args.port, store, body_store, Path(args.prompts_dir),
        unit_root=unit_root, session_store=session_store, tick=tick,
    )
    print(f"pu listening on http://{args.host}:{args.port} "
          f"(task: {' '.join(store.base_cmd)}, data: {args.taskdata})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
