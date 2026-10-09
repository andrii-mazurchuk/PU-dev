"""Append-only JSONL logs, where `current` is the last line for a key.

Two of this unit's stores are the same shape -- session types and triggers
-- and the part they must agree on is not the record, it is the reading:

**A line that cannot be parsed stops the read.** Both logs decide whether
something runs and with what permission, so skipping an unreadable line
would mean running under a record nobody can see. The line number is
reported because that is the only thing that makes the file fixable.

**`newline="\\n"`, always.** This unit has been bitten twice by Python
translating `\\n` to `\\r\\n` on Windows in a file read by something that
does not expect it -- see `tests/test_file_formats.py`.

**Last line wins.** History is free in this format and nothing is built on
top of it; that is the whole reason for choosing it.

Validation stays with each store, because what makes a record valid is the
one thing the two genuinely do not share.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator


def lines(path: Path, error: type[Exception]) -> Iterator[dict[str, Any]]:
    """Every record ever written, in order. Raises `error` on a line that
    is not a JSON object, naming the file and line number."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError:
        return
    for number, line in enumerate(raw.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError as exc:
            raise error(f"{path}:{number} is not readable JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise error(f"{path}:{number} is not an object")
        yield parsed


def current(path: Path, error: type[Exception],
            key: str = "name") -> dict[str, dict[str, Any]]:
    """Each key's latest record. Last line wins, which is the whole read
    rule."""
    latest: dict[str, dict[str, Any]] = {}
    for record in lines(path, error):
        value = record.get(key)
        if isinstance(value, str):
            latest[value] = record
    return latest


def append(path: Path, record: dict[str, Any]) -> dict[str, Any]:
    """One line, sorted keys so a diff of this file is readable."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")
    return record
