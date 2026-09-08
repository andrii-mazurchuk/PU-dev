"""Giving a repository access to the queue, without damaging it."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "install_tracker", ROOT / "scripts" / "install_tracker.py"
)
install_tracker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(install_tracker)


def test_a_fresh_repo_gets_all_three(tmp_path, capsys):
    assert install_tracker.install(tmp_path) == 0
    assert (tmp_path / "docs" / "agents" / "issue-tracker.md").exists()
    assert (tmp_path / ".claude" / "skills" / "pu-tasks" / "SKILL.md").exists()
    assert install_tracker.MARKER in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")


def test_nothing_is_ever_overwritten(tmp_path):
    """The repo's own version always wins. This is a guest."""
    tracker = tmp_path / "docs" / "agents" / "issue-tracker.md"
    tracker.parent.mkdir(parents=True)
    tracker.write_text("ours, hand-written", encoding="utf-8")

    install_tracker.install(tmp_path)
    assert tracker.read_text(encoding="utf-8") == "ours, hand-written"


def test_an_existing_claude_md_is_appended_to_never_rewritten(tmp_path):
    claude = tmp_path / "CLAUDE.md"
    claude.write_text("# Their rules\n\nDo not touch this.\n", encoding="utf-8")

    install_tracker.install(tmp_path)
    after = claude.read_text(encoding="utf-8")
    assert after.startswith("# Their rules")
    assert "Do not touch this." in after
    assert install_tracker.MARKER in after


def test_running_twice_changes_nothing_the_second_time(tmp_path):
    install_tracker.install(tmp_path)
    first = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    install_tracker.install(tmp_path)
    second = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert first == second, "the pointer must not accumulate"


def test_dry_run_writes_nothing(tmp_path):
    assert install_tracker.install(tmp_path, dry_run=True) == 0
    assert not (tmp_path / "docs").exists()
    assert not (tmp_path / ".claude").exists()
    assert not (tmp_path / "CLAUDE.md").exists()


def test_a_missing_directory_is_refused(tmp_path):
    assert install_tracker.install(tmp_path / "nope") == 1


def test_the_installed_tracker_names_both_interfaces(tmp_path):
    """The doc is the whole contract for a session outside this unit; if
    it stops explaining the split, nothing else will."""
    install_tracker.install(tmp_path)
    text = (tmp_path / "docs" / "agents" / "issue-tracker.md").read_text(
        encoding="utf-8"
    )
    assert "## Wayfinding operations" in text, "wayfinder looks for this heading"
    for operation in ("+READY -ACTIVE", "parent_uuid:", "task <uuid> start",
                      "annotate", "done", "depends:"):
        assert operation in text, operation
    assert "/body" in text and "text/markdown" in text
    assert "TASKRC" in text
