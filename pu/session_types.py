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

`research` gets the SOP lookup script specifically rather than
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
REACHES_PEERS = frozenset({"research", "execution"})


# The role every session runs under unless its type names another. An
# agent across the node is one or more session types here, a channel in cu
# and a memory namespace in mu-agent, joined by this name. The list of roles
# is the gateway's, written by cu; `system` is the one that always exists,
# so system work never depends on either. (pu vision, 2026-10-24.)
DEFAULT_AGENT_ROLE = "system"


class SessionTypeError(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class SessionType:
    name: str
    dir: Path

    # Set for a runtime-authored type, None for one of the four committed
    # directories. Which source a type came from decides where every
    # property below reads from, and nothing else in this unit has to care.
    record: dict | None = None

    @property
    def grant(self) -> Grant | None:
        """The permission bundle, or None for a directory-defined type --
        those still answer from the name-keyed maps, which is the migration
        decision rather than an oversight."""
        if self.record is None:
            return None
        return GRANTS[self.record["grant"]]

    @property
    def allowed_tools(self) -> str | None:
        if self.record is not None:
            # An empty tool string returns None, which is what absence from
            # ALLOWED_TOOLS already means downstream: `build_argv` emits no
            # `--allowedTools` at all for a falsy value. Whether that
            # genuinely denies everything is an open question, and this
            # property deliberately does not answer it -- it only has to
            # avoid changing the answer.
            return self.grant.tools or None
        return ALLOWED_TOOLS.get(self.name)

    @property
    def model(self) -> str | None:
        if self.record is not None:
            return self.record.get("model")
        return MODELS.get(self.name)

    @property
    def runs_in_target_repo(self) -> bool:
        if self.record is not None:
            return self.grant.runs_in_target_repo
        return self.name in RUNS_IN_TARGET_REPO

    @property
    def reaches_peers(self) -> bool:
        if self.record is not None:
            return self.grant.reaches_peers
        return self.name in REACHES_PEERS

    @property
    def agent_role(self) -> str:
        """The committed directories are system work by definition."""
        return (self.record or {}).get("agent_role") or DEFAULT_AGENT_ROLE

    @property
    def claim_tags(self) -> tuple[str, ...]:
        return tuple(((self.record or {}).get("claims") or {}).get("tags") or ())

    def claims(self, kind: str | None, tags) -> bool:
        """Whether this type claims a task of this kind carrying these tags.

        The settled binding is "session types claim by kind and tag", and it
        points one way only: a type names what it claims, and a task says
        nothing about session types. So writing a type never edits a task,
        and a task written before a type existed becomes claimable the
        moment that type is.

        A claim with no `kind` claims nothing. Matching every kind would let
        one careless record swallow the whole queue, and the failure would
        look like tasks silently running under the wrong instructions."""
        claims = (self.record or {}).get("claims") or {}
        if not claims.get("kind") or claims["kind"] != kind:
            return False
        return set(self.claim_tags).issubset(set(tags or ()))

    def instructions(self) -> str:
        """The type's own CLAUDE.md text.

        Read explicitly only for a type that runs elsewhere -- normally
        Claude Code loads it natively because the session's cwd *is* this
        directory, and reading it here as well would be a second copy."""
        try:
            return (self.dir / "CLAUDE.md").read_text(encoding="utf-8")
        except OSError:
            return ""


def discover(session_types_dir: Path, store=None) -> dict[str, SessionType]:
    """Every session type this unit can run, from both of its sources.

    **The four committed directories stay the source for themselves** and
    the store adds runtime-authored types beside them. That is the
    migration decision rather than a halfway state: turning the four into
    records would mean first deciding what "no tools" actually denies, and
    `ask` -- the one session driven by untrusted text from an
    unauthenticated origin -- is the only type on that path.

    A directory wins over a record of the same name. Such a record is
    refused at the store, so this is defence in depth rather than the real
    guard -- but a record able to shadow `execution` would be a record able
    to redefine what `execution` is allowed to do.
    """
    root = Path(session_types_dir)
    if not root.exists():
        raise SessionTypeError(f"session types directory not found: {root}")

    found: dict[str, SessionType] = {}
    if store is not None:
        for name, record in sorted(store.current().items()):
            # `enabled` is the switch, and this is the only place it has
            # teeth. A record that is written but not enabled must not
            # claim a task: writing one would otherwise be enough to start
            # spending money, which is what the flag exists to prevent.
            # The four committed directories have no flag -- being
            # committed *is* their approval.
            if not record.get("enabled"):
                continue
            # A record appended to the log by hand has no generated file
            # and no directory to run in. The record is the truth and the
            # file is its artifact, so a missing artifact is rebuilt
            # rather than being a session that runs with no instructions.
            store.ensure_generated(record)
            found[name] = SessionType(
                name=name, dir=store.dirs / name, record=record)

    for path in sorted(root.iterdir()):
        if path.is_dir() and (path / "CLAUDE.md").exists():
            found[path.name] = SessionType(name=path.name, dir=path)
    return found


def for_task(types: dict[str, SessionType], task) -> SessionType | None:
    """Which session type runs this task.

    A claiming type wins over the kind's default, because claiming is the
    whole point: a task of kind `execution` tagged `digest` should reach the
    type written for digests rather than the general execution session.

    **Most specific wins**, by number of claim tags, then by name so the
    outcome never depends on dict ordering. A type claiming more tags has
    stated a narrower case, and a tie broken arbitrarily would mean one
    task running under different instructions on different ticks.
    """
    claiming = [t for t in types.values() if t.claims(task.kind, task.tags)]
    if claiming:
        return sorted(claiming, key=lambda t: (-len(t.claim_tags), t.name))[0]
    return types.get(task.session_type or "")
