"""Which session types exist, and what each is allowed to do.

**A session type is a directory containing a `CLAUDE.md`.** That is the
whole definition, and discovery is a filesystem walk rather than a
manifest. A sibling unit's architecture audit found session-type identity
defined in four separate places and already silently drifted between them;
one definition derived from the tree cannot do that.

Adding a type is dropping a directory in. The two maps below are the only
code that has to change, and only because a tool grant is a security
decision that should be visible in code review rather than inferred from a
file someone dropped in.

## On the grants

Each grant is built from what the type's `CLAUDE.md` actually instructs,
starting from nothing -- not from what might be convenient. The prose in a
`CLAUDE.md` is advisory; the grant is what actually holds. A research
session cannot write files because `Write` is not in its list, and the
sentence telling it not to is a courtesy on top of that.

`intake` and `research` get the SOP lookup script specifically rather than
`Bash`, so resolving a procedure is always possible and reading the
filesystem generally is not.

`execution` gets Bash outright: its job is writing and testing real code in
a real repository, which cannot be enumerated in advance.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

# The interpreter a session should invoke. `python3` is right on Linux and
# absent on Windows, where only `python` exists -- so a grant naming just
# one of them silently fails on the other platform, and a session with no
# way to resolve a procedure does not fail loudly, it just quietly ignores
# a mandatory one.
INTERPRETER = "python" if os.name == "nt" else "python3"
LOOKUP_SCRIPT = "../../scripts/tag_sop_lookup.py"
LOOKUP_COMMAND = f"{INTERPRETER} {LOOKUP_SCRIPT}"

# Both spellings are granted regardless of platform. It costs nothing, and
# it means a session that reaches for the other name still works rather
# than hitting a permission prompt nobody is present to answer.
LOOKUP = (f"Bash(python3 {LOOKUP_SCRIPT}:*),"
          f"Bash(python {LOOKUP_SCRIPT}:*)")

ALLOWED_TOOLS: dict[str, str] = {
    "intake": f"Read,{LOOKUP}",
    "research": f"Read,WebSearch,WebFetch,{LOOKUP}",
    "execution": f"Bash,Read,Write,Edit,{LOOKUP}",
}


@dataclasses.dataclass(frozen=True)
class Grant:
    """What a session type is permitted, as one named bundle.

    Named `grant` and not `role` on purpose: the node's shared glossary
    already owns Role as a delivery-capability slot (`owner`, `logs`,
    `thinking`, resolved at the bridge), and this is the word this file
    already used for the other thing.

    Everything in here is a permission, which is why they travel together
    and why the set is small and fixed rather than composable: a grant is a
    security decision and stays visible in code review rather than inferred
    from a record someone wrote at runtime. `model` is deliberately *not* a
    field here -- it is cost and reasoning load, not permission, so it
    stays an ordinary field of the record.

    `runs_in_target_repo` is in here because it decides *where* code runs,
    and the system-allocated per-type directory is the scoping enforcement
    point. A runtime-authored type able to grant itself target-repo
    execution would walk straight out of that.
    """

    tools: str
    reaches_peers: bool
    runs_in_target_repo: bool


# The four grants, which are the four already in force -- renamed from the
# job each directory happened to do to what each actually permits, because
# a person approving a new type judges risk in one glance and "grant: repo"
# says it where "grant: execution" does not. They read in escalation order.
#
# Adding a fifth is adding one line here, visible in review. That is the
# escape hatch, and it is the reason the set stays fixed: `read` plus peer
# reach is the obvious gap and no type has asked for it yet.
#
# Each grant names its own peer reach rather than leaving it per-type:
# handing a session a bridge URL adds `mcp__mcp-bridge__*`, every tool
# every peer exposes, and that is not something a deployment setting should
# widen by filling in an env var.
GRANTS: dict[str, Grant] = {
    "none": Grant(tools="", reaches_peers=False, runs_in_target_repo=False),
    "read": Grant(tools=f"Read,{LOOKUP}",
                  reaches_peers=False, runs_in_target_repo=False),
    "web": Grant(tools=f"Read,WebSearch,WebFetch,{LOOKUP}",
                 reaches_peers=True, runs_in_target_repo=False),
    "repo": Grant(tools=f"Bash,Read,Write,Edit,{LOOKUP}",
                  reaches_peers=True, runs_in_target_repo=True),
}

# Which grant each of the four directory-defined types would carry. Not
# wired into the properties below yet, deliberately: that is the migration
# ticket's job, and it has one trap to get right first.
#
# ponytail: `none` is expressed here as an empty tool string, while the
# directory maps express it as *absence from the map* -- and those are not
# the same thing downstream. An absent entry means `runner.build_argv`
# emits no `--allowedTools` at all, which is not "zero tools"; an empty
# string would emit `--allowedTools ""`. `ask` is the only type on that
# path today. Settle which of the two actually denies everything before
# making these maps derive from GRANTS, or the migration quietly changes
# what the one untrusted-input session is allowed to do.
GRANT_FOR_DIRECTORY_TYPE: dict[str, str] = {
    "ask": "none",
    "intake": "read",
    "research": "web",
    "execution": "repo",
}

# `ask` is absent from all three maps below, and from
# records.SESSION_TYPE_FOR_KIND, and every one of those absences is
# deliberate rather than pending.
#
# It answers a question typed into the dashboard from an unauthenticated
# origin, using state assembled into its prompt. It gets **no tools**:
# sessions this unit spawns have no way back into it, and this one needs
# nothing the prompt does not already carry. Granting it `Read` would buy
# nothing and would hand a session driven by untrusted text a filesystem.
#
# Having no kind mapped to it is what keeps it off the queue entirely --
# there is no record anyone can write that causes an ask session to run.
# The only path to one is `POST /ask`.

# Absent means "whatever the CLI defaults to". Only set a model where the
# work genuinely differs in reasoning load.
MODELS: dict[str, str] = {}

# A type that runs in the *target repository* rather than in its own
# directory. That repo's own CLAUDE.md, conventions and hooks then apply,
# which is the point -- a per-repo gate is the strongest safety property
# available and it is wired there, not here. The cost is that this type's
# own CLAUDE.md no longer loads from cwd, so it is injected instead.
RUNS_IN_TARGET_REPO = frozenset({"execution"})

# A type that may reach other units' tools through the gateway's MCP bridge.
#
# This is a grant, so it belongs here beside ALLOWED_TOOLS rather than in
# config: handing a session a bridge URL adds `mcp__mcp-bridge__*`, which is
# every tool every peer exposes -- a far wider surface than anything in the
# list above, and one that grows whenever somebody registers a new unit.
#
# `intake` is deliberately absent. Its grant is `Read` plus the lookup
# script, deliberately narrow because it is the one session that causes work
# to exist; widening it to the whole system's tool surface is not something
# a deployment setting should be able to do by filling in an env var.
REACHES_PEERS = frozenset({"research", "execution"})


class SessionTypeError(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class SessionType:
    name: str
    dir: Path

    @property
    def allowed_tools(self) -> str | None:
        return ALLOWED_TOOLS.get(self.name)

    @property
    def model(self) -> str | None:
        return MODELS.get(self.name)

    @property
    def runs_in_target_repo(self) -> bool:
        return self.name in RUNS_IN_TARGET_REPO

    @property
    def reaches_peers(self) -> bool:
        return self.name in REACHES_PEERS

    def instructions(self) -> str:
        """The type's own CLAUDE.md text.

        Read explicitly only for a type that runs elsewhere -- normally
        Claude Code loads it natively because the session's cwd *is* this
        directory, and reading it here as well would be a second copy."""
        try:
            return (self.dir / "CLAUDE.md").read_text(encoding="utf-8")
        except OSError:
            return ""


def discover(session_types_dir: Path) -> dict[str, SessionType]:
    """Any immediate subdirectory holding a `CLAUDE.md`. The directory name
    is the type's name, so duplicates are impossible."""
    root = Path(session_types_dir)
    if not root.exists():
        raise SessionTypeError(f"session types directory not found: {root}")
    return {
        path.name: SessionType(name=path.name, dir=path)
        for path in sorted(root.iterdir())
        if path.is_dir() and (path / "CLAUDE.md").exists()
    }
