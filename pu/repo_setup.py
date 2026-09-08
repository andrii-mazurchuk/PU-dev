"""Preparing a target repository for an execution session.

An execution session runs with the *target repo* as its cwd, so that
repo's own `CLAUDE.md`, conventions and hooks apply. That is the whole
reason for the choice: a per-repo verification gate is the strongest safety
property available and it is wired there, not here.

The cost is that this unit's `session_types/execution/.claude/skills/` no
longer load from cwd. So they are copied in.

Two rules govern the copy, and both are about not being destructive in
someone else's repository:

**Never overwrite.** A file that already exists at the destination is left
exactly as it is and reported as skipped. The repo's own version of
anything always wins -- this unit is a guest.

**Never merge settings.** `.claude/settings.json` is the repo's own
security configuration; silently editing it would be changing what a repo
permits without anyone reviewing the change. If one exists, it is left
alone and reported. If none exists, none is created either -- an absent
settings file means the repo's defaults, and inventing one would be the
same act.

Everything reports rather than raises, except "this is not a git
repository", which is the one condition under which running a coding
session would be actively unsafe.
"""

from __future__ import annotations

import dataclasses
import shutil
from pathlib import Path


class RepoError(RuntimeError):
    """The repo cannot host a session. Raised, not degraded: proceeding
    would mean an agent writing files somewhere with no version control
    and therefore no way back."""


@dataclasses.dataclass(frozen=True)
class SetupReport:
    repo: str
    copied: tuple[str, ...] = ()
    skipped_existing: tuple[str, ...] = ()
    settings_present: bool = False

    def as_dict(self) -> dict:
        return {
            "repo": self.repo,
            "copied": list(self.copied),
            "skipped_existing": list(self.skipped_existing),
            "settings_present": self.settings_present,
        }


def check_repo(path: str | Path) -> Path:
    """Resolve a repo path, or raise.

    A missing `.git` is refused rather than tolerated. An execution session
    is granted Write and Edit; without version control there is no way to
    see what it did or undo it, and "degrade gracefully" would here mean
    "make an unreviewable change to a stranger's files"."""
    repo = Path(path).expanduser()
    if not repo.is_dir():
        raise RepoError(f"repo path is not a directory: {repo}")
    if not (repo / ".git").exists():
        raise RepoError(f"not a git repository: {repo}")
    return repo


def prepare(repo_path: str | Path, session_type_dir: Path) -> SetupReport:
    """Copy this session type's skills into the repo, non-destructively."""
    repo = check_repo(repo_path)
    source = Path(session_type_dir) / ".claude" / "skills"
    destination = repo / ".claude" / "skills"

    copied: list[str] = []
    skipped: list[str] = []

    if source.is_dir():
        for src in sorted(source.rglob("*")):
            if not src.is_file():
                continue
            relative = src.relative_to(source)
            target = destination / relative
            if target.exists():
                skipped.append(str(relative).replace("\\", "/"))
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
                copied.append(str(relative).replace("\\", "/"))
            except OSError:
                skipped.append(str(relative).replace("\\", "/"))

    return SetupReport(
        repo=str(repo),
        copied=tuple(copied),
        skipped_existing=tuple(skipped),
        # Reported, never touched. Whether this repo constrains what a
        # session may do is the repo's decision to state, not ours to make.
        settings_present=(repo / ".claude" / "settings.json").exists(),
    )
