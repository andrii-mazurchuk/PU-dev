"""The only module that runs `task`.

Everything else in this unit goes through these functions, which is what
makes the backing store swappable and what makes the whole unit testable
against a scratch directory instead of a real install.

Three deliberate choices, each verified against the real 2.6.1 binary
before being built on:

**No rc file.** Every invocation passes `rc:/dev/null` plus explicit
`rc.<key>=<value>` overrides. The schema -- the UDAs, their closed value
lists, the urgency coefficients -- lives in `RC_SCHEMA` below, in code.
A `.taskrc` on disk would be a second copy of that, and two copies drift.
`render_taskrc()` exists to hand a human the same config for their own
by-hand use, generated from the one source.

**The binary may be behind a prefix.** There is no native Windows
Taskwarrior, so on this machine `task` is reached as
`wsl -d Ubuntu -e task`. That is entirely contained in `base_cmd`; nothing
else in the unit knows. `PU_TASK_CMD` sets it, and `-e` is not optional --
see `normalise_base_cmd`.

**Writes are serialised.** `task add` does not print the uuid of what it
just created, so recovering it means a follow-up `+LATEST export` -- which
is only correct if no other write interleaved.

ponytail: one process-wide write lock. Fine while PU runs one session at a
time and takes occasional API writes; if concurrent writers ever become
real, the fix is a uuid-generating add (`task import` with a pre-made
uuid), not a finer lock.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shlex
import subprocess
import threading
from pathlib import Path
from typing import Callable, Iterable, Sequence

from pu import records

# The Taskwarrior schema, as rc overrides. This is the single source of
# truth for it; `render_taskrc()` renders the same thing for a human.
#
# `uda.kind.values` is the load-bearing line: with it, the binary refuses a
# task carrying an unknown kind (exit 2, nothing written). Without it, an
# unknown kind is silently accepted and PU has no session type for it.
RC_SCHEMA: dict[str, str] = {
    "confirmation": "off",
    "verbose": "nothing",
    "hooks": "off",
    "uda.kind.type": "string",
    "uda.kind.label": "Kind",
    "uda.kind.values": ",".join(records.KINDS),
    "uda.url.type": "string",
    "uda.url.label": "URL",
    "uda.parent_uuid.type": "string",
    "uda.parent_uuid.label": "Parent",
    "uda.repo.type": "string",
    "uda.repo.label": "Repo",
    # Ordering across every source lives here rather than in a priority
    # ladder in code. Retuning what PU works on next is a config change.
    "urgency.uda.kind.intake.coefficient": "6.0",
    "urgency.uda.kind.research.coefficient": "4.0",
    "urgency.uda.kind.execution.coefficient": "2.0",
}


class TaskError(RuntimeError):
    """A `task` invocation failed. Carries the binary's own stderr, which
    is the useful part -- a rejected UDA value says exactly which value."""

    def __init__(self, argv: Sequence[str], returncode: int, stderr: str):
        self.argv = list(argv)
        self.returncode = returncode
        self.stderr = stderr.strip()
        super().__init__(f"task exited {returncode}: {self.stderr or '(no stderr)'}")


@dataclasses.dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[Sequence[str]], CommandResult]


def subprocess_runner(argv: Sequence[str]) -> CommandResult:
    """The real thing. Injected rather than called directly so the suite
    can drive a fake -- but note the suite also runs this one against a
    real binary, because a fake cannot catch an argv bug."""
    proc = subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return CommandResult(proc.returncode, proc.stdout or "", proc.stderr or "")


def normalise_base_cmd(base_cmd: Sequence[str]) -> tuple[str, ...]:
    """Force `wsl -e` in place of `wsl --`.

    This is a correctness *and* a safety fix, found by running the real
    binary against adversarial text. `wsl.exe -- <cmd>` hands the command
    to the default login shell, which expands it: backticks vanish,
    `${HOME}` interpolates, and `$(echo PWNED)` **executes**. `wsl.exe -e
    <cmd>` execs directly and every byte survives.

    That matters here more than it might elsewhere, because the text
    reaching this module is not all trusted: a task's annotations carry a
    session's own final message, so a session could otherwise write shell
    into a store write and have it run.

    Normalised rather than documented, because `PU_TASK_CMD` is config a
    human sets and a comment cannot stop someone typing `--`."""
    argv = list(base_cmd)
    if argv and Path(argv[0]).stem.lower() == "wsl":
        argv = ["-e" if a == "--" else a for a in argv]
    return tuple(argv)


def base_cmd_from_env(environ: dict[str, str] | None = None) -> tuple[str, ...]:
    """`PU_TASK_CMD` is the whole command up to the arguments -- normally
    just `task`, and on a Windows host `wsl -d Ubuntu -e task`. Config
    comes from the environment; the gateway injects it from units.yaml."""
    env = os.environ if environ is None else environ
    raw = env.get("PU_TASK_CMD", "task").strip()
    return normalise_base_cmd(shlex.split(raw) if raw else ["task"])


def render_taskrc(data_location: str) -> str:
    """The same schema as a `.taskrc`, for a human running `task` by hand.

    PU never reads this. It exists so that the config you use at the
    terminal and the config PU passes cannot disagree -- both are rendered
    from `RC_SCHEMA`."""
    lines = [
        "# generated by pu -- do not edit; regenerate from the unit",
        f"data.location={data_location}",
    ]
    lines += [f"{k}={v}" for k, v in RC_SCHEMA.items() if k not in ("verbose", "hooks")]
    return "\n".join(lines) + "\n"


def _encode_fields(fields: dict[str, object]) -> list[str]:
    """Attribute arguments, in Taskwarrior's `key:value` form.

    `None` means "clear this attribute", which Taskwarrior spells as an
    empty value. A sequence (for `depends`) is comma-joined. Tags are not
    handled here -- they are `+tag`/`-tag` positional arguments, not
    attributes, and go through `add_tags`/`remove_tags`."""
    args: list[str] = []
    for key, value in fields.items():
        if value is None:
            args.append(f"{key}:")
        elif isinstance(value, (list, tuple, set)):
            args.append(f"{key}:{','.join(str(v) for v in value)}")
        else:
            args.append(f"{key}:{value}")
    return args


class TaskStore:
    def __init__(
        self,
        data_location: str,
        base_cmd: Sequence[str] | None = None,
        runner: Runner = subprocess_runner,
    ):
        self.data_location = data_location
        self.base_cmd = (
            normalise_base_cmd(base_cmd) if base_cmd else base_cmd_from_env()
        )
        self._runner = runner
        self._write_lock = threading.Lock()

    # -- plumbing ---------------------------------------------------------

    def _argv(self, args: Iterable[str]) -> list[str]:
        argv = list(self.base_cmd)
        argv.append("rc:/dev/null")
        argv.append(f"rc.data.location={self.data_location}")
        argv += [f"rc.{k}={v}" for k, v in RC_SCHEMA.items()]
        argv += list(args)
        return argv

    def warm(self) -> bool:
        """Wake the binary, ignoring the result.

        When `task` lives inside WSL, the first call after the distro has
        gone idle pays for booting it -- measured at ~9s here against ~0.3s
        warm. That is long enough to time out the first real request after
        a quiet period, so a caller pays for something that has nothing to
        do with their request. Called in the background at startup."""
        try:
            return self._runner(list(self.base_cmd) + ["--version"]).returncode == 0
        except OSError:
            return False

    def run(self, args: Iterable[str]) -> CommandResult:
        argv = self._argv(args)
        result = self._runner(argv)
        if result.returncode != 0:
            raise TaskError(argv, result.returncode, result.stderr)
        return result

    # -- reads ------------------------------------------------------------

    def export(self, *filters: str) -> list[records.Task]:
        """Every matching task, as records.

        An empty result is a normal answer, not an error: a filter matching
        nothing exits 0 with `[]`, and a store that has never been written
        to behaves the same. Absence is normal throughout this system."""
        result = self.run([*filters, "export"])
        text = result.stdout.strip()
        if not text:
            return []
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return []
        return [records.from_export(entry) for entry in raw if isinstance(entry, dict)]

    def get(self, uuid: str) -> records.Task | None:
        """One task by uuid, or None. Never by `id` -- see records.py."""
        found = self.export(uuid)
        return found[0] if found else None

    def ready(self, *extra_filters: str) -> list[records.Task]:
        """Open, unblocked, unclaimed work, most urgent first.

        `+READY` is Taskwarrior's own virtual tag for pending-and-unblocked
        -and-not-waiting; `-ACTIVE` excludes what another session has
        claimed. Together they are wayfinder's **frontier**: the edge of
        the known, for whoever is working the map.

        Note this includes human-in-the-loop tickets. That is the point --
        a person working a map has to see the grilling ticket that is next.
        What PU itself may *select* is a strictly narrower question; see
        `runnable`."""
        tasks = self.export("+READY", "-ACTIVE", *extra_filters)
        return sorted(tasks, key=lambda t: t.urgency, reverse=True)

    def runnable(self, *extra_filters: str) -> list[records.Task]:
        """The frontier, narrowed to what an agent may actually be given.

        The narrowing is `records.Task.runnable` rather than a filter
        string, deliberately: runnability is "carries `afk` **and** has a
        session type behind its kind", and expressing that as a Taskwarrior
        filter would be a second copy of `SESSION_TYPE_FOR_KIND` that
        drifts the first time a kind is added."""
        return [t for t in self.ready(*extra_filters) if t.runnable]

    def count(self, *filters: str) -> int:
        result = self.run([*filters, "count"])
        try:
            return int(result.stdout.strip() or 0)
        except ValueError:
            return 0

    # -- writes -----------------------------------------------------------

    def add(
        self,
        description: str,
        tags: Sequence[str] = (),
        **fields: object,
    ) -> str:
        """Create a task, return its uuid.

        The description goes after `--` so that one containing a colon or a
        leading `+` is not reparsed as an attribute or a tag -- verified
        against the real binary, and the failure would otherwise be silent
        data corruption rather than an error.

        An unknown `kind` raises here: the binary rejects it, exit 2, and
        writes nothing."""
        records.check_kind(fields.get("kind"))  # type: ignore[arg-type]
        args = _encode_fields(fields)
        args += [f"+{t}" for t in tags]
        with self._write_lock:
            self.run(["add", *args, "--", description])
            # `task add` does not report what it created; +LATEST does.
            # Correct only under the write lock.
            latest = self.export("+LATEST")
        if not latest:
            raise TaskError(["add"], 0, "task added but could not be read back")
        return latest[0].uuid

    def modify(
        self,
        uuid: str,
        add_tags: Sequence[str] = (),
        remove_tags: Sequence[str] = (),
        **fields: object,
    ) -> None:
        if "kind" in fields:
            records.check_kind(fields["kind"])  # type: ignore[arg-type]
        args = _encode_fields(fields)
        args += [f"+{t}" for t in add_tags]
        args += [f"-{t}" for t in remove_tags]
        if not args:
            return
        with self._write_lock:
            self.run([uuid, "modify", *args])

    def annotate(self, uuid: str, text: str) -> None:
        with self._write_lock:
            self.run([uuid, "annotate", "--", text])

    def claim(self, uuid: str) -> None:
        """Mark a task as being worked. `start` sets `+ACTIVE`, which is
        what removes it from every other session's frontier -- the claim
        is the store's own state, not a convention layered on top."""
        with self._write_lock:
            self.run([uuid, "start"])

    def release(self, uuid: str) -> None:
        """Undo a claim without resolving. The stale-claim reaper's tool."""
        with self._write_lock:
            self.run([uuid, "stop"])

    def complete(self, uuid: str) -> None:
        with self._write_lock:
            self.run([uuid, "done"])
