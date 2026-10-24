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
        trigger_store, store, body_store, now=at(400),
        fetch=json_source([{"id": "a"}]))
    second = pipeline.emit_due_work(
        trigger_store, store, body_store, now=at(800),
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
        fetch=lambda url: (500, b"")) == ()
    assert store.export("status:pending") == []
    assert trigger_store.cursor("posts")["seen"] == []


def test_a_watch_fetches_at_most_every_interval(store, body_store, tmp_path):
    """2,880 fetches a day per watch at a 30s heartbeat is how a source
    starts refusing. Default five minutes, or the trigger's own."""
    from pu import pipeline

    calls = []

    def counting(url):
        calls.append(url)
        return 200, b"[]"

    trigger_store = triggers.TriggerStore(tmp_path)
    trigger_store.append(emitting())
    for offset in (0, 30, 60, 290):
        pipeline.emit_due_work(trigger_store, store, body_store,
                               now=at(offset), fetch=counting)
    assert len(calls) == 1
    pipeline.emit_due_work(trigger_store, store, body_store,
                           now=at(301), fetch=counting)
    assert len(calls) == 2


def test_a_refusing_source_is_backed_off_and_a_success_resets_it(
        store, body_store, tmp_path):
    from pu import pipeline

    trigger_store = triggers.TriggerStore(tmp_path)
    trigger_store.append(emitting(source={
        "kind": "http-json", "url": "http://example/feed.json",
        "every_seconds": 30}))

    told = pipeline.emit_due_work(trigger_store, store, body_store, now=at(),
                                  fetch=lambda url: (429, b"", "600"))
    assert "backing off" in told[0]["error"]
    # Retry-After honoured: nothing is fetched inside the ten minutes.
    calls = []
    pipeline.emit_due_work(trigger_store, store, body_store, now=at(300),
                           fetch=lambda url: calls.append(url) or (200, b"[]"))
    assert calls == []
    pipeline.emit_due_work(trigger_store, store, body_store, now=at(601),
                           fetch=lambda url: calls.append(url) or (200, b"[]"))
    assert len(calls) == 1
    assert "backoff_until" not in trigger_store.cursor("posts")


def test_without_retry_after_the_wait_doubles_up_to_an_hour():
    moment = FIXED
    cursor = triggers.after_fetch({}, moment, 503)
    assert cursor["backoff_seconds"] == 60
    cursor = triggers.after_fetch(cursor, moment, 503)
    assert cursor["backoff_seconds"] == 120
    for _ in range(10):
        cursor = triggers.after_fetch(cursor, moment, 503)
    assert cursor["backoff_seconds"] == triggers.MAX_BACKOFF_SECONDS


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


def test_a_cron_schedule_emits_once_and_names_its_cadence(
        store, body_store, tmp_path):
    """A cron schedule has no every_seconds. Reading it raised after the
    task was created and before the cursor advanced, so every tick made
    another task."""
    from pu import pipeline

    trigger_store = triggers.TriggerStore(tmp_path)
    trigger_store.append(emitting(
        name="morning",
        source={"kind": "schedule", "cron": "0 9 * * *", "tz": "Europe/Warsaw"}))

    # 07:00Z is 09:00 in Warsaw: the slot it names.
    slot = lambda: datetime(2026, 10, 12, 7, 0, tzinfo=timezone.utc)
    first = pipeline.emit_due_work(trigger_store, store, body_store, now=slot)
    created = [e for e in first if "task_uuid" in e]
    assert len(created) == 1, first
    assert "`0 9 * * *` (Europe/Warsaw)" in body_store.get(
        created[0]["task_uuid"])
    again = pipeline.emit_due_work(trigger_store, store, body_store, now=slot)
    assert not [e for e in again if "task_uuid" in e], "fired twice for one slot"
    assert not [e for e in first + again if "error" in e]


# -- the dry run ----------------------------------------------------------
#
# The one object here whose correctness cannot be read off its own record:
# whether a predicate holds depends on what the source is serving this
# minute. So the test that matters is that pressing the button changes
# nothing -- a preview with a side effect is a worse tool than no preview.


