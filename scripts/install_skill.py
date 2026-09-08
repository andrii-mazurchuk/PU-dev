#!/usr/bin/env python3
"""Install the skills this unit ships, so sessions anywhere can use them.

    python3 scripts/install_skill.py                  # all, for every session
    python3 scripts/install_skill.py --dry-run
    python3 scripts/install_skill.py --only pu-tasks setup-project
    python3 scripts/install_skill.py --repo <path>    # one repo only

**Globally by default, and that is the design.** Upstream tooling copies
its docs into each repository because each repository has its own issue
tracker. There is one queue here, holding every source, and one set of
efforts — so a per-repo copy would be N copies of one fact, and they would
drift. `--repo` exists for a repository that genuinely needs its own, not
as the normal path.

**No pointer is written into anyone's CLAUDE.md.** A skill is discovered
from its own description, so a pointer buys nothing — and writing one
names another unit inside a repo that has no business knowing that unit
exists. `setup-project` writes a project's own block, deliberately and
with the user watching; this script only copies files.

**Nothing is ever overwritten.** An existing skill is left as it is and
reported, so a local edit survives a re-run. Delete it first to replace
it.

Run by a person: the unit's own process writing into arbitrary directories
is a boundary it should not have.
"""

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = ROOT / "skills"


def available() -> list[str]:
    """Every skill in this unit's `skills/` directory. Derived from the
    filesystem rather than a list here, so adding one is dropping a
    directory in and there is no second place to update."""
    if not SOURCE_DIR.is_dir():
        return []
    return sorted(
        d.name for d in SOURCE_DIR.iterdir()
        if d.is_dir() and (d / "SKILL.md").exists()
    )


def skills_root(repo: Path | None) -> Path:
    """Where skills go: beside a repo's own, or where every session sees
    them."""
    base = repo if repo is not None else Path.home()
    return base / ".claude" / "skills"


def install(
    repo: Path | None = None,
    only: list[str] | None = None,
    dry_run: bool = False,
) -> int:
    names = available()
    if not names:
        print(f"no skills found in {SOURCE_DIR}", file=sys.stderr)
        return 1
    if repo is not None and not repo.is_dir():
        print(f"not a directory: {repo}", file=sys.stderr)
        return 1

    if only:
        unknown = [n for n in only if n not in names]
        if unknown:
            print(f"unknown skill(s): {', '.join(unknown)}", file=sys.stderr)
            print(f"available: {', '.join(names)}", file=sys.stderr)
            return 1
        names = [n for n in names if n in only]

    root = skills_root(repo)
    scope = f"repo {repo}" if repo else "every session"
    installed, skipped = 0, 0

    for name in names:
        target = root / name
        if target.exists():
            print(f"  skipped (exists)  {name}")
            skipped += 1
            continue
        if dry_run:
            print(f"  would install     {name}")
            continue
        # copytree, not one file: a skill may carry supporting documents
        # its SKILL.md links to, and half a skill is worse than none.
        shutil.copytree(SOURCE_DIR / name, target)
        print(f"  installed         {name}")
        installed += 1

    print(f"\n{root}    ({scope})")
    if skipped:
        print(f"{skipped} already present and left alone; delete to replace.")
    if not dry_run and installed:
        print("Point sessions at the schema with the TASKRC path the unit "
              "prints on start.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=None,
                        help="install into one repository instead of globally")
    parser.add_argument("--only", nargs="+", default=None,
                        help="install just these skills")
    parser.add_argument("--dry-run", action="store_true",
                        help="say what would be written, write nothing")
    args = parser.parse_args()
    repo = Path(args.repo).expanduser().resolve() if args.repo else None
    return install(repo, args.only, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
