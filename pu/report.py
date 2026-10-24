"""The report contract: what every session's final message must start with.

The run record *is* the report (pu vision, 2026-10-24). It has two halves,
and they are written by different hands on purpose:

- **The header is measured by pu** -- type and version, agent role, what
  started it, timing, cost, turns, tools, refusals, exit code. An agent
  never reports these: it cannot know them reliably, and could misstate
  them.
- **The body is written by the agent**, in a fixed six-field shape that
  every generated prompt asks for. `OUTCOME` and `NEEDS HUMAN` are read
  back by pu and acted on -- that is what makes the format pay for itself
  rather than being decoration.

Do not grow the format. Andrey: short in text, re-checked over time.
"""

from __future__ import annotations

import dataclasses
import re

OUTCOMES = ("done", "partial", "failed", "nothing-to-do")
FIELDS = ("OUTCOME", "GOAL", "ACTIONS", "REASONING", "EVIDENCE", "NEEDS HUMAN")

# A report longer than this is the agent dumping rather than reporting. The
# rest is cut, marked and flagged, and the raw stream still holds all of it.
# Andrey: see how it performs in practice.
MAX_CHARS = 16_000
TRUNCATION_MARK = "\n\n[report truncated by pu at 16,000 characters]"

CONTRACT = """## Your final message is the report

It is recorded as this run's report. Start it with these six lines, each
field on its own line, then write the rest in whatever form your
instructions ask for:

OUTCOME: done | partial | failed | nothing-to-do
GOAL: <the goal, restated in one line>
ACTIONS: <what you did, side effects first: anything you wrote, sent or created>
REASONING: <why, briefly>
EVIDENCE: <how you know the goal is met>
NEEDS HUMAN: none | <what a person has to do>

`failed` means the goal was not met and another attempt is worth making;
this unit retries once with your report in hand. Use `partial` or
`NEEDS HUMAN` when a person has to look instead.

You may be run more than once for the same work, because delivery is
at-least-once. Check before repeating a side effect."""

_FIELD_LINE = re.compile(
    r"^\s*\**\s*(" + "|".join(re.escape(f) for f in FIELDS) + r")\s*\**\s*:\s*\**\s*(.*)$",
    re.IGNORECASE,
)


@dataclasses.dataclass(frozen=True)
class Report:
    text: str
    truncated: bool
    outcome: str | None
    needs_human: str | None
    structured: bool

    def as_record(self) -> dict:
        return {
            "report": self.text,
            "report_truncated": self.truncated,
            "report_outcome": self.outcome,
            "needs_human": self.needs_human,
            "report_structured": self.structured,
        }


def parse(text: str | None) -> Report:
    """Read the six fields back. Never raises: a malformed body is a run
    marked *unstructured*, which is a signal rather than a failure."""
    raw = (text or "").strip()
    truncated = len(raw) > MAX_CHARS
    kept = raw[:MAX_CHARS] + TRUNCATION_MARK if truncated else raw

    found: dict[str, str] = {}
    for line in raw.splitlines():
        match = _FIELD_LINE.match(line)
        if match:
            key = match.group(1).upper()
            # The first occurrence wins: a later "OUTCOME:" inside the body's
            # prose must not overwrite the header.
            found.setdefault(key, match.group(2).strip())

    outcome = (found.get("OUTCOME") or "").split()[0].lower() \
        if found.get("OUTCOME") else None
    if outcome not in OUTCOMES:
        outcome = None
    needs = found.get("NEEDS HUMAN")
    needs_human = None if not needs or needs.lower().startswith("none") else needs

    return Report(
        text=kept,
        truncated=truncated,
        outcome=outcome,
        needs_human=needs_human,
        structured=outcome is not None and all(f in found for f in FIELDS),
    )