def test_a_dry_run_reports_what_would_happen_and_writes_nothing(
        tmp_path, monkeypatch):
    from pu import service

    monkeypatch.setattr(
        triggers, "http_fetch",
        json_source([{"id": "a", "level": "error", "title": "deploy failed"},
                     {"id": "b", "level": "info", "title": "fine"}]))

    trigger_store = triggers.TriggerStore(tmp_path)
    draft = emitting(predicate=[["level", "==", "error"]])

    result = service.dry_run_trigger(trigger_store, draft)

    assert result["ok"] is True
    assert result["fired"] is True
    assert (result["fetched"], result["fresh"], result["matched_total"]) \
        == (2, 2, 1)
    assert result["matched"][0]["title"] == "deploy failed"

    # The task it would create, down to the afk tag the emits asked for.
    created = result["would_create"]
    assert created["kind"] == "execution"
    assert "digest" in created["tags"] and "afk" in created["tags"]
    assert "deploy failed" in created["body"]

    # Nothing written: no record, and no cursor to re-report against.
    assert trigger_store.current() == {}
    assert trigger_store.cursors() == {}

    # And therefore repeatable: the same answer, not "nothing new".
    again = service.dry_run_trigger(trigger_store, draft)
    assert (again["fired"], again["fresh"]) == (True, 2)


def test_a_dry_run_of_an_unrepresentable_draft_is_refused(tmp_path):
    from pu import service

    with pytest.raises(triggers.TriggerError):
        service.dry_run_trigger(
            triggers.TriggerStore(tmp_path),
            a_trigger(source={"kind": "schedule", "every_seconds": 60},
                      predicate=[["level", "==", "error"]]))


def test_a_dry_run_that_does_not_fire_previews_no_task(tmp_path, monkeypatch):
    from pu import service

    monkeypatch.setattr(triggers, "http_fetch",
                        json_source([{"id": "a", "level": "info"}]))
    result = service.dry_run_trigger(
        triggers.TriggerStore(tmp_path),
        emitting(predicate=[["level", "==", "error"]]))

    assert result["fired"] is False
    assert result["would_create"] is None
    assert result["fetched"] == 1 and result["matched_total"] == 0


def test_a_dry_run_states_a_failed_fetch_rather_than_an_empty_source(
        tmp_path, monkeypatch):
    """`ok` false, and the reason says so. An empty source and an
    unreachable one produce the same item count and must not read the
    same, or a down feed looks like a quiet one."""
    from pu import service

    monkeypatch.setattr(triggers, "http_fetch", json_source([], status=500))
    result = service.dry_run_trigger(triggers.TriggerStore(tmp_path),
                                     emitting())

    assert result["ok"] is False
    assert "500" in result["reason"]
    assert result["fired"] is False and result["would_create"] is None


def test_a_dry_run_uses_the_saved_cursor_without_moving_it(
        tmp_path, monkeypatch):
    """A saved trigger's aggregates are the real ones -- an item already
    seen is not new -- but reading them must not advance anything."""
    from pu import service

    monkeypatch.setattr(triggers, "http_fetch",
                        json_source([{"id": "a", "level": "error"}]))
    trigger_store = triggers.TriggerStore(tmp_path)
    draft = emitting(predicate=[["level", "==", "error"]])
    trigger_store.append(draft)
    trigger_store.save_cursor("posts", dict(EMPTY, seen=["a"]))

    result = service.dry_run_trigger(trigger_store, draft)

    assert result["fresh"] == 0 and result["fired"] is False
    assert trigger_store.cursors()["posts"]["seen"] == ["a"]


# -- the calendar ---------------------------------------------------------
#
# An interval cannot say "every morning at eight", and it drifts against
# the clock the moment a tick is late. So a schedule may carry a cron
# expression instead. The tests that matter are the two the interval gets
# wrong: a named minute that has already passed, and a schedule written
# after today's slot.


def cron(expression, tz="Europe/Warsaw", **overrides):
    record = a_trigger(source={"kind": "schedule", "cron": expression,
                               "tz": tz})
    record.update(overrides)
    return record


def utc(year, month, day, hour, minute=0):
    return lambda: datetime(year, month, day, hour, minute,
                            tzinfo=timezone.utc)


