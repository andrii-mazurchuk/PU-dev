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
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
from pathlib import Path
from typing import Callable, Sequence


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
    raw_stream: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.is_error


@dataclasses.dataclass(frozen=True)
class CompletedRun:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[Sequence[str], "Path | None"], CompletedRun]


def subprocess_runner(argv: Sequence[str], cwd: Path | None) -> CompletedRun:
    proc = subprocess.run(
        list(argv),
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return CompletedRun(proc.returncode, proc.stdout or "", proc.stderr or "")


def build_argv(
    prompt: str,
    allowed_tools: str | None = None,
    model: str | None = None,
    append_system_prompt: str | None = None,
    mcp_bridge_url: str | None = None,
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
    argv = ["claude", "-p", prompt]
    if model:
        argv += ["--model", model]
    if append_system_prompt:
        argv += ["--append-system-prompt", append_system_prompt]

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
        raw_stream=stdout,
    )


def run_session(
    prompt: str,
    cwd: Path | None,
    allowed_tools: str | None = None,
    model: str | None = None,
    append_system_prompt: str | None = None,
    mcp_bridge_url: str | None = None,
    runner: Runner = subprocess_runner,
) -> SessionResult:
    argv = build_argv(prompt, allowed_tools, model, append_system_prompt, mcp_bridge_url)
    completed = runner(argv, cwd)
    parsed = parse_stream(completed.stdout)
    return dataclasses.replace(parsed, exit_code=completed.returncode)
