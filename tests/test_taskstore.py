"""The store, against the real binary."""

from __future__ import annotations

import pytest

from pu import records, taskstore


def test_add_returns_a_uuid_and_reads_back(store):
    uuid = store.add("implement the thing", kind="execution", project="dev.pu")
    task = store.get(uuid)
    assert task is not None
    assert task.uuid == uuid
    assert task.description == "implement the thing"
    assert task.kind == "execution"
    assert task.project == "dev.pu"


def test_description_with_reserved_characters_survives(store):
    """A description containing a colon or a leading plus must not be
    reparsed as an attribute or a tag. Without the `--` separator this
    fails as silent data corruption, not as an error."""
    text = "handle +afk and project: prefixes in text"
    uuid = store.add(text, kind="execution")
    assert store.get(uuid).description == text


def test_unknown_kind_is_refused_and_writes_nothing(store):
    before = store.count("status:pending")
    with pytest.raises(records.RecordError):
        store.add("bad", kind="nonsense")
    assert store.count("status:pending") == before


def test_the_binary_still_guards_the_add_command_path(store):
    """`uda.kind.values` is what protects a person or a session writing by
    hand with `task add`."""
    with pytest.raises(taskstore.TaskError) as excinfo:
        store.run(["add", "kind:nonsense", "--", "bad"])
    assert "kind" in str(excinfo.value)
    assert store.count("status:pending") == 0


def test_import_does_not_validate_so_our_own_check_is_the_guard(store):
    """Pinning a real asymmetry rather than assuming symmetry: `task
    import` writes whatever JSON it is given and validates no UDA value.
    Since this unit creates every task through import, records.check_kind
    is the only guard on our own writes -- if that ever gets removed as
    'redundant', this test says why it is not."""
    import json
    import uuid as uuidlib

    store.run(["import"], stdin=json.dumps([{
        "uuid": str(uuidlib.uuid4()), "status": "pending",
        "description": "smuggled", "entry": "20260908T120000Z",
        "kind": "nonsense",
    }]))
    smuggled = store.export("status:pending")[0]
    assert smuggled.kind == "nonsense", "import accepted it, as measured"
    # ...and the record is inert: no session type, so nothing can run it.
    assert smuggled.session_type is None
    assert smuggled.runnable is False

    # The guard that actually applies to us:
    with pytest.raises(records.RecordError):
        store.add("via add", kind="nonsense")


def test_a_multi_line_attribute_is_refused(store):
    """A multi-line key:value exits 0, drops the value, and overwrites the
    description. Refused rather than attempted."""
    uuid = store.add("a task", kind="research")
    with pytest.raises(taskstore.TaskError) as excinfo:
        store.modify(uuid, project="two\nlines")
    assert "multi-line" in str(excinfo.value)
    assert store.get(uuid).description == "a task"


def test_a_created_task_keeps_hostile_text_verbatim(store):
    """Creation goes through stdin, so nothing in the description is
    exposed to a command line."""
    text = "backticks `x`, $(echo NOPE), ${HOME}, colons: and\nnewlines"
    uuid = store.add(text, kind="research")
    assert store.get(uuid).description == text


def test_empty_store_exports_empty_not_error(store):
    assert store.export() == []
    assert store.ready() == []
    assert store.runnable() == []


def test_runnable_requires_the_afk_opt_in(store):
    opted_in = store.add("opted in", tags=["afk"], kind="execution")
    not_opted = store.add("not opted in", kind="execution")
    assert [t.uuid for t in store.runnable()] == [opted_in]
    # ...but both are on the frontier, which is a human's view.
    assert {t.uuid for t in store.ready()} == {opted_in, not_opted}


def test_a_hitl_kind_is_never_runnable_even_when_marked_afk(store):
    """grilling and prototype are human-in-the-loop by definition. Marking
    one afk must not make it selectable -- runnability is the conjunction
    of the opt-in and a kind that has a session type."""
    hitl = store.add("talk it through", tags=["afk"], kind="grilling")
    assert store.runnable() == []
    assert [t.uuid for t in store.ready()] == [hitl]


