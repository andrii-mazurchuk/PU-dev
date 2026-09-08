"""Making the queue reachable from outside, without damaging anything."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "install_skill", ROOT / "scripts" / "install_skill.py"
)
install_skill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(install_skill)


def test_the_default_target_is_global_not_per_repo(monkeypatch, tmp_path):
    """One queue means one copy. A per-repo copy would be N copies of a
    single fact, and they would drift."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert install_skill.target_for(None) == (
        tmp_path / ".claude" / "skills" / "pu-tasks" / "SKILL.md"
    )


def test_a_repo_install_goes_beside_that_repo_s_own_skills(tmp_path):
    assert install_skill.target_for(tmp_path) == (
        tmp_path / ".claude" / "skills" / "pu-tasks" / "SKILL.md"
    )


def test_installing_writes_exactly_one_file(tmp_path):
    assert install_skill.install(tmp_path) == 0
    written = tmp_path / ".claude" / "skills" / "pu-tasks" / "SKILL.md"
    assert written.exists()
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == [written]


def test_no_pointer_is_written_into_anyone_s_claude_md(tmp_path):
    """The pointer bought nothing -- a skill is found by its description --
    and it named another unit inside a repo that should not know that unit
    exists."""
    claude = tmp_path / "CLAUDE.md"
    claude.write_text("# Their rules\n", encoding="utf-8")

    install_skill.install(tmp_path)

    assert claude.read_text(encoding="utf-8") == "# Their rules\n"


def test_an_existing_skill_is_never_overwritten(tmp_path):
    target = tmp_path / ".claude" / "skills" / "pu-tasks" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("theirs", encoding="utf-8")

    assert install_skill.install(tmp_path) == 0
    assert target.read_text(encoding="utf-8") == "theirs"


def test_dry_run_writes_nothing(tmp_path):
    assert install_skill.install(tmp_path, dry_run=True) == 0
    assert not (tmp_path / ".claude").exists()


def test_a_missing_repo_is_refused(tmp_path):
    assert install_skill.install(tmp_path / "nope") == 1


def test_the_skill_is_the_tracker_doc_wayfinder_looks_for(tmp_path):
    """It carries both jobs now: the queue reference, and the operations
    section /wayfinder consults. If it stops explaining the split or loses
    that heading, nothing else covers it."""
    install_skill.install(tmp_path)
    text = (tmp_path / ".claude" / "skills" / "pu-tasks" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert text.startswith("---"), "needs frontmatter to be discoverable"
    assert "## Wayfinding operations" in text, "wayfinder looks for this"
    for operation in ("+READY -ACTIVE", "parent_uuid:", "task <uuid> start",
                      "annotate", "done", "depends:"):
        assert operation in text, operation
    assert "/body" in text and "text/markdown" in text
    assert "TASKRC" in text
    # the two hazards a session must not learn the hard way
    assert "kind:execution" in text and "grilling" in text
