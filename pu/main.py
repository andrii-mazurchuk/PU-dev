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
from typing import Sequence

from pu import bodies, notify, pipeline, server, sessions, taskstore

# Matches this unit's registered base_url. Keeping the default equal to the
# registered port is deliberate: a sibling unit ships a main.py defaulting
# to a port its own units.yaml entry does not use, and nothing catches it.
DEFAULT_PORT = 9001

# The one directory this unit keeps private state under. The gateway
# injects HOLONIC_STATE_DIR and creates it before this process starts --
# see holonic-node/docs/UNIT_STANDARDS.md, "Private storage". PU_STATE_DIR
# predates the standard and still wins, so a deployment pinning it is
# untouched; the bare fallback is what lets pu run with no gateway at all.
STATE_DIR_ENV = "HOLONIC_STATE_DIR"
LEGACY_STATE_DIR_ENV = "PU_STATE_DIR"
DEFAULT_STATE_DIR = "state"

# The Taskwarrior store, inside that directory. It used to live at
# ~/.pu-taskdata -- outside the repo entirely, so nothing could back it
# up or mount it without knowing that pu in particular put it there.
TASKDATA_SUBDIR = "taskdata"
LEGACY_TASKDATA = "~/.pu-taskdata"


def default_state_dir(environ: dict[str, str] | None = None) -> str:
    """Resolved at call time, not import time: the gateway sets this in
    the process environment, so a default captured at import would be
    computed before it is visible and pu would come up healthy against an
    empty queue."""
    env = os.environ if environ is None else environ
    return env.get(STATE_DIR_ENV) or env.get(LEGACY_STATE_DIR_ENV) or DEFAULT_STATE_DIR


def _store_holds_data(directory: Path) -> bool:
    """Whether a Taskwarrior store has anything in it.

    Presence of files is not the test. `task` creates `pending.data` and
    `completed.data` empty on first contact, so a store it has merely
    touched looks populated to anything counting directory entries.
    """
    try:
        return any(f.is_file() and f.stat().st_size for f in directory.iterdir())
    except OSError:
        return False


