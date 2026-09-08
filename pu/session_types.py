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
from pathlib import Path

LOOKUP = "Bash(python3 ../../scripts/tag_sop_lookup.py:*)"

ALLOWED_TOOLS: dict[str, str] = {
    "intake": f"Read,{LOOKUP}",
    "research": f"Read,WebSearch,WebFetch,{LOOKUP}",
    "execution": f"Bash,Read,Write,Edit,{LOOKUP}",
}

# Absent means "whatever the CLI defaults to". Only set a model where the
# work genuinely differs in reasoning load.
MODELS: dict[str, str] = {}

# A type that runs in the *target repository* rather than in its own
# directory. That repo's own CLAUDE.md, conventions and hooks then apply,
# which is the point -- a per-repo gate is the strongest safety property
# available and it is wired there, not here. The cost is that this type's
# own CLAUDE.md no longer loads from cwd, so it is injected instead.
RUNS_IN_TARGET_REPO = frozenset({"execution"})


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
