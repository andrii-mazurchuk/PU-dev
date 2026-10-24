"""The session-type store: one append-only log, `current` is the last line.

A session type used to be a directory in this repo, so adding one was a
commit and a deploy. This store is the other half of that: a record written
at runtime, by a person at the dashboard or by an agent over the API, with
no redeploy.

**Append-only JSONL, and `current` is the last line for a name.** History
is not a feature here -- the format gives it away for free, which is
exactly why nothing is built on top of it. No diffing, no rollback UI, no
"restore version 3". If those are ever wanted they are a new decision, not
an obvious extension of this file.

**The record is the source of truth and `CLAUDE.md` is generated from it.**
Never the other way round. That is what makes prompt iteration versioned:
every write appends a line and regenerates the file, so the prompt a
session ran under is recoverable from the log, and a hand-edit of the
generated file is overwritten rather than respected. The generated file is
an artifact.

**Two origins, one record shape.** A type authored here and a type declared
by a peer unit differ in how they are gated, not in what they look like --
so `origin` is a field and there is no second store. pu takes all
computational sessions from other units wherever possible, so the declared
origin is the larger path, not the exception.

What is deliberately *not* here: the first-enable gate, the auto-approve
switch, and notification. This file records what happened and answers
`has_ever_been_enabled`, which is the only thing the gate needs from it.
Deciding whether an enable is allowed is a different concern and a
different ticket, and putting it here would mean every writer carries the
policy.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterator

from pu import jsonl, session_types

# A name is the identity and it is also a directory name, so it is built
# into a filesystem path from input that arrives over HTTP. Strict rather
# than trusting the caller, the same reasoning as the body store's uuid
# pattern. A dot is allowed because a peer-declared type is namespaced by
# its declaring unit (`mu-spec.verify-layer`); `..` is refused outright so
# the dot cannot be used to climb.
_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

# The record, as reconstructed from the session-type object decision and
# the role-filler convention. Required fields first, then the ones whose
# absence is meaningful.
REQUIRED = ("name", "purpose", "prompt", "grant", "origin")
OPTIONAL = ("model", "mcp_servers", "plugins", "trigger", "enabled", "claims",
            "retired", "agent_role")

ORIGINS = ("authored", "declared")

# The generated file carries this header so nobody edits it by hand twice.
_GENERATED_HEADER = (
    "<!-- Generated from this unit's session-type store. Edits here are\n"
    "     overwritten on the next write; change the record instead. -->\n\n"
)


def contract_preamble(grant_name: str) -> str:
    """What pu owes a session, above whatever its author wrote.

    Every hand-written session type in this repo states that the final
    message is the resolution -- `research/CLAUDE.md` says it outright and
    `execution/CLAUDE.md` gives it a section. A generated file used to
    carry the author's prompt and nothing else, so the one thing every
    committed type teaches its session was the one thing a runtime type
    could not know. The first real runtime session proved it: its prompt
    said "resolve the task", it had no peer reach, so it spent part of a
    paid turn attempting `pu_resolve_task`, was refused, and reported that
    the task was still open -- while pu took that complaint as the answer
    and completed the task.

    **Derived from the grant, not templated per grant.** The reach sentence
    follows from `Grant.reaches_peers`, so a fifth grant inherits this
    rather than needing a fifth paragraph somebody has to remember to
    write.

    This goes **above** the author's prompt, and it wins. A prompt telling
    the session to call `pu_resolve_task` is wrong whatever its author
    intended, and the point of the preamble is that this is visible before
    the session wastes a turn discovering it.
    """
    grant = session_types.GRANTS[grant_name]

    if grant.reaches_peers:
        reach = (
            "You can reach other units' tools through the bridge "
            "(`mcp__mcp-bridge__*`). **Do not resolve this task yourself "
            "even though you can** -- this unit does it from your final "
            "message, and doing both records the answer twice."
        )
    else:
        reach = (
            "You have no way back into this unit. There are no `pu_*` tools "
            "in this session, and attempting one is refused rather than "
            "queued -- it only costs you part of the turn."
        )

    return "\n".join([
        "## How this session ends",
        "",
        "**Your final message is the resolution.** It is recorded against "
        "the task and the task is then closed. There is nothing else to do "
        "and no tool to call to make that happen.",
        "",
        reach,
        "",
        f"Tools granted: {grant.tools or 'none'}.",
        "",
        "If the task carries a `## Payload` section, that is what the "
        "trigger which created it fetched. A failed or absent fetch is "
        "**stated** there rather than left out, so an empty payload is a "
        "fact you can act on and not a gap to go and fill.",
        "",
        "---",
        "",
        "",
    ])


class TypeStoreError(ValueError):
    """A record that cannot be represented, or a name that cannot be a
    path. Raised rather than degraded: a session type decides what tools a
    session gets, so a half-understood record must not become a running
    session."""


def _validate(record: dict[str, Any]) -> dict[str, Any]:
    unknown = set(record) - set(REQUIRED) - set(OPTIONAL)
    if unknown:
        raise TypeStoreError(
            f"unknown field(s) {sorted(unknown)} "
            f"(allowed: {sorted(REQUIRED + OPTIONAL)})")
    missing = [f for f in REQUIRED if not record.get(f)]
    if missing:
        raise TypeStoreError(f"missing required field(s) {missing}")

    name = record["name"]
    if not isinstance(name, str) or not _NAME.match(name) or ".." in name:
        raise TypeStoreError(f"not a usable session-type name: {name!r}")

    # The four committed types answer from the name-keyed maps in code, so a
    # record of the same name would be a record that looks authoritative and
    # is not. `discover` resolves the collision in the directories' favour,
    # which means such a record would be silently inert -- and a silently
    # inert security decision is worse than a refused one. Named here so the
    # person writing it finds out immediately.
    if name in session_types.GRANT_FOR_DIRECTORY_TYPE:
        raise TypeStoreError(
            f"{name!r} is a committed session type, defined by its directory "
            f"and its grant in code. A record cannot redefine it; choose "
            f"another name")

    grant = record["grant"]
    if grant not in session_types.GRANTS:
        raise TypeStoreError(
            f"unknown grant {grant!r} "
            f"(allowed: {sorted(session_types.GRANTS)})")

    # Named `agent_role`, not `role`: the glossary's Role is a delivery slot
    # (owner, logs, thinking). Absent means system, filled in here so a
    # record never carries "unknown" -- and so every record written before
    # the field existed reads the same as one that says system.
    role = record.get("agent_role") or session_types.DEFAULT_AGENT_ROLE
    if not isinstance(role, str) or not _NAME.match(role) or ".." in role:
        raise TypeStoreError(f"not a usable agent role: {role!r}")
    record = dict(record, agent_role=role)

    origin = record["origin"]
    if origin not in ORIGINS:
        raise TypeStoreError(f"unknown origin {origin!r} (allowed: {ORIGINS})")

    # A declared type arrives inside reviewed, deployed software, so it is
    # trusted enough to skip the first-enable gate -- which is exactly why
    # it must not be able to ask for the one grant that is arbitrary code
    # execution in a repository. The declaring unit requests and the
    # running unit grants; this is the refusal, stated rather than
    # silently downgraded to something weaker.
    if origin == "declared" and session_types.GRANTS[grant].runs_in_target_repo:
        raise TypeStoreError(
            f"a declared session type may not request grant {grant!r}: "
            f"it runs in the target repository, and that is granted by a "
            f"person, never by a declaration")

    return dict(record)


class TypeStore:
    """The log, plus the directory tree generated from it."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.log = self.root / "session_types.jsonl"
        self.dirs = self.root / "session_types"

    # -- reading ---------------------------------------------------------

    def _lines(self) -> Iterator[dict[str, Any]]:
        return jsonl.lines(self.log, TypeStoreError)

    def current(self) -> dict[str, dict[str, Any]]:
        return jsonl.current(self.log, TypeStoreError)

    def get(self, name: str) -> dict[str, Any] | None:
        return self.current().get(name)

    def has_ever_been_enabled(self, name: str) -> bool:
        """Whether this name was ever written with `enabled` true.

        What the first-enable gate asks, and the only reason the log's
        history is read rather than just its tail. `current()` cannot
        answer it: a type enabled and then disabled reads as never-enabled
        from the last line alone, and would take the gate a second time."""
        return any(record.get("name") == name and record.get("enabled")
                   for record in self._lines())

    # -- writing ---------------------------------------------------------

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        """Validate, append one line, regenerate the type's directory.

        The order matters: the log is the source of truth, so it is written
        first. A crash between the two leaves a correct log and a stale
        file, which the next write fixes. The other order would leave a
        directory no record explains."""
        validated = _validate(record)
        jsonl.append(self.log, validated)
        self._generate(validated)
        return validated

    def ensure_generated(self, record: dict[str, Any]) -> None:
        """Write the type's file if it is not there, leave it if it is.

        **A record written by hand is a whole session type.** The log is
        a plain JSONL file in private storage, and appending a line to it
        is a legitimate way to configure this unit -- no HTTP, no
        restart, the next read picks it up. What that path cannot do is
        produce the generated `CLAUDE.md`, which until now left a type
        that claimed tasks and then ran with no instructions and a
        working directory that did not exist.

        Regenerating it here is not a special case for hand edits; it
        follows from the rule the store already has. The record is the
        source of truth and the file is an artifact, so an artifact that
        is missing is rebuilt from the truth. An artifact that is
        *present* is left exactly as it is -- this is not a reconciler,
        and silently rewriting a file on every read would make a
        deliberate local change impossible to keep for even one tick.
        """
        if not (self.dirs / record["name"] / "CLAUDE.md").exists():
            self._generate(record)

    def _generate(self, record: dict[str, Any]) -> Path:
        """Write the type's `CLAUDE.md` from its record.

        `newline="\\n"` is not cosmetic. This unit has already been bitten
        once by Python translating `\\n` to `\\r\\n` on Windows in a file
        read by something that did not expect it -- see
        `tests/test_file_formats.py`."""
        directory = self.dirs / record["name"]
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "CLAUDE.md"
        target.write_text(
            _GENERATED_HEADER
            + contract_preamble(record["grant"])
            + record["prompt"].rstrip("\n") + "\n",
            encoding="utf-8", newline="\n")
        return target