def test_a_calendar_schedule_fires_on_the_minute_it_names():
    # 06:00Z is 08:00 in Warsaw.
    out = triggers.evaluate(cron("0 8 * * *"), EMPTY, now=utc(2026, 10, 12, 6))
    assert out.fired is True and out.advance is True


def test_a_calendar_schedule_written_after_its_slot_waits_for_the_next():
    """The one thing an interval gets backwards. `every 24h` fires
    immediately because the person is watching for it; `every day at
    08:00` written at ten in the morning must not fire at ten."""
    assert triggers.evaluate(
        cron("0 8 * * *"), EMPTY, now=utc(2026, 10, 12, 8)).fired is False


def test_a_missed_slot_still_fires_once_late():
    """pu is not running when the host is asleep. A schedule matched
    against the current minute would drop every slot it was down for;
    walking forward from the last fire catches it, once."""
    out = triggers.evaluate(
        cron("0 8 * * *"), dict(EMPTY, fired_at="2026-10-11T06:00:00+00:00"),
        now=utc(2026, 10, 12, 10))
    assert out.fired is True
    assert "late" in out.reason


def test_a_calendar_schedule_does_not_fire_twice_for_one_slot():
    fired_at = "2026-10-12T06:00:00+00:00"
    assert triggers.evaluate(
        cron("0 8 * * *"), dict(EMPTY, fired_at=fired_at),
        now=utc(2026, 10, 12, 6, 30)).fired is False


def test_a_weekday_schedule_only_fires_on_that_weekday():
    # 2026-10-12 is a Monday, 2026-10-13 a Tuesday. 07:00Z is 09:00 Warsaw.
    monday = triggers.evaluate(cron("0 9 * * mon"), EMPTY,
                               now=utc(2026, 10, 12, 7))
    tuesday = triggers.evaluate(cron("0 9 * * mon"), EMPTY,
                                now=utc(2026, 10, 13, 7))
    assert monday.fired is True and tuesday.fired is False


def test_the_timezone_is_the_one_written_not_the_hosts():
    """A wall-clock schedule without a zone moves twice a year. 06:00Z
    is 08:00 in Warsaw and 07:00 in London."""
    assert triggers.evaluate(cron("0 8 * * *", tz="Europe/Warsaw"), EMPTY,
                             now=utc(2026, 10, 12, 6)).fired is True
    assert triggers.evaluate(cron("0 8 * * *", tz="Europe/London"), EMPTY,
                             now=utc(2026, 10, 12, 6)).fired is False


def test_day_of_month_and_day_of_week_are_ored():
    """Cron's own rule, and the reason `0 9 1 * mon` means the first and
    every Monday rather than a Monday that is the first."""
    fields = triggers.parse_cron("0 9 1 * mon")
    first_a_thursday = datetime(2026, 10, 1, 9, 0)
    a_monday = datetime(2026, 10, 12, 9, 0)
    a_plain_tuesday = datetime(2026, 10, 13, 9, 0)
    assert triggers.cron_matches(fields, first_a_thursday) is True
    assert triggers.cron_matches(fields, a_monday) is True
    assert triggers.cron_matches(fields, a_plain_tuesday) is False


@pytest.mark.parametrize("expression", [
    "0 8 * *", "60 8 * * *", "0 25 * * *", "0 8 * * funday",
    "*/0 8 * * *", "9-5 8 * * *",
])
def test_an_unparseable_cron_is_refused_rather_than_never_firing(expression):
    with pytest.raises(triggers.TriggerError):
        triggers.parse_cron(expression)


def test_a_schedule_is_an_interval_or_a_calendar_but_not_both(tmp_path):
    store = triggers.TriggerStore(tmp_path)
    with pytest.raises(triggers.TriggerError):
        store.append(a_trigger(source={"kind": "schedule", "cron": "0 8 * * *",
                                       "every_seconds": 3600}))


def test_an_unknown_timezone_is_refused_at_the_door(tmp_path):
    store = triggers.TriggerStore(tmp_path)
    with pytest.raises(triggers.TriggerError):
        store.append(cron("0 8 * * *", tz="Mars/Olympus"))


# -- the title the task gets ----------------------------------------------


