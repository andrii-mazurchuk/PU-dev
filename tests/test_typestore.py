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

    store.append(record(prompt="one\n"))
    store.append(record(prompt="two\n"))

    generated = (tmp_path / "session_types" / "digest" / "CLAUDE.md")
    text = generated.read_text(encoding="utf-8")
    assert "two" in text
    assert "one" not in text
    assert "Generated from this unit's session-type store" in text


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
