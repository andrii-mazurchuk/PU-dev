"""What pu watches, and when that counts as something happening.

A trigger decides that work exists; the heartbeat tick is what starts a
session, through the gates. So nothing here launches anything, and every
test below is about a judgement that could have gone the other way.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from pu import triggers

FIXED = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


def at(offset_seconds: float = 0):
    return lambda: FIXED + timedelta(seconds=offset_seconds)


def json_source(items, status=200):
    body = json.dumps(items).encode("utf-8")
    return lambda url: (status, body)


def a_trigger(**overrides):
    record = {
        "name": "posts",
        "source": {"kind": "http-json", "url": "http://example/feed.json"},
        "enabled": True,
    }
    record.update(overrides)
    return record


EMPTY = {"seen": [], "last_new_at": None, "pending": 0, "fired_at": None}


# -- transport status is not data ----------------------------------------


def test_a_failed_fetch_neither_fires_nor_advances():
    """The invariant this ticket lands in the standard. Advancing on a
    failure would silently skip whatever the fetch did not return, and
    firing on one would treat unreachability as content."""
    result = triggers.evaluate(
        a_trigger(), EMPTY, fetch=lambda url: (500, b""), now=at())

    assert result.fired is False
    assert result.advance is False
    assert "status 500" in result.reason
    assert triggers.advanced(EMPTY, result, now=at()) == EMPTY


def test_a_2xx_carrying_something_unreadable_is_also_unknown():
    """Not empty. Concluding "no items" would fire an absence predicate
    against a source that is perfectly healthy and merely changed shape --
    the failure mode is a watch that invents work from a schema change."""
    result = triggers.evaluate(
        a_trigger(predicate=[["age_since_last_seen", ">=", 1]]),
        EMPTY, fetch=lambda url: (200, b"<not json"), now=at())

    assert result.fired is False
    assert result.advance is False
    assert "unreadable" in result.reason


# -- the ordinary case ---------------------------------------------------


def test_new_items_fire_once_and_the_cursor_stops_them_repeating():
    """At-least-once with an advancing cursor is the whole delivery model.
    Without the second half, every tick re-reports the same backlog."""
    items = [{"id": "a", "title": "one"}, {"id": "b", "title": "two"}]
    first = triggers.evaluate(a_trigger(), EMPTY,
                              fetch=json_source(items), now=at())
    assert first.fired is True
    assert len(first.items) == 2

    cursor = triggers.advanced(EMPTY, first, now=at())
    assert sorted(cursor["seen"]) == ["a", "b"]

    second = triggers.evaluate(a_trigger(), cursor,
                               fetch=json_source(items), now=at(60))
    assert second.fired is False
    assert second.advance is True


def test_an_empty_predicate_does_not_fire_against_an_idle_source():
    """`all([])` is true in both directions, so the obvious implementation
    fires every tick forever on a source with nothing new. "No condition"
    means every new item, not every tick."""
    result = triggers.evaluate(a_trigger(), EMPTY,
                               fetch=json_source([]), now=at())

    assert result.fired is False
    assert result.advance is True


# -- the three cases the map says are WATCH with different fields --------


def test_every_third_post_accumulates_across_ticks_and_resets_on_fire():
    """`count_since_fire` has to mean what it says, or this case needs a
    feature instead of a predicate. The counter is in the cursor, so it
    survives the ticks between fires."""
    trigger = a_trigger(predicate=[["count_since_fire", ">=", 3]])
    cursor = EMPTY

    for index, letter in enumerate("ab"):
        result = triggers.evaluate(
            trigger, cursor, fetch=json_source([{"id": letter}]),
            now=at(index))
        assert result.fired is False
        cursor = triggers.advanced(cursor, result, now=at(index))
    assert cursor["pending"] == 2

    result = triggers.evaluate(
        trigger, cursor, fetch=json_source([{"id": "c"}]), now=at(3))
    assert result.fired is True

    cursor = triggers.advanced(cursor, result, now=at(3))
    assert cursor["pending"] == 0
    assert cursor["fired_at"] is not None


def test_absence_fires_on_nothing_new_once_enough_time_has_passed():
    """The one case that must fire with no items at all, which is why a
    predicate of only aggregate clauses is treated differently."""
    trigger = a_trigger(predicate=[["age_since_last_seen", ">=", 3600]])

    seeded = triggers.advanced(
        EMPTY,
        triggers.evaluate(trigger, EMPTY, fetch=json_source([{"id": "a"}]),
                          now=at()),
        now=at())

    soon = triggers.evaluate(trigger, seeded, fetch=json_source([]),
                             now=at(60))
    assert soon.fired is False

    later = triggers.evaluate(trigger, seeded, fetch=json_source([]),
                              now=at(7200))
    assert later.fired is True
    assert later.items == ()


def test_absence_does_not_fire_on_a_source_nobody_has_read_yet():
    """A watch that has never seen an item has no age, and both zero and
    infinity would be a claim. Otherwise every absence watch fires once on
    the tick after it is written."""
    trigger = a_trigger(predicate=[["age_since_last_seen", ">=", 1]])

    result = triggers.evaluate(trigger, EMPTY, fetch=json_source([]), now=at())
    assert result.fired is False


# -- the predicate -------------------------------------------------------


def test_per_item_clauses_filter_and_a_non_match_does_not_fire():
    trigger = a_trigger(predicate=[["title", "contains", "deploy"]])
    items = [{"id": "a", "title": "deploy failed"}, {"id": "b", "title": "tea"}]

    result = triggers.evaluate(trigger, EMPTY, fetch=json_source(items),
                               now=at())
    assert result.fired is True
    assert [i["id"] for i in result.items] == ["a"]

    quiet = triggers.evaluate(trigger, EMPTY,
                              fetch=json_source([{"id": "c", "title": "tea"}]),
                              now=at())
    assert quiet.fired is False


def test_a_dotted_path_reaches_a_nested_field_and_absence_is_not_an_error():
    """A missing field is a clause that does not match, not a broken
    trigger -- a feed that drops a field must not take the watch down."""
    trigger = a_trigger(predicate=[["meta.level", "==", "error"]])
    items = [{"id": "a", "meta": {"level": "error"}},
             {"id": "b"},
             {"id": "c", "meta": "not-an-object"}]

    result = triggers.evaluate(trigger, EMPTY, fetch=json_source(items),
                               now=at())
    assert [i["id"] for i in result.items] == ["a"]


def test_an_ordering_comparison_against_a_non_number_does_not_match():
    """There is no defensible answer to "is 'tea' >= 3", so it is False
    rather than a guess or an exception that strands the watch."""
    assert triggers._compare("tea", ">=", 3) is False
    assert triggers._compare(5, ">=", 3) is True


def test_an_unusable_pattern_is_raised_rather_than_silently_never_matching():
    """A typo in a regex that quietly matched nothing would look exactly
    like a quiet source."""
    with pytest.raises(triggers.TriggerError, match="not a usable pattern"):
        triggers._compare("x", "matches", "[")


# -- the store -----------------------------------------------------------


def test_only_enabled_triggers_are_evaluated(tmp_path):
    """Same reason the flag binds for session types: writing one must not
    be enough to start causing work."""
    store = triggers.TriggerStore(tmp_path)
    store.append(a_trigger(name="live", enabled=True))
    store.append(a_trigger(name="draft", enabled=False))

    assert [t["name"] for t in store.enabled()] == ["live"]


def test_the_cursor_round_trips_and_an_unreadable_one_means_nothing_seen(
        tmp_path):
    """Re-reporting a batch is the recoverable direction; skipping one is
    not. So a corrupt cursor file reads as a fresh watch."""
    store = triggers.TriggerStore(tmp_path)
    store.save_cursor("posts", {"seen": ["a"], "pending": 2,
                                "last_new_at": None, "fired_at": None})
    assert store.cursor("posts")["seen"] == ["a"]

    store.cursors_path.write_text("{ broken", encoding="utf-8")
    assert store.cursor("posts") == EMPTY


def test_the_seen_list_is_bounded(tmp_path):
    """Remembering ids rather than a position is what makes reordering
    safe, and it is only affordable because the list is capped."""
    cursor = EMPTY
    for batch in range(3):
        ids = [f"i{batch}-{n}" for n in range(100)]
        result = triggers.evaluate(
            a_trigger(), cursor,
            fetch=json_source([{"id": i} for i in ids]), now=at(batch))
        cursor = triggers.advanced(cursor, result, now=at(batch))

    assert len(cursor["seen"]) == triggers.SEEN_LIMIT
    # The most recent are the ones kept.
    assert "i2-99" in cursor["seen"]


@pytest.mark.parametrize("record, message", [
    ({"name": "x"}, "missing required field 'source'"),
    ({"name": "x", "source": {"kind": "ftp", "url": "u"}}, "unknown source kind"),
    ({"name": "x", "source": {"kind": "rss"}}, "needs a url"),
    ({"name": "x", "source": {"kind": "rss", "url": "u"},
      "predicate": [["a", "~=", 1]]}, "unknown operator"),
    ({"name": "x", "source": {"kind": "rss", "url": "u"},
      "predicate": [["a", ">="]]}, "[field, operator, value]"),
    ({"name": "x", "source": {"kind": "rss", "url": "u"}, "wat": 1},
     "unknown field"),
])
def test_a_trigger_that_cannot_be_represented_is_refused(
        tmp_path, record, message):
    """A trigger decides that work exists, so a half-understood one would
    invent work nobody asked for."""
    store = triggers.TriggerStore(tmp_path)
    with pytest.raises(triggers.TriggerError, match=message):
        store.append(record)


# -- rss -----------------------------------------------------------------


RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>a feed</title>
  <item><title>one</title><link>http://x/1</link><guid>g1</guid></item>
  <item><title>two</title><link>http://x/2</link><guid>g2</guid></item>
</channel></rss>"""