def warn_if_legacy_taskdata_is_unmigrated(
    taskdata_dir: Path, base_cmd: Sequence[str] = ("task",)
) -> bool:
    """Say so, loudly, when the store may have moved out from under a
    queue that still has work in it.

    Nothing migrates automatically. A task store is not something to move
    on a unit's own initiative, and copying it wrong is worse than not
    copying it -- these files are read by a Linux binary, so anything that
    rewrites line endings on the way corrupts them silently.

    Staying quiet is the dangerous option: pu would start clean against an
    empty store and report itself perfectly healthy, and the only symptom
    would be a queue that used to have work in it and now does not.

    The old default was `~/.pu-taskdata`, and under the WSL arrangement
    that `~` was the *binary's* home, not this process's -- so from here
    the old store is on the far side of a filesystem boundary and cannot
    be checked without paying for a subprocess at boot. Rather than check
    the wrong home directory and report a confident "nothing to migrate",
    the WSL case says plainly that it cannot see, and where to look.
    """
    if _store_holds_data(taskdata_dir):
        return False

    through_wsl = bool(base_cmd) and Path(base_cmd[0]).stem.lower() == "wsl"
    if through_wsl:
        print(
            f"  NOTE: the task store is now {taskdata_dir}, and it is empty.\n"
            f"        It used to default to {LEGACY_TASKDATA} -- which, with "
            f"the binary behind\n"
            f"        WSL, means that path in the WSL user's home, not this "
            f"machine's. pu\n"
            f"        cannot see there from here. If that store still holds a "
            f"queue, stop pu\n"
            f"        and copy it across verbatim: a byte copy, nothing that "
            f"rewrites line\n"
            f"        endings, which the Taskwarrior binary reads as data.",
            flush=True,
        )
        return True

    legacy = Path(LEGACY_TASKDATA).expanduser()
    if not _store_holds_data(legacy):
        return False
    print(
        f"  WARNING: {legacy} still holds a task store and {taskdata_dir} is "
        f"empty.\n"
        f"           pu is starting against the empty one. To keep the old "
        f"queue, stop pu\n"
        f"           and copy the files across verbatim -- a byte copy, "
        f"nothing that rewrites\n"
        f"           line endings, which the Taskwarrior binary reads as "
        f"data.",
        flush=True,
    )
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="PU -- the processing unit")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PU_PORT", DEFAULT_PORT)))
    parser.add_argument(
        "--taskdata",
        default=os.environ.get("PU_TASKDATA"),
        help="Taskwarrior data location. Defaults to taskdata/ inside the "
             "state directory. When the binary is reached through WSL this "
             "is a path on the WSL side, not a Windows path -- the default "
             "is translated for you; an explicit value is not.",
    )
    parser.add_argument(
        "--state-dir",
        default=default_state_dir(),
        help="this unit's private storage; nothing outside this repo reads it",
    )
    parser.add_argument("--prompts-dir", default="prompts")
    args = parser.parse_args()

    unit_root = Path(__file__).resolve().parent.parent
    state = Path(args.state_dir)
    state.mkdir(parents=True, exist_ok=True)

    # The task store is the one part of this unit's state that is read by
    # a binary rather than by us, and under the WSL arrangement that
    # binary is on the other side of a filesystem boundary -- so the
    # default is resolved to a real local directory and then written the
    # way the binary will read it. On Linux, and in a container, the
    # translation is the identity and this is simply state/taskdata.
    base_cmd = taskstore.base_cmd_from_env()
    taskdata_dir = state / TASKDATA_SUBDIR
    taskdata_dir.mkdir(parents=True, exist_ok=True)
    taskdata = args.taskdata or taskstore.path_for_binary(taskdata_dir, base_cmd)
    warn_if_legacy_taskdata_is_unmigrated(taskdata_dir, base_cmd)

    store = taskstore.TaskStore(data_location=taskdata, base_cmd=base_cmd)
    body_store = bodies.BodyStore(state / "bodies")
    session_store = sessions.SessionStore(state / "sessions")

    def tick():
        return pipeline.tick(
            store, body_store, session_store,
            unit_root=unit_root,
            peers_path=unit_root / "peers.json",
            cost_policy_path=unit_root / "cost_policy.json",
            tracker_path=state / "repeat_tracker.json",
            # Absent until the gateway sets it, and absent is a working
            # state: sessions simply reach no peer tools, and nobody is
            # notified when a task is blocked.
            mcp_bridge_url=notify.bridge_url(),
        ).as_dict()

    # A session driving this store by hand runs bare `task`, which reads a
    # config file -- and this unit deliberately has none, passing its whole
    # schema as `rc.` overrides so nothing can drift. Without the UDAs
    # declared, `kind:` and `parent_uuid:` are simply rejected for anyone
    # but us. So the file is rendered here, from the same RC_SCHEMA the
    # overrides come from, and refreshed on every start.
    taskrc = state / "taskrc"
    try:
        taskrc.parent.mkdir(parents=True, exist_ok=True)
        # newline="\n" is not cosmetic. This file is written by whatever
        # platform runs the unit and read by the binary, which under the
        # WSL arrangement is Linux. Python would otherwise translate to
        # CRLF here, and Taskwarrior reads the trailing carriage return as
        # part of the value -- so `data.location=/tmp/x` silently becomes a
        # different directory and a session writes somewhere the unit
        # never looks.
        taskrc.write_text(
            taskstore.render_taskrc(taskdata),
            encoding="utf-8",
            newline="\n",
        )
    except OSError:
        taskrc = None  # degrade: our own writes never needed it

    # The store may live behind WSL, whose first call after idle pays to
    # boot the distro. Warm it off the request path.
    threading.Thread(target=store.warm, daemon=True).start()

    httpd = server.build_server(
        args.host, args.port, store, body_store, Path(args.prompts_dir),
        unit_root=unit_root, session_store=session_store, tick=tick,
    )
    # flush: stdout is a pipe whenever the gateway or a shell redirects it,
    # and a startup banner nobody sees is a path nobody can point a session
    # at.
    print(f"pu listening on http://{args.host}:{args.port} "
          f"(task: {' '.join(store.base_cmd)}, data: {taskdata})",
          flush=True)
    if taskrc:
        # Printed as the *binary* will read it. Under the WSL arrangement
        # that is not the path this process wrote to, and handing a session
        # the Windows one gives it a file it cannot open.
        readable = taskstore.path_for_binary(taskrc, store.base_cmd)
        print(f"  sessions: task rc:{readable} ...", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
