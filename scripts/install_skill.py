#!/usr/bin/env python3
"""Make the pu task queue reachable from sessions outside this unit.

    python3 scripts/install_skill.py                 # for every session
    python3 scripts/install_skill.py --dry-run
    python3 scripts/install_skill.py --repo <path>   # one repo only

Installs one file: `skills/pu-tasks/SKILL.md`, which is both the queue
reference and the tracker doc `/wayfinder` looks for.

**Globally by default, and that is the design.** Upstream tooling copies a
tracker doc into each repository because each has its own issue tracker.
There is one queue here, holding every source — so a per-repo copy would
be N copies of a single fact, and they would drift. `--repo` exists for a
repository that genuinely needs its own, not as the normal path.

**No pointer is written into anyone's CLAUDE.md.** A skill is discovered
from its own description, so the pointer bought nothing — and it named
another unit inside a repo that should not know that unit exists, which is
exactly the coupling this system is arranged to avoid.

**Nothing is ever overwritten.** An existing file is left as it is and
reported. Run by a person: pu-the-process writing into arbitrary
directories is a boundary it should not have.
"""

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "skills" / "pu-tasks" / "SKILL.md"
RELATIVE = Path(".claude") / "skills" / "pu-tasks" / "SKILL.md"


def target_for(repo: Path | None) -> Path:
    """Where the skill goes. A repo install sits beside that repo's own
    skills; a global one is available in every directory."""
    if repo is not None:
        return repo / RELATIVE
    return Path.home() / ".claude" / "skills" / "pu-tasks" / "SKILL.md"


def install(repo: Path | None = None, dry_run: bool = False) -> int:
    if not SOURCE.exists():
        print(f"missing source: {SOURCE}", file=sys.stderr)
        return 1
    if repo is not None and not repo.is_dir():
        print(f"not a directory: {repo}", file=sys.stderr)
        return 1

    target = target_for(repo)
    scope = f"repo {repo}" if repo else "every session"

    if target.exists():
        print(f"skipped (exists)  {target}")
        print("\nDelete it first if you meant to replace it.")
        return 0
    if dry_run:
        print(f"would write       {target}    ({scope})")
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE, target)
    print(f"wrote             {target}    ({scope})")
    print("\nPoint sessions at the schema with the TASKRC path pu prints "
          "on start.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=None,
                        help="install into one repository instead of globally")
    parser.add_argument("--dry-run", action="store_true",
                        help="say what would be written, write nothing")
    args = parser.parse_args()
    repo = Path(args.repo).expanduser().resolve() if args.repo else None
    return install(repo, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