def test_a_title_takes_fields_from_the_item_that_matched():
    assert triggers.fill_title(
        "deploy failed: {title}", {"title": "migration 0042"}
    ) == "deploy failed: migration 0042"


def test_a_path_that_is_not_there_stays_visible():
    """An empty string would turn a typo into a title that looks
    deliberate, in the one field a person reads in a list of thirty."""
    assert triggers.fill_title("x {titel}", {"title": "y"}) == "x {titel}"


def test_a_title_is_collapsed_and_clipped():
    long = triggers.fill_title("{summary}", {"summary": "a\n  b" + "c" * 300})
    assert "\n" not in long and len(long) <= 140
    assert long.startswith("a bc")


def test_a_fired_trigger_titles_its_task_from_the_payload(
        store, body_store, tmp_path):
    from pu import pipeline

    trigger_store = triggers.TriggerStore(tmp_path)
    trigger_store.append(emitting(
        emits={"kind": "execution", "tags": [], "afk": True,
               "title": "fix: {title}"}))

    emitted = pipeline.emit_due_work(
        trigger_store, store, body_store, now=at(),
        fetch=json_source([{"id": "a", "title": "the nightly build"}]))

    assert store.get(emitted[0]["task_uuid"]).description \
        == "fix: the nightly build"


# -- renaming -------------------------------------------------------------


def test_renaming_a_trigger_keeps_its_cursor(tmp_path):
    """The one thing that must move with it. An empty `seen` list would
    re-report every item the source is still serving, which for a watch
    emitting agent-runnable work is a pile of paid sessions for things
    already dealt with."""
    from pu import service

    store = triggers.TriggerStore(tmp_path)
    store.append(emitting(enabled=True))
    store.save_cursor("posts", dict(EMPTY, seen=["a", "b"], pending=2))

    written = service.rename_trigger(store, "posts", "deploy-watch")

    assert written["name"] == "deploy-watch"
    assert store.cursor("deploy-watch")["seen"] == ["a", "b"]
    # Still live: a name changes nothing about what it does, so the edit
    # rule has nothing to protect against here.
    assert written["enabled"] is True


def test_a_renamed_trigger_leaves_the_old_name_retired_not_deleted(tmp_path):
    from pu import service

    store = triggers.TriggerStore(tmp_path)
    store.append(emitting())
    service.rename_trigger(store, "posts", "deploy-watch")

    assert store.get("posts")["retired"] is True
    assert store.get("posts")["enabled"] is False
    # And gone from what anyone reads.
    listed = [t["name"] for t in service.list_triggers(store)["triggers"]]
    assert listed == ["deploy-watch"]


def test_a_retired_trigger_is_never_evaluated(tmp_path):
    from pu import service

    store = triggers.TriggerStore(tmp_path)
    store.append(emitting(enabled=True))
    service.rename_trigger(store, "posts", "deploy-watch")

    assert [t["name"] for t in store.enabled()] == ["deploy-watch"]


def test_a_rename_may_not_write_over_an_existing_name(tmp_path):
    from pu import service

    store = triggers.TriggerStore(tmp_path)
    store.append(emitting())
    store.append(a_trigger(name="other"))

    with pytest.raises(service.ServiceError):
        service.rename_trigger(store, "posts", "other")
    with pytest.raises(service.ServiceError):
        service.rename_trigger(store, "nobody", "whatever")


def test_a_retired_name_can_be_taken_again(tmp_path):
    """Otherwise every name ever used is spent forever -- including the
    one you renamed away from and want back thirty seconds later. The
    cursor goes with the rename, so the name does not come back carrying
    a stale `seen` list."""
    from pu import service

    store = triggers.TriggerStore(tmp_path)
    store.append(emitting(enabled=True))
    store.save_cursor("posts", dict(EMPTY, seen=["a"]))

    service.rename_trigger(store, "posts", "watch")
    back = service.rename_trigger(store, "watch", "posts")

    assert back["name"] == "posts" and back["enabled"] is True
    assert store.get("posts").get("retired") is not True
    assert [t["name"] for t in service.list_triggers(store)["triggers"]] \
        == ["posts"]
    assert store.cursor("posts")["seen"] == ["a"]
