#!/usr/bin/env python3
"""Resolve a task's tag to the SOP it makes mandatory.

    python3 scripts/tag_sop_lookup.py --tag integration
    python3 scripts/tag_sop_lookup.py --list
    python3 scripts/tag_sop_lookup.py --validate-all

Convention: tag `integration` -> `sops/sop-integration/SKILL.md`. Exact
string match; if there is no such file, that tag simply has no procedure.

A session is granted this script specifically, rather than Bash at large,
so that resolving a procedure is something it can always do and reading
the filesystem generally is not.

The logic lives in `pu/sops.py`; this is a CLI over it, so the catalogue a
session sees and the catalogue the unit serves over HTTP can never
disagree.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pu import sops  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="tag -> SOP lookup")
    parser.add_argument("--project-dir", default=str(ROOT),
                        help="unit root holding sops/ (defaults to this repo)")
    parser.add_argument("--tag")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--validate-all", action="store_true")
    args = parser.parse_args()

    root = Path(args.project_dir).resolve()

    if args.list:
        entries = sops.catalogue(root)
        if not entries:
            # Zero SOPs is a supported state, not a problem to report.
            print("No SOPs defined.")
            return 0
        for entry in entries:
            kinds = f" [kinds: {', '.join(entry['kinds'])}]" if entry["kinds"] else ""
            print(f"  {entry['tag']}{kinds}")
            if entry["description"]:
                print(f"      {entry['description']}")
        return 0

    if args.validate_all:
        broken = sops.validate(root)
        if broken:
            print(f"BROKEN: {', '.join(broken)}", file=sys.stderr)
            return 1
        print(f"All {len(sops.catalogue(root))} SOP directories are self-consistent.")
        return 0

    if not args.tag:
        print("ERROR: --tag required (or --list / --validate-all)", file=sys.stderr)
        return 1

    path = sops.resolve(root, args.tag)
    if path is None:
        print(f"No SOP for tag '{args.tag}'.", file=sys.stderr)
        return 1
    print(str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
