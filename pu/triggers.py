"""Triggers: what pu watches, and when that counts as something happening.

A trigger decides that **work exists**. It does not start a session -- the
gateway's heartbeat tick does, through the gates, and that is the only door
in. So nothing here launches anything; evaluation answers a question and
the tick decides what to do with the answer.

**Two source kinds, and they are config rather than handlers.**
`http-json` and `rss`. A plugin interface was rejected: the extension point
is "anything that serves JSON", which needs no registry, no contract and no
versioning -- an exotic source is served as JSON by the user's own script
next to the node.

**The predicate is a flat ANDed comparison list and never a language.**
No OR, no nesting, no arithmetic, no functions. Anything richer wakes the
session and decides in English, because the filter must be cheaper than the
judge. This is the surface that grows one reasonable field at a time until
it is a DSL nobody designed, which is why the revisit ticket on the map
says to split a watch in two before adding OR.

**Transport status is not data.** A predicate is evaluated over fetched
content, never over reachability. A failed fetch is *unknown*: it does not
fire, and it does not advance the cursor. The two are the same rule --
advancing on a failure would silently skip whatever the fetch did not
return.

**Cursors advance on 2xx, and there is no lease.** Lease-and-ack buys
automatic replay of one batch after a rare crash and costs a lease duration
nobody can bound; the revisit ticket records exactly what would reopen it.

**Trigger types are plural, and the tick is not one of them.** The
gateway's cron and interval pings are *one* type; a watch over a source is
another; a push is another. What is singular is *starting a session*: that
happens on a tick, through the gates, whatever the type. A trigger is a
condition, the tick is the clock and the gate, and conflating the two reads
as "the heartbeat is the only trigger", which it is not.

Absence, "every third post" and joins are **the same mechanism with
different fields**, not separate ones:

    absence           age_since_last_seen >= N
    every third post  count_since_fire >= 3
    every N seconds   source kind `schedule`, which fetches nothing
"""

from __future__ import annotations

import dataclasses
import json
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ElementTree
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any, Callable

from pu import jsonl, records

# `schedule` fetches nothing. It is here rather than in a field of its own
# because `source` answers one question -- what does this trigger watch --
# and a clock is a legitimate answer. Keeping it here means one validation
# path and one evaluation entry point instead of two shapes of trigger.
#
# ponytail: an interval, not a cron expression, so "every 24h from whenever
# it last fired" is expressible and "every day at 09:00 Europe/Warsaw" is
# not. The gateway's own static triggers support both (`{type: cron, expr,
# tz}`), so the upgrade path is that syntax rather than a new idea. Add it
# at the first watch that genuinely needs a wall-clock time.
SOURCE_KINDS = ("http-json", "rss", "schedule")

# The whole operator set. Adding one is a decision, not a convenience --
# see the module docstring on why this list is the surface that grows.
OPERATORS = (">=", ">", "==", "!=", "contains", "matches")

# Fields that describe the watch rather than an item. Everything else in a
# clause is a dotted path into one fetched item.
AGGREGATE_FIELDS = ("count_since_fire", "age_since_last_seen")

REQUIRED = ("name", "source")
OPTIONAL = ("purpose", "predicate", "emits", "enabled", "retired")

# ponytail: the cursor remembers the last 200 item ids rather than a
# position, so ordering assumptions cannot bite -- a feed that reorders or
# backfills still reports each item once. The ceiling is a source that
# moves more than 200 items between two ticks, which would re-report the
# overflow. Raise it, or switch to a watermark, if a real feed hits it.
SEEN_LIMIT = 200


class TriggerError(ValueError):
    """A trigger that cannot be represented, or a predicate that cannot be
    evaluated. Raised rather than degraded: a trigger decides that work
    exists, and a half-understood one would invent work nobody asked for."""


@dataclasses.dataclass(frozen=True)
class Evaluation:
    """What one evaluation concluded.

    `advance` is separate from `fired` on purpose. A successful fetch that
    does not fire still advances -- the items were seen. A failed fetch
    does neither, because transport status is not data.
    """

    fired: bool
    reason: str
    items: tuple[dict[str, Any], ...] = ()
    advance: bool = False
    seen_ids: tuple[str, ...] = ()


