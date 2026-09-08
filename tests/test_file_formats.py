"""Files this unit writes that something else reads.

Both cases here are the same mistake caught twice: Python translates `\n`
to `\r\n` when writing text on Windows, and both of these files are read by
something that does not expect it.
"""

from __future__ import annotations

from pu import bodies, taskstore


def test_the_generated_taskrc_uses_unix_line_endings(tmp_path):
    """Written by whatever platform runs the unit, read by the binary --
    which under the WSL arrangement is Linux.

    Measured, not theorised: with CRLF, Taskwarrior read the carriage
    return as part of the value, so `data.location=/tmp/x` addressed a
    directory named `/tmp/x\\r`. Sessions pointed at this file wrote to a
    store the unit never looks at, and both sides reported success."""
    target = tmp_path / "taskrc"
    target.write_text(
        taskstore.render_taskrc("/tmp/store"), encoding="utf-8", newline="\n"
    )
    raw = target.read_bytes()
    assert b"\r\n" not in raw
    assert b"data.location=/tmp/store\n" in raw


def test_a_body_is_stored_exactly_as_sent(tmp_path):
    """A body is posted by one tool and may be read by another, or land in
    git. It must come back byte for byte, with no translation in either
    direction."""
    store = bodies.BodyStore(tmp_path)
    uuid = "11111111-2222-3333-4444-555555555555"
    text = "## A\n\nline one\nline two\n"

    store.set(uuid, text)

    assert store.get(uuid) == text
    assert b"\r\n" not in (tmp_path / f"{uuid}.md").read_bytes()


def test_a_body_containing_crlf_is_preserved_not_normalised(tmp_path):
    """The converse: if a caller genuinely sends CRLF, that is their text
    and we store it, rather than deciding we know better."""
    store = bodies.BodyStore(tmp_path)
    uuid = "11111111-2222-3333-4444-555555555556"
    text = "line one\r\nline two\r\n"

    store.set(uuid, text)

    assert store.get(uuid) == text
