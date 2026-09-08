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

**Creation goes through `import`, on stdin, with a uuid we chose.**
`task add` does not report what it created, so recovering the uuid meant a
follow-up `+LATEST export` -- correct only while nothing else writes. It
does not: a wayfinder session driving the same store through the `task`
CLI can land a write in that window, and we would return *its* uuid as
ours. Choosing the uuid removes the race rather than narrowing it, and
because the record travels on stdin it also escapes the command-line
length limit (~30KB on Windows) and every quoting hazard.

That is also why there is no write lock here. Taskwarrior does its own
file locking, and ours would only have protected us from ourselves --
which was never the risk once a second writer became real.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shlex
import subprocess
import uuid as uuid_module
from datetime import datetime, timezone
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


Runner = Callable[[Sequence[str], "str | None"], CommandResult]


def subprocess_runner(argv: Sequence[str], stdin: str | None = None) -> CommandResult:
    """The real thing. Injected rather than called directly so the suite
    can drive a fake -- but note the suite also runs this one against a
    real binary, because a fake cannot catch an argv bug."""
    proc = subprocess.run(
        list(argv),
        input=stdin,
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


def path_for_binary(path: Path | str, base_cmd: Sequence[str]) -> str:
    """A local path, written the way the `task` binary will read it.

    When the binary is reached through WSL it sits on the other side of a
    filesystem boundary: a Windows path means nothing to it, so a config
    file handed to a session as `C:\\...` is a file that session cannot
    open. Windows drives are mounted at `/mnt/<letter>`, so the
    translation is deterministic and costs no subprocess.

    Anything else -- a native binary, or an already-POSIX path -- comes
    back unchanged. Natively on Linux this function does nothing at all,
    which is the point: the boundary only exists in one arrangement."""
    text = str(path)
    if not base_cmd or Path(base_cmd[0]).stem.lower() != "wsl":
        return text
    resolved = Path(text).resolve()
    if not resolved.drive:
        return text.replace("\\", "/")
    drive = resolved.drive.rstrip(":").lower()
    rest = str(resolved)[len(resolved.drive):].replace("\\", "/").lstrip("/")
    return f"/mnt/{drive}/{rest}"


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
    # `verbose` is kept: this file's main consumer is a session parsing
    # `task ... export`, and Taskwarrior's chatter ("TASKRC override…",
    # "The project X has changed") lands on stderr, where a caller that
    # treats stderr as failure aborts on it. A person wanting the chatter
    # back passes `rc.verbose=on` for that one command.
    #
    # `hooks` is dropped on purpose: this unit disables them for its own
    # writes, but a person's hooks are theirs and nothing here should
    # switch them off.
    lines += [f"{k}={v}" for k, v in RC_SCHEMA.items() if k != "hooks"]
    return "\n".join(lines) + "\n"


def _encode_fields(fields: dict[str, object]) -> list[str]:
    """Attribute arguments, in Taskwarrior's `key:value` form.

    `None` means "clear this attribute", which Taskwarrior spells as an
    empty value. A sequence (for `depends`) is comma-joined. Tags are not
    handled here -- they are `+tag`/`-tag` positional arguments, not
    attributes, and go through `add_tags`/`remove_tags`.

    **A multi-line value is refused.** Measured against the real binary: a
    `key:value` argument containing newlines, past a few hundred bytes,
    exits 0, silently discards the value, *and overwrites the task's
    description*. Nothing here does that today; this stops the next caller
    finding out by losing data. Long text belongs in an annotation, which
    is protected by `--`, or in the body store."""
    args: list[str] = []
    for key, value in fields.items():
        if value is None:
            args.append(f"{key}:")
        elif isinstance(value, (list, tuple, set)):
            args.append(f"{key}:{','.join(str(v) for v in value)}")
        else:
            text = str(value)
            if "\n" in text or "\r" in text:
                raise TaskError(
                    [f"{key}:<multi-line>"], 0,
                    f"{key} cannot hold a multi-line value: Taskwarrior "
                    f"discards it and corrupts the description",
                )
            args.append(f"{key}:{text}")
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
            return self._runner(list(self.base_cmd) + ["--version"], None).returncode == 0
        except OSError:
            return False

    def run(self, args: Iterable[str], stdin: str | None = None) -> CommandResult:
        argv = self._argv(args)
        result = self._runner(argv, stdin)
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

        Written through `import` with a uuid we chose, on stdin. `task add`
        never reports what it created, so the uuid had to be recovered by a
        follow-up `+LATEST export` -- which returns the wrong task if
        anything else wrote in between, and something else does write now:
        a session driving the same store through the `task` CLI. Choosing
        the uuid removes the race rather than narrowing it.

        stdin also means the record escapes the command-line length limit
        and every quoting hazard, so a description carrying newlines,
        backticks or `$(...)` is stored verbatim.

        An unknown `kind` raises before anything is written."""
        records.check_kind(fields.get("kind"))  # type: ignore[arg-type]

        new_uuid = str(uuid_module.uuid4())
        record: dict[str, object] = {
            "uuid": new_uuid,
            "status": "pending",
            "description": description,
            "entry": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        }
        for key, value in fields.items():
            if value is None:
                continue
            record[key] = (
                list(value) if isinstance(value, (list, tuple, set)) else str(value)
            )
        if tags:
            record["tags"] = list(tags)

        self.run(["import"], stdin=json.dumps([record]))
        return new_uuid

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
        self.run([uuid, "modify", *args])

    def annotate(self, uuid: str, text: str) -> None:
        self.run([uuid, "annotate", "--", text])

    def claim(self, uuid: str) -> None:
        """Mark a task as being worked. `start` sets `+ACTIVE`, which is
        what removes it from every other session's frontier -- the claim
        is the store's own state, not a convention layered on top."""
        self.run([uuid, "start"])

    def release(self, uuid: str) -> None:
        """Undo a claim without resolving. The stale-claim reaper's tool."""
        self.run([uuid, "stop"])

    def complete(self, uuid: str) -> None:
        self.run([uuid, "done"])