def test_rss_items_flatten_to_the_same_shape_a_predicate_sees():
    """Deliberately shallow: a predicate over an arbitrary XML tree would
    be a second language, and the channel's own title is not an item."""
    trigger = a_trigger(source={"kind": "rss", "url": "http://x/feed"},
                        predicate=[["title", "==", "two"]])

    result = triggers.evaluate(trigger, EMPTY,
                               fetch=lambda url: (200, RSS), now=at())

    assert result.fired is True
    assert [i["guid"] for i in result.items] == ["g2"]
    assert result.seen_ids == ("g1", "g2")


# -- the schedule kind: the gateway's cron and pings, as one trigger type -


def test_a_schedule_fires_on_its_first_evaluation():
    """Waiting one interval would make "every 24h" mean "nothing for a
    day", and the person who just wrote it is watching for it to work."""
    trigger = a_trigger(source={"kind": "schedule", "every_seconds": 3600})

    result = triggers.evaluate(trigger, EMPTY, now=at())

    assert result.fired is True
    assert result.reason == "due"
    assert result.items == ()


def test_a_schedule_waits_its_interval_after_firing():
    trigger = a_trigger(source={"kind": "schedule", "every_seconds": 3600})

    cursor = triggers.advanced(
        EMPTY, triggers.evaluate(trigger, EMPTY, now=at()), now=at())
    assert cursor["fired_at"] is not None

    assert triggers.evaluate(trigger, cursor, now=at(60)).fired is False
    assert triggers.evaluate(trigger, cursor, now=at(3600)).fired is True


