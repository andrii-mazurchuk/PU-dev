#!/usr/bin/env python3
"""Give a repository access to the pu task queue.

    python3 scripts/install_tracker.py --repo /path/to/repo
    python3 scripts/install_tracker.py --repo /path/to/repo --dry-run

Writes three things into the target repo:

  docs/agents/issue-tracker.md      the operations reference; this is what
                                    `/wayfinder` looks for
  .claude/skills/pu-tasks/SKILL.md  so an ordinary session can consult the
                                    queue without being told to
  CLAUDE.md                         one pointer block, appended

**Run by a person, not by the unit.** pu-the-process writing into arbitrary
repositories is a boundary it should not have; it stretches this only in
`repo_setup`, and only to prepare a workspace for its own session.

**Nothing is ever overwritten.** A file that already exists is left exactly
as it is and reported as skipped. The repo's own version always wins --
this is a guest.
"""

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TRACKER_SOURCE = ROOT / "docs" / "issue-tracker-taskwarrior.md"
TRACKER_TARGET = Path("docs") / "agents" / "issue-tracker.md"
SKILL_SOURCE = ROOT / "skills" / "pu-tasks" / "SKILL.md"
SKILL_TARGET = Path(".claude") / "skills" / "pu-tasks" / "SKILL.md"

MARKER = "## Issue tracker"

POINTER = f"""
{MARKER}

Tasks and specs for this repo live in Taskwarrior, held by the **pu**
processing unit. `docs/agents/issue-tracker.md` is the operations
reference — read it before creating, claiming or resolving anything, and
consult it when a skill refers to "the issue tracker".

Two interfaces: `task` for the task graph, pu's HTTP API for long-form
bodies. Point `TASKRC` at the config pu prints on start, or `kind:` and
`parent_uuid:` will not exist for you.
"""


def install(repo: Path, dry_run: bool = False) -> int:
    if not repo.is_dir():
        print(f"not a directory: {repo}", file=sys.stderr)
        return 1

    copies = [(TRACKER_SOURCE, repo / TRACKER_TARGET),
              (SKILL_SOURCE, repo / SKILL_TARGET)]

    for source, target in copies:
        if not source.exists():
            print(f"missing source: {source}", file=sys.stderr)
            return 1
        rel = target.relative_to(repo)
        if target.exists():
            print(f"  skipped (exists)  {rel}")
            continue
        if dry_run:
            print(f"  would write       {rel}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        print(f"  wrote             {rel}")

    claude_md = repo / "CLAUDE.md"
    existing = claude_md.read_text(encoding="utf-8") if claude_md.exists() else ""
    if MARKER in existing:
        print("  skipped (present) CLAUDE.md pointer")
    elif dry_run:
        print("  would append      CLAUDE.md pointer")
    else:
        # Appended, never rewritten: this file is the repo's own statement
        # of how it works, and editing it around someone would be exactly
        # the sort of thing nobody reviews.
        separator = "" if existing.endswith("\n") or not existing else "\n"
        claude_md.write_text(existing + separator + POINTER, encoding="utf-8")
        print("  appended          CLAUDE.md pointer")

    print(f"\n{repo} can now reach the queue.")
    print("Point sessions at the schema:  export TASKRC=<pu>/state/taskrc")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True)
    parser.add_argument("--dry-run", action="store_true",
                        help="say what would be written, write nothing")
    args = parser.parse_args()
    return install(Path(args.repo).expanduser().resolve(), args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
