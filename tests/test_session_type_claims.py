"""Which session type runs a task, once types can be written at runtime.

Delegating a task to a particular session is the whole point of the store,
and the binding is "session types claim by kind and tag". These tests guard
the direction of that binding and the tie-breaks, not the plumbing.
"""

from __future__ import annotations

import dataclasses

import pytest

from pu import session_types, typestore


@dataclasses.dataclass(frozen=True)
class FakeTask:
    """Only what `for_task` reads. `session_type` is the kind's default, as
    `records.Task` derives it from `SESSION_TYPE_FOR_KIND`."""

    kind: str | None = "execution"
    tags: tuple[str, ...] = ()
    session_type: str | None = "execution"


@pytest.fixture
def types(tmp_path):
    """The four committed directories, plus whatever a test writes."""
    committed = tmp_path / "committed"
    for name in ("ask", "execution", "intake", "research"):
        (committed / name).mkdir(parents=True)
        (committed / name / "CLAUDE.md").write_text("x\n", encoding="utf-8")
    store = typestore.TypeStore(tmp_path / "state")

    def build():
        return session_types.discover(committed, store=store)

    return store, build


def a_record(**overrides):
    record = {
        "name": "digest",
        "purpose": "Summarise the overnight watch.",
        "prompt": "You summarise.\n",
        "grant": "read",
        "origin": "authored",
        "enabled": True,
    }
    record.update(overrides)
    return record


def test_a_type_that_is_not_enabled_claims_nothing(types):
    """`enabled` is the only control there is today, and `discover` is the
    one place it has teeth. Without this, writing a record would be enough
    to start spending money -- which is what the flag exists to prevent."""
    store, build = types
    store.append(a_record(enabled=False,
                          claims={"kind": "execution", "tags": ["digest"]}))

    assert "digest" not in build()
    assert session_types.for_task(
        build(), FakeTask(tags=("digest",))).name == "execution"


def test_a_claiming_type_wins_over_the_kind_s_default(types):
    """The delegation case. Without this the store is write-only: a runtime
    type could exist and no task could ever reach it, because a task's
    session type is derived from its kind alone."""
    store, build = types
    store.append(a_record(claims={"kind": "execution", "tags": ["digest"]}))

    chosen = session_types.for_task(build(), FakeTask(tags=("digest",)))
    assert chosen.name == "digest"
    assert chosen.allowed_tools.startswith("Read")


def test_a_task_without_the_tag_still_gets_the_default(types):
    """A claim narrows; it must not capture the kind. Otherwise writing one
    type would silently re-route every execution task in the queue."""
    store, build = types
    store.append(a_record(claims={"kind": "execution", "tags": ["digest"]}))

    assert session_types.for_task(build(), FakeTask()).name == "execution"


def test_a_claim_without_a_kind_claims_nothing(types):
    """Matching every kind would let one careless record swallow the queue,
    and the symptom would be tasks running under the wrong instructions
    rather than an error."""
    store, build = types
    store.append(a_record(claims={"tags": ["digest"]}))

    assert session_types.for_task(
        build(), FakeTask(tags=("digest",))).name == "execution"


def test_the_most_specific_claim_wins_and_ties_break_by_name(types):
    """A type claiming more tags has stated a narrower case. A tie broken
    arbitrarily would mean one task running under different instructions on
    different ticks, which is the kind of bug that never reproduces."""
    store, build = types
    store.append(a_record(name="broad",
                          claims={"kind": "execution", "tags": ["digest"]}))
    store.append(a_record(name="narrow",
                          claims={"kind": "execution",
                                  "tags": ["digest", "nightly"]}))
    store.append(a_record(name="also-broad",
                          claims={"kind": "execution", "tags": ["digest"]}))

    found = build()
    assert session_types.for_task(
        found, FakeTask(tags=("digest", "nightly"))).name == "narrow"
    # Only the two broad ones match, and `also-broad` sorts first.
    assert session_types.for_task(
        found, FakeTask(tags=("digest",))).name == "also-broad"


def test_a_record_cannot_take_a_committed_type_s_name(types):
    """`discover` resolves the collision in the directory's favour, so such
    a record would be silently inert -- and a silently inert security
    decision is worse than a refused one."""
    store, _ = types

    with pytest.raises(typestore.TypeStoreError, match="committed session type"):
        store.append(a_record(name="execution", grant="repo"))


def test_a_runtime_type_answers_its_properties_from_its_grant(types):
    """The four committed types still answer from the name-keyed maps. A
    record answers from `GRANTS`, and the two sources must not disagree
    about what a grant permits."""
    store, build = types
    store.append(a_record(name="builder", grant="repo", model="opus"))
    store.append(a_record(name="looker", grant="read"))

    found = build()
    assert found["builder"].runs_in_target_repo is True
    assert found["builder"].reaches_peers is True
    assert found["builder"].model == "opus"
    assert found["looker"].runs_in_target_repo is False
    assert found["looker"].reaches_peers is False
    assert found["looker"].model is None


def test_an_empty_grant_reads_as_absence_not_as_an_empty_string(types):
    """`build_argv` emits no `--allowedTools` for a falsy value, which is
    what the committed maps already do by omitting a name. Returning `""`
    here would emit `--allowedTools ""` instead -- a different thing, and
    the open question this deliberately does not answer."""
    store, build = types
    store.append(a_record(name="mute", grant="none"))

    assert build()["mute"].allowed_tools is None


def test_the_generated_directory_is_the_type_s_cwd(types):
    """A runtime type runs in the directory the store generated, which is
    where its `CLAUDE.md` was written -- that directory is the scoping
    enforcement point, and it lives in private storage rather than in this
    repo."""
    store, build = types
    store.append(a_record(name="digest", prompt="be brief\n"))

    chosen = build()["digest"]
    assert chosen.dir == store.dirs / "digest"
    assert "be brief" in (chosen.dir / "CLAUDE.md").read_text(encoding="utf-8")


def test_a_record_appended_by_hand_gets_its_file_generated(tmp_path):
    """Appending a line to the log is a legitimate way to configure this
    unit -- no HTTP, no restart. What that path cannot do is write the
    generated CLAUDE.md, which left a type that claimed tasks and then
    ran with no instructions in a directory that did not exist."""
    from pu import jsonl, session_types, typestore

    store = typestore.TypeStore(tmp_path)
    jsonl.append(store.log, {
        "name": "hand-typed", "purpose": "p", "prompt": "Do the thing.",
        "grant": "read", "origin": "authored", "enabled": True})
    assert not (store.dirs / "hand-typed").exists()

    (tmp_path / "committed").mkdir()
    found = session_types.discover(tmp_path / "committed", store)

    generated = store.dirs / "hand-typed" / "CLAUDE.md"
    assert generated.exists()
    assert "Do the thing." in generated.read_text(encoding="utf-8")
    assert found["hand-typed"].dir == store.dirs / "hand-typed"


def test_a_file_that_is_there_is_never_rewritten_behind_a_reader(tmp_path):
    """Not a reconciler. Rewriting on every read would make a deliberate
    local change impossible to keep for even one tick."""
    from pu import session_types, typestore

    store = typestore.TypeStore(tmp_path)
    store.append({"name": "digest", "purpose": "p", "prompt": "original",
                  "grant": "read", "origin": "authored", "enabled": True})
    target = store.dirs / "digest" / "CLAUDE.md"
    target.write_text("edited by hand\n", encoding="utf-8", newline="\n")

    (tmp_path / "committed").mkdir()
    session_types.discover(tmp_path / "committed", store)

    assert target.read_text(encoding="utf-8") == "edited by hand\n"
