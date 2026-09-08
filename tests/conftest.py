"""Fixtures.

The suite runs against the **real** `task` binary wherever one is
reachable, and skips otherwise. That is deliberate: a fake runner cannot
catch an argv bug, and argv bugs are exactly what survives a fully green
suite built entirely on fakes.

On Linux (and CI) the binary is plain `task` and nothing needs setting. On
a Windows host there is no native build, so it is reached through WSL. The
suite works either way with no configuration: it probes, in order,
`PU_TASK_CMD`, then `task` on PATH, then WSL (`PU_WSL_DISTRO`, default
`Ubuntu`).
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
        distro = os.environ.get("PU_WSL_DISTRO", "Ubuntu")
        # `-e`, never `--`: the latter goes through a login shell that
        # expands the arguments. See taskstore.normalise_base_cmd.
        candidates.append(("wsl", "-d", distro, "-e", "task"))

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
