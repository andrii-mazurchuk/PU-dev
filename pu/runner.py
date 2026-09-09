"""The `claude -p` invocation, and parsing what came back.

Injectable, so the suite never spends a real API call or real wall-clock
time on it -- but note the seam is the *subprocess*, not the argv
construction. Argv is built here and asserted on in tests, because a fake
that also builds the argv would test nothing.

Every session runs `--output-format stream-json --verbose`. That is not
optional in two senses: the CLI refuses `--print --output-format
stream-json` without `--verbose`, and the stream is the only place cost,
duration, the ordered tool-call sequence and permission denials are
reported at all.

**Nothing multi-line ever goes in argv.** On Windows the CLI is an npm
batch shim, and a command line containing a newline is truncated there --
silently, at the newline. Measured: a prompt passed as an argument reached
the model as its first line only, *and* took the trailing
`--output-format stream-json --verbose` down with it, so the session both
misunderstood its task and reported in plain text. The prompt goes on
stdin, and session-type instructions are folded into it rather than passed
as `--append-system-prompt`, which would have exactly the same problem.

That leaves argv holding only short single-line flags, which is the shape
that survives every launcher.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Sequence

from pu import usage_gate


def claude_command(environ: dict[str, str] | None = None) -> tuple[str, ...]:
    """The command that actually launches Claude Code on this machine.

    Resolved rather than assumed. On Windows the CLI is an npm shim -- the
    real file is `claude.cmd`, and `CreateProcess` cannot run a bare
    extensionless name, so a plain `["claude", ...]` fails with "the system
    cannot find the file specified". `shutil.which` applies `PATHEXT` and
    returns the name that will actually start.

    `PU_CLAUDE_CMD` overrides, for the same reason `PU_TASK_CMD` exists:
    where a binary lives is deployment config, not an assumption to bake
    into the code."""
    env = os.environ if environ is None else environ
    override = env.get("PU_CLAUDE_CMD", "").strip()
    if override:
        return tuple(shlex.split(override))
    return (shutil.which("claude") or "claude",)


@dataclasses.dataclass(frozen=True)
class SessionResult:
    """What one invocation did.

    `exit_code` always comes from the real process, never inferred from
    the stream's own `is_error`: a crash before any valid JSON is emitted
    must still produce a real non-zero code with everything else absent.

    Every other field degrades to None when the stream never produced a
    final result line -- a process killed mid-stream is missing data, not
    an error.

    `tool_calls` is the ordered *sequence* of names, not counts. A consumer
    derives counts in one line; aggregating here would permanently lose
    the order, which is the question the field exists to answer."""

    exit_code: int
    text: str = ""
    cost_usd: float | None = None
    duration_ms: int | None = None
    num_turns: int | None = None
    is_error: bool | None = None
    tool_calls: tuple[str, ...] = ()
    models_used: tuple[str, ...] = ()
    permission_denials: tuple[str, ...] = ()
    # What this session saw of the account's rolling usage windows, if it
    # reported them. Empty when the CLI emitted no rate_limit_event --
    # which is normal, and means the previous reading stands.
    rate_limit_windows: dict = dataclasses.field(default_factory=dict)
    raw_stream: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.is_error


@dataclasses.dataclass(frozen=True)
class CompletedRun:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[Sequence[str], "Path | None", str], CompletedRun]


def subprocess_runner(argv: Sequence[str], cwd: Path | None, stdin: str) -> CompletedRun:
    proc = subprocess.run(
        list(argv),
        cwd=str(cwd) if cwd else None,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return CompletedRun(proc.returncode, proc.stdout or "", proc.stderr or "")


def build_argv(
    allowed_tools: str | None = None,
    model: str | None = None,
    mcp_bridge_url: str | None = None,
    command: Sequence[str] = ("claude",),
) -> list[str]:
    """The command, exactly.

    `--allowedTools` matters more than it looks: without it every tool call
    sits behind an interactive permission prompt, and there is nobody
    present to answer one. A headless session with no grant does not fail
    loudly -- it stalls.

    `--mcp-config` is passed inline rather than persisted to a `.mcp.json`,
    so a session's tool surface is a property of how it was launched and
    cannot leak into anyone else's. `--strict-mcp-config` is deliberately
    not passed."""
    # `-p` with no prompt argument: the prompt arrives on stdin.
    argv = [*command, "-p"]
    if model:
        argv += ["--model", model]

    grants = [allowed_tools] if allowed_tools else []
    if mcp_bridge_url:
        argv += ["--mcp-config", json.dumps(
            {"mcpServers": {"mcp-bridge": {"type": "http", "url": mcp_bridge_url}}}
        )]
        grants.append("mcp__mcp-bridge__*")
    if grants:
        argv += ["--allowedTools", ",".join(grants)]

    argv += ["--output-format", "stream-json", "--verbose"]
    return argv


def parse_stream(stdout: str) -> SessionResult:
    """Fold the stream into a result.

    Tolerant by construction: a line that is not JSON is skipped rather
    than raising, because a partial stream from a killed process is a
    normal thing to be handed and losing the tool sequence it *did* record
    would be worse than losing nothing."""
    tool_calls: list[str] = []
    denials: list[str] = []
    final: dict = {}
    rate_limit_windows: dict = {}

    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue

        if event.get("type") == "result":
            final = event
            continue

        # The only place the account's rolling usage windows are ever
        # visible: the CLI emits them mid-session and offers no way to
        # ask. Captured here so the gate has something to read next tick
        # -- see pu/usage_gate.py.
        if event.get("type") == "rate_limit_event":
            windows = usage_gate.parse_rate_limit_event(event)
            if windows:
                rate_limit_windows = windows
            continue

        message = event.get("message")
        if isinstance(message, dict):
            for block in message.get("content") or ():
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_calls.append(str(block.get("name", "")))

        if event.get("subtype") == "permission_denial" or "permission_denial" in str(
            event.get("type", "")
        ):
            denials.append(json.dumps(event)[:500])

    usage = final.get("modelUsage")
    models = tuple(usage.keys()) if isinstance(usage, dict) else ()

    for denial in final.get("permission_denials") or ():
        denials.append(json.dumps(denial)[:500] if not isinstance(denial, str) else denial)

    return SessionResult(
        exit_code=0,
        text=str(final.get("result") or ""),
        cost_usd=final.get("total_cost_usd"),
        duration_ms=final.get("duration_ms"),
        num_turns=final.get("num_turns"),
        is_error=final.get("is_error"),
        tool_calls=tuple(tool_calls),
        models_used=models,
        permission_denials=tuple(denials),
        rate_limit_windows=rate_limit_windows,
        raw_stream=stdout,
    )


def run_session(
    prompt: str,
    cwd: Path | None,
    allowed_tools: str | None = None,
    model: str | None = None,
    mcp_bridge_url: str | None = None,
    runner: Runner = subprocess_runner,
    command: Sequence[str] | None = None,
) -> SessionResult:
    argv = build_argv(
        allowed_tools, model, mcp_bridge_url, command or claude_command()
    )
    completed = runner(argv, cwd, prompt)
    parsed = parse_stream(completed.stdout)
    return dataclasses.replace(parsed, exit_code=completed.returncode)