def validated(record: dict[str, Any]) -> dict[str, Any]:
    """Public because a dry run needs the refusals without the write: the
    whole point of testing a draft is to be told it is unrepresentable
    before it is stored."""
    unknown = set(record) - set(REQUIRED) - set(OPTIONAL)
    if unknown:
        raise TriggerError(
            f"unknown field(s) {sorted(unknown)} "
            f"(allowed: {sorted(REQUIRED + OPTIONAL)})")
    for field in REQUIRED:
        if not record.get(field):
            raise TriggerError(f"missing required field {field!r}")

    name = record["name"]
    if not isinstance(name, str) or not name.strip():
        raise TriggerError(f"not a usable trigger name: {name!r}")

    source = record["source"]
    if not isinstance(source, dict):
        raise TriggerError("source is an object")
    if source.get("kind") not in SOURCE_KINDS:
        raise TriggerError(
            f"unknown source kind {source.get('kind')!r} "
            f"(allowed: {SOURCE_KINDS})")

    if source["kind"] == "schedule":
        if source.get("cron") and source.get("every_seconds"):
            raise TriggerError(
                "a schedule is either an interval or a calendar, not both: "
                "drop every_seconds or drop cron")
        if source.get("cron"):
            parse_cron(source["cron"])
            if source.get("tz"):
                _zone(source["tz"])
        else:
            try:
                if float(source.get("every_seconds") or 0) <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                raise TriggerError(
                    "a schedule needs every_seconds, a positive number, "
                    "or a cron expression") from None
        # Nothing is fetched, so there are no items and no cursor position
        # for a clause to be about: `count_since_fire` is always zero and
        # `age_since_last_seen` never moves. A predicate here would read as
        # a condition and act as a no-op, which is worse than a refusal.
        if record.get("predicate"):
            raise TriggerError(
                "a schedule trigger takes no predicate: it fetches nothing, "
                "so there is nothing for a clause to be evaluated over. The "
                "interval is the whole condition")
    elif not source.get("url"):
        raise TriggerError("a source needs a url")

    emits = record.get("emits")
    if emits is not None:
        if not isinstance(emits, dict):
            raise TriggerError("emits is an object")
        # Validated here rather than at task-creation time: a trigger with
        # an unrunnable kind would otherwise be written happily and fail
        # only when it first fires, which could be days later and is the
        # one moment nobody is watching.
        records.check_kind(emits.get("kind"))
        if not isinstance(emits.get("tags") or [], list):
            raise TriggerError("emits.tags is a list")

    for clause in record.get("predicate") or ():
        if not isinstance(clause, (list, tuple)) or len(clause) != 3:
            raise TriggerError(
                f"a clause is [field, operator, value]; got {clause!r}")
        if clause[1] not in OPERATORS:
            raise TriggerError(
                f"unknown operator {clause[1]!r} (allowed: {OPERATORS})")
    return dict(record)


# -- the calendar ---------------------------------------------------------
#
# "Every day at 08:00" and "every Monday" are not intervals. An interval
# drifts against the clock the moment a tick is late, and no arithmetic
# on seconds expresses "the first of the month". So a schedule may carry
# a cron expression instead, in the gateway's own syntax -- `{cron, tz}`
# -- which was already named as the upgrade path when the interval was
# the only option.
#
# Five fields, `*`, lists, ranges and steps. No `@weekly`, no `L`, no
# `#`: this is a filter over a minute, and the exotic syntax exists for
# cases nobody here has.

