"""The session-type store.

Every test here guards a decision that was argued for and could have gone
the other way, rather than a line of code.
"""

from __future__ import annotations

import json

import pytest

from pu import typestore


def record(**overrides):
    base = {
        "name": "digest",
        "purpose": "Summarise what the watch brought in.",
        "prompt": "You summarise.\n",
        "grant": "read",
        "origin": "authored",
    }
    base.update(overrides)
    return base


def test_a_second_write_appends_and_current_is_the_last_line(tmp_path):
    """The read rule *is* "last line wins". If a write replaced instead of
    appending, the log would stop being the history that makes prompt
    iteration recoverable -- which is the only reason the format is JSONL
    rather than one file per type."""
    store = typestore.TypeStore(tmp_path)

    store.append(record(prompt="first\n"))
    store.append(record(prompt="second\n"))

    lines = store.log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["prompt"] == "first\n"
    assert store.current()["digest"]["prompt"] == "second\n"


def test_the_generated_claude_md_carries_the_record_s_prompt(tmp_path):
    """The record is the source of truth and the file is an artifact. A
    rewrite must land, or a session runs under a prompt the log does not
    show."""
    store = typestore.TypeStore(tmp_path)

    store.append(record(prompt="the first prompt\n"))
    store.append(record(prompt="the second prompt\n"))

    generated = (tmp_path / "session_types" / "digest" / "CLAUDE.md")
    text = generated.read_text(encoding="utf-8")
    assert "Generated from this unit's session-type store" in text

    # The author's prompt is everything below pu's own preamble, and it is
    # checked as the whole of that rather than as a substring of the file:
    # a bare `in` against short prose collides with the preamble's own
    # words, which is how the first version of this test broke when the
    # preamble arrived.
    body = text.rsplit("\n---\n\n", 1)[-1]
    assert body == "the second prompt\n"


def test_the_generated_file_uses_unix_line_endings(tmp_path):
    """Same mistake this unit already made twice: Python translates `\\n`
    to `\\r\\n` when writing text on Windows, and this file is read by
    Claude Code rather than by Python."""
    store = typestore.TypeStore(tmp_path)

    store.append(record(prompt="line one\nline two\n"))

    raw = (tmp_path / "session_types" / "digest" / "CLAUDE.md").read_bytes()
    assert b"\r\n" not in raw


def test_enabled_then_disabled_still_counts_as_ever_enabled(tmp_path):
    """What the first-enable gate asks. Reading only the last line would
    report a type that was enabled and then disabled as never-enabled, and
    it would take the gate a second time -- so the gate would fire twice
    for one type and the notification would be wrong."""
    store = typestore.TypeStore(tmp_path)

    store.append(record(enabled=True))
    store.append(record(enabled=False))

    assert store.current()["digest"]["enabled"] is False
    assert store.has_ever_been_enabled("digest") is True
    assert store.has_ever_been_enabled("never-written") is False


def test_a_declared_type_cannot_request_the_repo_grant(tmp_path):
    """The role-filler convention's most important property. Without this
    refusal, any unit could declare a session type carrying `Bash` and
    receive it, and installing a unit would become installing arbitrary
    execution."""
    store = typestore.TypeStore(tmp_path)

    with pytest.raises(typestore.TypeStoreError, match="granted by a person"):
        store.append(record(origin="declared", grant="repo"))

    # The same grant from the authored origin is fine: a person asked for it.
    store.append(record(origin="authored", grant="repo"))


def test_an_unusable_name_is_refused_rather_than_becoming_a_path(tmp_path):
    """The name is built into a filesystem path from input that arrives
    over HTTP. A dot is legitimate -- a peer-declared type is namespaced by
    its declaring unit -- which is exactly why `..` has to be refused
    explicitly rather than falling out of the pattern."""
    store = typestore.TypeStore(tmp_path)

    for bad in ("../escape", "..", "has space", "Upper", ""):
        with pytest.raises(typestore.TypeStoreError):
            store.append(record(name=bad))

    store.append(record(name="mu-spec.verify-layer"))
    assert "mu-spec.verify-layer" in store.current()


def test_an_unknown_field_or_grant_is_refused(tmp_path):
    """A record decides what tools a session gets. A typo'd field name that
    was silently accepted would read as a default, and the default is the
    thing the typo was trying to change."""
    store = typestore.TypeStore(tmp_path)

    with pytest.raises(typestore.TypeStoreError, match="unknown field"):
        store.append(record(prmopt="typo"))
    with pytest.raises(typestore.TypeStoreError, match="unknown grant"):
        store.append(record(grant="admin"))
    with pytest.raises(typestore.TypeStoreError, match="missing required"):
        store.append({"name": "x", "origin": "authored"})


def test_an_unparseable_line_stops_the_read(tmp_path):
    """Skipping it would mean running a session type under a record nobody
    can see. The log decides tool grants, so a line that cannot be read is
    a stop rather than a shrug."""
    store = typestore.TypeStore(tmp_path)
    store.append(record())
    with store.log.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("{not json\n")

    with pytest.raises(typestore.TypeStoreError, match="not readable JSON"):
        store.current()


def test_the_generated_file_states_the_contract_above_the_author_s_prompt(
        tmp_path):
    """Every committed type states that the final message is the
    resolution; a generated file used to carry only the author's prompt, so
    the one thing each hand-written type teaches its session was the one
    thing a runtime type could not know. The first real runtime session
    spent part of a paid turn proving it."""
    store = typestore.TypeStore(tmp_path)
    store.append(record(prompt="Summarise the payload.\n"))

    text = (tmp_path / "session_types" / "digest" / "CLAUDE.md").read_text(
        encoding="utf-8")

    assert "Your final message is the resolution" in text
    # Above the prompt, and it wins: a prompt saying "call pu_resolve_task"
    # is wrong whatever its author intended.
    assert text.index("final message") < text.index("Summarise the payload")


def test_the_reach_sentence_is_derived_from_the_grant(tmp_path):
    """Derived rather than templated per grant, so a fifth grant inherits
    it instead of needing a paragraph somebody remembers to write."""
    store = typestore.TypeStore(tmp_path)
    store.append(record(name="quiet", grant="read"))
    store.append(record(name="reaching", grant="web"))

    def generated(name):
        return (tmp_path / "session_types" / name / "CLAUDE.md").read_text(
            encoding="utf-8")

    assert "no way back into this unit" in generated("quiet")
    assert "no way back into this unit" not in generated("reaching")
    assert "mcp__mcp-bridge__*" in generated("reaching")
    assert "Do not resolve this task yourself" in generated("reaching")


def test_a_grant_with_no_tools_says_so_rather_than_leaving_it_blank(tmp_path):
    """"Tools granted:" followed by nothing reads as a truncated file."""
    store = typestore.TypeStore(tmp_path)
    store.append(record(name="mute", grant="none"))

    text = (tmp_path / "session_types" / "mute" / "CLAUDE.md").read_text(
        encoding="utf-8")
    assert "Tools granted: none." in text