def test_a_schedule_never_fetches_anything():
    """It is a clock, not a source. A fetch here would be a network call
    nobody asked for, and a failing one would stop a schedule that has no
    business depending on the network."""
    def explode(url):
        raise AssertionError("a schedule must not fetch")

    trigger = a_trigger(source={"kind": "schedule", "every_seconds": 1})
    assert triggers.evaluate(trigger, EMPTY, fetch=explode, now=at()).fired


def test_a_schedule_with_a_predicate_is_refused(tmp_path):
    """There are no items and the aggregates never move, so a clause would
    read as a condition and act as a no-op -- worse than a refusal."""
    store = triggers.TriggerStore(tmp_path)

    with pytest.raises(triggers.TriggerError, match="takes no predicate"):
        store.append(a_trigger(
            source={"kind": "schedule", "every_seconds": 60},
            predicate=[["count_since_fire", ">=", 1]]))


def test_a_schedule_needs_a_positive_interval(tmp_path):
    store = triggers.TriggerStore(tmp_path)

    for bad in (None, 0, -5, "soon"):
        with pytest.raises(triggers.TriggerError, match="every_seconds"):
            store.append(a_trigger(
                source={"kind": "schedule", "every_seconds": bad}))


def test_a_watch_still_needs_a_url(tmp_path):
    """The schedule branch must not have loosened validation for the two
    kinds that do fetch."""
    store = triggers.TriggerStore(tmp_path)

    with pytest.raises(triggers.TriggerError, match="needs a url"):
        store.append(a_trigger(source={"kind": "http-json"}))


# -- the emitter: a fired trigger creates a task, and nothing else --------


def emitting(**overrides):
    record = a_trigger(emits={"kind": "execution", "tags": ["digest"],
                              "project": "demo", "afk": True})
    record.update(overrides)
    return record