_CRON_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))
_CRON_NAMES = {
    "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# A cron can be satisfiable on paper and never occur -- 31 February. Two
# weeks of minutes is the scan budget; anything that has not come round
# by then is treated as never.
_CRON_HORIZON_MINUTES = 14 * 24 * 60


def parse_cron(expression: str) -> tuple[frozenset[int], ...]:
    """`minute hour day-of-month month day-of-week` into five sets.

    Raised rather than degraded, like everything else here: a cron
    nobody can parse would otherwise mean a trigger that silently never
    fires, which looks exactly like a source that went quiet.
    """
    if not isinstance(expression, str):
        raise TriggerError(f"not a cron expression: {expression!r}")
    fields = expression.split()
    if len(fields) != 5:
        raise TriggerError(
            f"a cron expression has five fields -- minute hour day-of-month "
            f"month day-of-week -- and {expression!r} has {len(fields)}")

    out = []
    for field, (low, high) in zip(fields, _CRON_RANGES):
        allowed: set[int] = set()
        for part in field.split(","):
            step = 1
            if "/" in part:
                part, _, raw_step = part.partition("/")
                if not raw_step.isdigit() or int(raw_step) < 1:
                    raise TriggerError(f"not a usable step in {field!r}")
                step = int(raw_step)
            if part in ("*", ""):
                first, last = low, high
            else:
                bounds = part.split("-")
                if len(bounds) > 2:
                    raise TriggerError(f"not a usable range in {field!r}")
                try:
                    first = _cron_number(bounds[0])
                    last = _cron_number(bounds[-1])
                except KeyError:
                    raise TriggerError(
                        f"not a usable value in {field!r}") from None
            if not (low <= first <= high and low <= last <= high) \
                    or first > last:
                raise TriggerError(
                    f"{field!r} is outside {low}-{high}")
            allowed.update(range(first, last + 1, step))
        out.append(frozenset(allowed))
    # Sunday is both 0 and 7 everywhere else; accepted on input above as
    # 0 only, so `7` is refused rather than quietly meaning Monday.
    return tuple(out)


def _cron_number(token: str) -> int:
    token = token.strip().lower()
    if token in _CRON_NAMES:
        return _CRON_NAMES[token]
    if not token.lstrip("-").isdigit():
        raise KeyError(token)
    return int(token)


def _zone(name: str):
    """The timezone a cron is read in. A wall-clock schedule without one
    is a schedule that moves twice a year."""
    try:
        return ZoneInfo(name)
    except Exception as exc:                       # noqa: BLE001
        raise TriggerError(f"not a known timezone {name!r}: {exc}") from None


def cron_matches(fields: tuple[frozenset[int], ...], moment: datetime) -> bool:
    """Whether this minute is one the expression names.

    Day-of-month and day-of-week are ORed when both are restricted,
    which is cron's own rule and the reason `0 9 1 * mon` means "the
    first, and every Monday" rather than "a Monday the first".
    """
    minute, hour, dom, month, dow = fields
    day_ok = (moment.day in dom) if dom != frozenset(range(1, 32)) else True
    week_ok = ((moment.weekday() + 1) % 7) in dow \
        if dow != frozenset(range(0, 7)) else True
    both_restricted = (dom != frozenset(range(1, 32))
                       and dow != frozenset(range(0, 7)))
    when = (day_ok or week_ok) if both_restricted else (day_ok and week_ok)
    return (moment.minute in minute and moment.hour in hour
            and moment.month in month and when)


def cron_due(source: dict[str, Any], cursor: dict[str, Any],
             moment: datetime) -> tuple[bool, str]:
    """Whether a named minute has passed since this last fired.

    **Scanned minute by minute rather than matched against now.** A tick
    is every thirty seconds when everything is well and not at all when
    the host was asleep, so "does this minute match" would silently drop
    the 08:00 run of any morning pu happened to be down for. Walking
    forward from the last fire catches it, once, late -- which for a
    daily digest is the right answer and for nothing here is the wrong
    one.

    With no previous fire there is no window, and a schedule written at
    ten in the morning must not immediately fire its eight o'clock. So
    the baseline is a minute ago: it fires now only if a named minute
    has just gone by.
    """
    fields = parse_cron(source["cron"])
    zone = _zone(source["tz"]) if source.get("tz") else timezone.utc
    now_local = moment.astimezone(zone)

    last = cursor.get("fired_at")
    try:
        since = (datetime.fromisoformat(last).astimezone(zone) if last
                 else now_local - timedelta(minutes=1))
    except ValueError:
        since = now_local - timedelta(minutes=1)

    # From the minute after the last fire, up to and including this one.
    step = since.replace(second=0, microsecond=0) + timedelta(minutes=1)
    scanned = 0
    while step <= now_local and scanned < _CRON_HORIZON_MINUTES:
        if cron_matches(fields, step):
            late = (now_local - step).total_seconds()
            return True, ("due" if late < 120
                          else f"due, and {int(late // 60)}m late")
        step += timedelta(minutes=1)
        scanned += 1
    return False, "not due yet"


# -- reading a source -----------------------------------------------------


def http_fetch(url: str, timeout: float = 10.0) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    except Exception:
        # Any transport failure is one thing to this module: unknown. The
        # distinction between a refused connection, a timeout and a DNS
        # failure would be a distinction the predicate must not see.
        return 0, b""


def dotted(item: Any, path: str) -> Any:
    """`a.b.c` into nested mappings. Absent is None, never an error -- a
    missing field is a clause that does not match, not a broken trigger."""
    value = item
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


# `{dotted.path}` in a title, filled from the item that matched. The
# body already carries every item in full, so this exists for one reason:
# a queue of tasks all called "failed-deploys: fired" cannot be read, and
# the thing that tells them apart is in the payload.
_PLACEHOLDER = re.compile(r"\{([a-zA-Z0-9_.]+)\}")


def fill_title(template: str, item: dict[str, Any] | None) -> str:
    """Substitute the item's fields into a title. One level, no logic.

    **A path that is not there is left as it was written.** The
    alternative -- an empty string -- turns a typo into a title that
    looks deliberate, and this is the one field a person reads in a list
    of thirty. A visible `{titel}` is a bug report.

    Whitespace is collapsed and the result clipped, because a title goes
    into Taskwarrior's description and a feed's summary field can be a
    paragraph.
    """
    def one(match: "re.Match[str]") -> str:
        value = dotted(item or {}, match.group(1))
        return match.group(0) if value is None else str(value)

    return " ".join(_PLACEHOLDER.sub(one, template).split())[:140]


def _json_items(raw: bytes, source: dict[str, Any]) -> list[dict[str, Any]]:
    parsed = json.loads(raw.decode("utf-8"))
    found = dotted(parsed, source["items_path"]) if source.get("items_path") \
        else parsed
    if isinstance(found, dict):
        found = [found]
    if not isinstance(found, list):
        raise TriggerError(
            "a json source must reach a list of items; set items_path")
    return [i for i in found if isinstance(i, dict)]


# RSS and Atom, flattened to the same handful of keys. Deliberately shallow:
# a feed reader is not what this is, and a predicate over a dotted path into
# an arbitrary XML tree would be a second language.
_RSS_FIELDS = ("title", "link", "guid", "id", "description", "summary",
               "pubDate", "updated", "published")


def _rss_items(raw: bytes, source: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise TriggerError(f"source is not parseable XML: {exc}") from exc

    items: list[dict[str, Any]] = []
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag not in ("item", "entry"):
            continue
        flat: dict[str, Any] = {}
        for child in node:
            name = child.tag.rsplit("}", 1)[-1]
            if name in _RSS_FIELDS:
                flat[name] = (child.text or "").strip() or child.get("href", "")
        items.append(flat)
    return items


def read_source(source: dict[str, Any],
                fetch: Callable[[str], tuple[int, bytes]] = http_fetch
                ) -> tuple[bool, list[dict[str, Any]], str]:
    """`(ok, items, reason)`. `ok` is false for anything that is not a 2xx,
    and then the items are empty and nothing may be concluded from them."""
    status, raw = fetch(source["url"])
    if not 200 <= status < 300:
        return False, [], f"fetch did not succeed (status {status})"
    try:
        if source["kind"] == "rss":
            return True, _rss_items(raw, source), "fetched"
        return True, _json_items(raw, source), "fetched"
    except (TriggerError, ValueError, UnicodeDecodeError) as exc:
        # A 2xx carrying something unreadable is *also* unknown rather than
        # empty: concluding "no items" would fire an absence predicate on a
        # source that is actually fine and merely changed shape.
        return False, [], f"fetched but unreadable: {exc}"


def item_id(item: dict[str, Any], source: dict[str, Any]) -> str:
    """An item's identity. Configurable because only the source knows which
    field is stable; the fallback order is what feeds actually use."""
    if source.get("id_path"):
        value = dotted(item, source["id_path"])
    else:
        value = (item.get("guid") or item.get("id") or item.get("link")
                 or item.get("url"))
    # A source with no stable identity falls back to the whole item, which
    # re-reports it whenever any field changes. Worse than an id and better
    # than treating every item as the same one.
    return str(value) if value is not None else json.dumps(item, sort_keys=True)


# -- the predicate --------------------------------------------------------


def _compare(left: Any, operator: str, right: Any) -> bool:
    if operator in (">=", ">"):
        try:
            left, right = float(left), float(right)
        except (TypeError, ValueError):
            # Not a number on one side is not a match. An ordering
            # comparison between a string and a number has no answer worth
            # guessing at.
            return False
        return left >= right if operator == ">=" else left > right
    if operator == "==":
        return left == right
    if operator == "!=":
        return left != right
    if operator == "contains":
        return right is not None and str(right) in str(left or "")
    if operator == "matches":
        try:
            return re.search(str(right), str(left or "")) is not None
        except re.error as exc:
            raise TriggerError(f"not a usable pattern {right!r}: {exc}") from exc
    raise TriggerError(f"unknown operator {operator!r}")


def matching(items: list[dict[str, Any]],
             predicate) -> list[dict[str, Any]]:
    """The items satisfying every per-item clause. An empty predicate
    matches everything, so a trigger with no predicate fires on anything
    new -- which is the obvious reading of "no condition"."""
    clauses = [c for c in (predicate or ())
               if c[0] not in AGGREGATE_FIELDS]
    return [item for item in items
            if all(_compare(dotted(item, f), op, v) for f, op, v in clauses)]


# -- the store, and the cursors -------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TriggerStore:
    """The trigger log, plus the cursor state the watch keeps beside it.

    Two files, and the split is the point. The **log is append-only
    config**, the same discipline as session types: a trigger is written,
    `current` is the last line, and history is free. The **cursors are
    mutable state** -- a position is not a decision anybody wants a history
    of, and appending one per tick would grow a file forever to record
    nothing.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.log = self.root / "triggers.jsonl"
        self.cursors_path = self.root / "cursors.json"

    # -- config ----------------------------------------------------------

    def current(self) -> dict[str, dict[str, Any]]:
        return jsonl.current(self.log, TriggerError)

    def get(self, name: str) -> dict[str, Any] | None:
        return self.current().get(name)

    def enabled(self) -> list[dict[str, Any]]:
        """The triggers a tick should evaluate. `enabled` binds here for
        the same reason it binds in session-type discovery: writing one
        must not be enough to start causing work."""
        return [record for _, record in sorted(self.current().items())
                if record.get("enabled")]

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        return jsonl.append(self.log, validated(record))

    # -- cursors ---------------------------------------------------------

    def cursors(self) -> dict[str, dict[str, Any]]:
        try:
            parsed = json.loads(self.cursors_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # Absent or unreadable both mean "nothing seen yet", which is
            # the honest starting state and re-reports rather than skips.
            # The other direction would silently swallow a backlog.
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def cursor(self, name: str) -> dict[str, Any]:
        return self.cursors().get(name) or {
            "seen": [], "last_new_at": None, "pending": 0, "fired_at": None,
        }

    def save_cursor(self, name: str, cursor: dict[str, Any]) -> None:
        state = self.cursors()
        state[name] = cursor
        self.root.mkdir(parents=True, exist_ok=True)
        # Written whole and replaced, rather than edited in place: a crash
        # mid-write leaves the previous file, and re-reporting a batch is
        # the recoverable direction.
        temporary = self.cursors_path.with_suffix(".json.writing")
        temporary.write_text(
            json.dumps(state, indent=1, sort_keys=True),
            encoding="utf-8", newline="\n")
        temporary.replace(self.cursors_path)


def evaluate(trigger: dict[str, Any], cursor: dict[str, Any],
             fetch: Callable[[str], tuple[int, bytes]] = http_fetch,
             now: Callable[[], datetime] = _now) -> Evaluation:
    """Whether this trigger says work exists, and what it saw.

    Pure with respect to the store: it is handed a cursor and returns what
    the next one should be built from, so a tick can decide not to advance.
    """
    source = trigger["source"]

    if source["kind"] == "schedule":
        # The clock is the condition, so there is no fetch that could fail
        # and nothing to be unknown about.
        if source.get("cron"):
            fired, reason = cron_due(source, cursor, now())
            return Evaluation(fired=fired, reason=reason, advance=True)
        # An interval, where a trigger that has never fired fires on its
        # first evaluation rather than waiting: waiting would make "every
        # 24h" mean "nothing for a day", and the person who wrote it is
        # watching for it to work. A calendar is the opposite case and is
        # handled as such above -- "every day at 08:00" written at ten in
        # the morning must not fire at ten.
        due = _age_seconds(cursor.get("fired_at"), now())
        fired = due is None or due >= float(source["every_seconds"])
        return Evaluation(
            fired=fired,
            reason="due" if fired else "not due yet",
            advance=True,
        )

    ok, items, reason = read_source(source, fetch)
    if not ok:
        # Unknown, not empty. No fire, no advance -- and the reason is
        # carried so a tick can say why rather than looking idle.
        return Evaluation(fired=False, reason=reason)

    seen = set(cursor.get("seen") or ())
    fresh = [i for i in items if item_id(i, source) not in seen]
    matched = matching(fresh, trigger.get("predicate"))

    pending = int(cursor.get("pending") or 0) + len(matched)
    moment = now()
    last_new_at = cursor.get("last_new_at")
    if fresh:
        last_new_at = moment.isoformat()

    aggregates = {
        "count_since_fire": pending,
        "age_since_last_seen": _age_seconds(last_new_at, moment),
    }
    predicate = list(trigger.get("predicate") or ())
    aggregate_clauses = [c for c in predicate if c[0] in AGGREGATE_FIELDS]
    per_item_clauses = [c for c in predicate if c[0] not in AGGREGATE_FIELDS]

    holds = all(_compare(aggregates[field], operator, value)
                for field, operator, value in aggregate_clauses)

    # **A watch with only aggregate clauses may fire on nothing new** --
    # that is exactly what an absence watch is, and `age_since_last_seen`
    # is meaningless otherwise. Anything else needs a matched item: with no
    # predicate at all, "no condition" means every new item and not every
    # tick, and `all([])` being true in both directions would otherwise
    # make an empty predicate fire forever against an idle source.
    fired = holds and (
        bool(matched)
        or (bool(aggregate_clauses) and not per_item_clauses)
    )

    return Evaluation(
        fired=fired,
        reason="fired" if fired else "no condition met",
        items=tuple(matched),
        advance=True,
        seen_ids=tuple(item_id(i, source) for i in items),
    )


def _age_seconds(last_new_at: str | None, moment: datetime) -> float | None:
    """Seconds since anything new was seen, or None when nothing ever has.

    None rather than zero or infinity: a watch that has never seen an item
    has no age, and either number would be a claim. `_compare` answers
    False for None on every ordering operator, so an absence predicate does
    not fire on a source nobody has read yet.
    """
    if not last_new_at:
        return None
    try:
        then = datetime.fromisoformat(last_new_at)
    except ValueError:
        return None
    return (moment - then).total_seconds()


def advanced(cursor: dict[str, Any], evaluation: Evaluation,
             now: Callable[[], datetime] = _now) -> dict[str, Any]:
    """The cursor after an evaluation. Identity when it must not advance.

    Every fetched id is marked seen, not just the matched ones: an item the
    predicate rejected has still been looked at, and leaving it unseen
    would re-count it on every tick forever.
    """
    if not evaluation.advance:
        return cursor

    moment = now()
    seen = list(dict.fromkeys(list(cursor.get("seen") or ())
                              + list(evaluation.seen_ids)))
    fresh = [i for i in evaluation.seen_ids
             if i not in set(cursor.get("seen") or ())]
    return {
        "seen": seen[-SEEN_LIMIT:],
        "last_new_at": (moment.isoformat() if fresh
                        else cursor.get("last_new_at")),
        # Reset on fire: `count_since_fire` means what it says, which is
        # what makes "every third post" a predicate rather than a feature.
        "pending": 0 if evaluation.fired else int(
            cursor.get("pending") or 0) + len(evaluation.items),
        "fired_at": moment.isoformat() if evaluation.fired
        else cursor.get("fired_at"),
    }
