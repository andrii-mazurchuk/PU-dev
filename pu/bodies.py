"""The body store: one optional markdown blob per task uuid.

Taskwarrior holds a one-line description and timestamped annotations. That
is the right shape for a task and the wrong shape for a wayfinder map,
whose body has five structured sections, or for an inbox message, which is
arbitrary text someone sent.

So a record's long form lives in exactly one of three places, and which one
is a property of the record rather than of its source:

  external  `url` is set -- GitHub owns the text, we hold a pointer
  local     a blob here, keyed by uuid
  absent    one line was enough

Keyed by uuid because uuid is the only permanent identity Taskwarrior has.

The store is deliberately dumb: no history, no versioning. A wayfinder map
body is rewritten in place every time a decision lands, which is what the
skill expects -- the map is an index, and its history is the closed tickets
it points at.
"""

from __future__ import annotations

import re
from pathlib import Path

# Anything that is not a plain uuid is refused. This store is addressed by
# values that arrive over HTTP, so the path is built from untrusted input;
# a strict pattern is what keeps `../` out of it, rather than trusting a
# caller to have validated first.
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                   r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class BodyError(ValueError):
    pass


class BodyStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, uuid: str) -> Path:
        if not _UUID.match(uuid or ""):
            raise BodyError(f"not a uuid: {uuid!r}")
        return self.root / f"{uuid}.md"

    def get(self, uuid: str) -> str | None:
        """The body, or None when there isn't one. A missing body is
        normal -- most tasks are one line."""
        path = self._path(uuid)
        try:
            # newline="" to match the write: what was stored comes back
            # byte for byte, with no platform translation in either
            # direction.
            return path.read_text(encoding="utf-8", newline="")
        except OSError:
            return None

    def set(self, uuid: str, text: str) -> None:
        path = self._path(uuid)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Written via a temporary sibling and replaced, so a reader never
        # observes a half-written map. os.replace is atomic within a
        # filesystem.
        tmp = path.with_suffix(".md.tmp")
        # newline="" stores exactly what was sent. Without it Python
        # rewrites every \n to \r\n on Windows, so a body posted by one
        # tool and read by another comes back subtly different -- and a
        # map that round-trips through git would churn on line endings.
        tmp.write_text(text, encoding="utf-8", newline="")
        tmp.replace(path)

    def delete(self, uuid: str) -> bool:
        path = self._path(uuid)
        try:
            path.unlink()
            return True
        except OSError:
            return False

    def count(self) -> int:
        try:
            return sum(1 for _ in self.root.glob("*.md"))
        except OSError:
            return 0

    def total_bytes(self) -> int:
        try:
            return sum(p.stat().st_size for p in self.root.glob("*.md"))
        except OSError:
            return 0