def test_blocking_removes_a_task_from_the_frontier(store):
    blocker = store.add("first", tags=["afk"], kind="research")
    blocked = store.add("second", tags=["afk"], kind="execution")
    store.modify(blocked, depends=[blocker])

    assert [t.uuid for t in store.runnable()] == [blocker]
    assert store.get(blocked).depends == (blocker,)

    store.complete(blocker)
    assert [t.uuid for t in store.runnable()] == [blocked]


def test_claiming_removes_it_from_the_frontier(store):
    uuid = store.add("work", tags=["afk"], kind="execution")
    store.claim(uuid)
    assert store.get(uuid).active is True
    assert store.runnable() == []
    store.release(uuid)
    assert [t.uuid for t in store.runnable()] == [uuid]


def test_urgency_orders_the_frontier_by_kind(store):
    """Ordering across sources is a coefficient, not a priority ladder in
    code. research outranks execution by configuration alone."""
    execution = store.add("code", tags=["afk"], kind="execution")
    research = store.add("find out", tags=["afk"], kind="research")
    assert [t.uuid for t in store.runnable()] == [research, execution]


def test_annotations_round_trip(store):
    uuid = store.add("thing", kind="execution")
    store.annotate(uuid, "the answer")
    assert "the answer" in store.get(uuid).annotations


def test_get_unknown_uuid_is_none_not_error(store):
    assert store.get("11111111-2222-3333-4444-555555555555") is None


def test_text_survives_the_hop_to_the_binary(store):
    """Regression: `wsl -- cmd` hands the command to the login shell, which
    ate backticks, interpolated `${HOME}` and *executed* `$(...)`. Task
    annotations carry a session's own final message, so that was a path
    from session output to shell execution."""
    hostile = [
        "backticks `repo` here",
        "dollar $(echo PWNED) here",
        "brace ${HOME} here",
        "quotes \"double\" and 'single'",
        "semicolon ; pipe | amp & star *",
    ]
    for text in hostile:
        uuid = store.add(text, kind="execution")
        assert store.get(uuid).description == text, text
        store.annotate(uuid, text)
        assert text in store.get(uuid).annotations, text


def test_base_cmd_forces_exec_over_the_login_shell():
    """`--` is rewritten to `-e` wherever it comes from, because config a
    human writes cannot be relied on to get this right."""
    assert taskstore.base_cmd_from_env({"PU_TASK_CMD": "wsl -d Ubuntu -- task"}) == (
        "wsl", "-d", "Ubuntu", "-e", "task",
    )
    assert taskstore.normalise_base_cmd(["wsl.exe", "--", "task"]) == (
        "wsl.exe", "-e", "task",
    )
    # A plain local binary is left exactly alone.
    assert taskstore.normalise_base_cmd(["task"]) == ("task",)
    assert taskstore.base_cmd_from_env({}) == ("task",)


def test_rendered_taskrc_declares_every_kind():
    rendered = taskstore.render_taskrc("/tmp/x")
    assert "data.location=/tmp/x" in rendered
    for kind in records.KINDS:
        assert kind in rendered
    # The human's own rc must not disable hooks -- the on-add hook is the
    # guard on the by-hand write path.
    assert "hooks=" not in rendered


def test_a_path_is_translated_for_a_binary_behind_wsl():
    """The config file this process writes lives on the Windows side; the
    binary reading it lives in WSL. Handing a session the path we wrote to
    gives it a file it cannot open."""
    wsl = ("wsl", "-d", "Ubuntu", "-e", "task")
    assert taskstore.path_for_binary(
        r"C:\agents\units\pu\state\taskrc", wsl
    ) == "/mnt/c/agents/units/pu/state/taskrc"

    # Natively there is no boundary and nothing is translated.
    assert taskstore.path_for_binary("/home/u/.taskrc", ("task",)) == "/home/u/.taskrc"
