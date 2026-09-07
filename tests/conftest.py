"""Fixtures.

The suite runs against the **real** `task` binary wherever one is
reachable, and skips otherwise. That is deliberate: a fake runner cannot
catch an argv bug, and argv bugs are exactly what survives a fully green
suite built entirely on fakes.

On Linux (and CI) the binary is `task`. On this Windows host there is no
native build, so it is reached as `wsl -d Ubuntu -- task` -- set
`PU_TASK_CMD` and the suite picks it up unchanged.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid as uuidlib

import pytest

from pu import bodies, taskstore


def _resolve_base_cmd() -> tuple[str, ...] | None:
    """The command that reaches a working `task`, or None."""
    explicit = os.environ.get("PU_TASK_CMD")
    candidates = []
    if explicit:
        candidates.append(tuple(explicit.split()))
    if shutil.which("task"):
        candidates.append(("task",))
    if shutil.which("wsl"):
        candidates.append(("wsl", "-d", "Ubuntu", "--", "task"))

    for cmd in candidates:
        try:
            proc = subprocess.run(
                [*cmd, "--version"], capture_output=True, text=True, timeout=30
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode == 0 and proc.stdout.strip():
            return cmd
    return None


BASE_CMD = _resolve_base_cmd()

requires_task = pytest.mark.skipif(
    BASE_CMD is None, reason="no reachable `task` binary"
)


@pytest.fixture
def store():
    """A store over a scratch data location, isolated per test.

    The path is on the *binary's* side of the boundary, which is why it is
    a POSIX path built here rather than pytest's tmp_path -- when `task`
    runs through WSL, a Windows temp path is not addressable."""
    if BASE_CMD is None:
        pytest.skip("no reachable `task` binary")
    location = f"/tmp/pu-tests/{uuidlib.uuid4()}"
    return taskstore.TaskStore(data_location=location, base_cmd=BASE_CMD)


@pytest.fixture
def body_store(tmp_path):
    return bodies.BodyStore(tmp_path / "bodies")