def test_a_fired_trigger_creates_one_task_carrying_the_payload(
        store, body_store, tmp_path):
    """The whole loop in one assertion: a watch sees something, a task
    exists, and the items are in its body for the session to read."""
    from pu import pipeline

    trigger_store = triggers.TriggerStore(tmp_path)
    trigger_store.append(emitting())

    emitted = pipeline.emit_due_work(
        trigger_store, store, body_store, now=at(),
        fetch=json_source([{"id": "a", "title": "deploy failed"}]))

    assert len(emitted) == 1
    task = store.get(emitted[0]["task_uuid"])
    assert task.kind == "execution"
    assert task.project == "demo"
    assert "digest" in task.tags
    assert task.runnable is True  # afk was asked for

    body = body_store.get(task.uuid)
    assert "deploy failed" in body
    assert "## Payload" in body


def test_firing_twice_creates_two_tasks_and_a_quiet_tick_creates_none(
        store, body_store, tmp_path):
    """At-least-once, with the cursor as the only thing stopping a repeat.
    Two separate events are two tasks -- that is not a duplicate."""
    from pu import pipeline

    trigger_store = triggers.TriggerStore(tmp_path)
    trigger_store.append(emitting())

    first = pipeline.emit_due_work(
        trigger_store, store, body_store, now=at(),
        fetch=json_source([{"id": "a"}]))
    again = pipeline.emit_due_work(
        trigger_store, store, body_store, now=at(10),
        fetch=json_source([{"id": "a"}]))
    second = pipeline.emit_due_work(
        trigger_store, store, body_store, now=at(20),
        fetch=json_source([{"id": "a"}, {"id": "b"}]))

    assert len(first) == 1 and len(second) == 1
    assert again == ()
    assert first[0]["task_uuid"] != second[0]["task_uuid"]
    assert len(store.export("status:pending")) == 2


def test_a_failed_fetch_creates_nothing_and_leaves_the_cursor_alone(
        store, body_store, tmp_path):
    """Transport status is not data, all the way through to the queue."""
    from pu import pipeline

    trigger_store = triggers.TriggerStore(tmp_path)
    trigger_store.append(emitting())

    assert pipeline.emit_due_work(
        trigger_store, store, body_store, now=at(),
        fetch=lambda url: (503, b"")) == ()
    assert store.export("status:pending") == []
    assert trigger_store.cursor("posts")["seen"] == []


def test_one_broken_trigger_does_not_silence_the_others(
        store, body_store, tmp_path):
    """A single unusable regex would otherwise stop every watch in the
    unit, and the symptom is a queue that quietly goes empty."""
    from pu import pipeline

    trigger_store = triggers.TriggerStore(tmp_path)
    trigger_store.append(emitting(name="broken",
                                  predicate=[["title", "matches", "["]]))
    trigger_store.append(emitting(name="fine"))

    emitted = pipeline.emit_due_work(
        trigger_store, store, body_store, now=at(),
        fetch=json_source([{"id": "a", "title": "x"}]))

    by_name = {e["trigger"]: e for e in emitted}
    assert "error" in by_name["broken"]
    assert "task_uuid" in by_name["fine"]


def test_a_disabled_trigger_emits_nothing(store, body_store, tmp_path):
    from pu import pipeline

    trigger_store = triggers.TriggerStore(tmp_path)
    trigger_store.append(emitting(enabled=False))

    assert pipeline.emit_due_work(
        trigger_store, store, body_store, now=at(),
        fetch=json_source([{"id": "a"}])) == ()


def test_a_trigger_emitting_an_unrunnable_kind_is_refused_when_written(
        tmp_path):
    """Validated at write time, not at fire time: a trigger with a typo'd
    kind would otherwise be written happily and fail days later, which is
    the one moment nobody is watching."""
    trigger_store = triggers.TriggerStore(tmp_path)

    with pytest.raises(Exception, match="kind"):
        trigger_store.append(emitting(emits={"kind": "nonsense"}))


def test_a_schedule_emits_a_task_whose_body_states_there_is_no_payload(
        store, body_store, tmp_path):
    """A session that cannot tell "nothing was fetched" from "fetching was
    not part of this" has to guess, and it will guess wrong about whether
    to go and look."""
    from pu import pipeline

    trigger_store = triggers.TriggerStore(tmp_path)
    trigger_store.append(emitting(
        name="nightly", source={"kind": "schedule", "every_seconds": 3600}))

    emitted = pipeline.emit_due_work(
        trigger_store, store, body_store, now=at())

    body = body_store.get(emitted[0]["task_uuid"])
    assert "None. This is a schedule" in body
    assert "nothing failed to arrive" in body
