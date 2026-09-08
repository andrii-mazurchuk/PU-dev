"""Installing the skills this unit ships, without damaging anything."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "install_skill", ROOT / "scripts" / "install_skill.py"
)
install_skill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(install_skill)

OURS = {"pu-tasks", "setup-project"}
VENDORED = {"wayfinder", "grilling"}
FORKED = {"research", "prototype", "domain-modeling"}


def test_every_shipped_skill_is_discovered_from_the_filesystem():
    """Derived from the tree, not a list in code -- adding a skill is
    dropping a directory in, with no second place to update."""
    assert set(install_skill.available()) == OURS | VENDORED | FORKED


def test_the_default_target_is_global_not_per_repo(monkeypatch, tmp_path):
    """One queue and one set of efforts means one copy. Per-repo copies
    would be N copies of one fact, and they drift."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert install_skill.skills_root(None) == tmp_path / ".claude" / "skills"


def test_installing_copies_whole_skill_directories(tmp_path):
    """A skill may carry documents its SKILL.md links to, and half a skill
    is worse than none."""
    assert install_skill.install(tmp_path) == 0
    root = tmp_path / ".claude" / "skills"
    for name in OURS | VENDORED | FORKED:
        assert (root / name / "SKILL.md").exists(), name
    # prototype's two branch documents must have come along
    assert (root / "prototype" / "LOGIC.md").exists()
    assert (root / "prototype" / "UI.md").exists()
    assert (root / "domain-modeling" / "CONTEXT-FORMAT.md").exists()


def test_no_pointer_is_written_into_anyone_s_claude_md(tmp_path):
    """A skill is found by its description. Writing a pointer buys nothing
    and names another unit inside a repo that should not know it exists."""
    claude = tmp_path / "CLAUDE.md"
    claude.write_text("# Their rules\n", encoding="utf-8")
    install_skill.install(tmp_path)
    assert claude.read_text(encoding="utf-8") == "# Their rules\n"


def test_an_existing_skill_is_never_overwritten(tmp_path):
    target = tmp_path / ".claude" / "skills" / "pu-tasks"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("mine, edited", encoding="utf-8")

    assert install_skill.install(tmp_path) == 0

    assert (target / "SKILL.md").read_text(encoding="utf-8") == "mine, edited"
    # ...and the others still went in.
    assert (tmp_path / ".claude" / "skills" / "wayfinder" / "SKILL.md").exists()


def test_only_installs_a_subset(tmp_path):
    assert install_skill.install(tmp_path, only=["grilling"]) == 0
    root = tmp_path / ".claude" / "skills"
    assert [p.name for p in root.iterdir()] == ["grilling"]


def test_an_unknown_name_is_refused(tmp_path):
    assert install_skill.install(tmp_path, only=["nope"]) == 1
    assert not (tmp_path / ".claude").exists()


def test_dry_run_writes_nothing(tmp_path):
    assert install_skill.install(tmp_path, dry_run=True) == 0
    assert not (tmp_path / ".claude").exists()


def test_a_missing_repo_is_refused(tmp_path):
    assert install_skill.install(tmp_path / "nope") == 1


# -- what the skills themselves must keep saying --------------------------


def _skill(name: str) -> str:
    return (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", sorted(OURS | VENDORED | FORKED))
def test_every_skill_has_frontmatter(name):
    text = _skill(name)
    assert text.startswith("---"), name
    assert "\nname:" in text and "\ndescription:" in text, name


def test_pu_tasks_is_the_tracker_doc_wayfinder_looks_for():
    text = _skill("pu-tasks")
    assert "## Wayfinding operations" in text
    for operation in ("+READY -ACTIVE", "parent_uuid:", "task <uuid> start",
                      "annotate", "done", "depends:"):
        assert operation in text, operation
    assert "/body" in text and "text/markdown" in text


def test_wayfinder_is_byte_identical_to_upstream():
    """Vendored unchanged on purpose: anything edited here is something we
    re-merge every time upstream moves. The tracker doc is the graft
    point, so this file never needs touching."""
    text = _skill("wayfinder")
    assert 'Call the Skill tool twice, for "grilling" and "domain-modeling"' in text
    assert "disable-model-invocation: true" in text
    # every skill it names by name must actually be shipped
    for named in ("grilling", "domain-modeling", "research", "prototype"):
        assert (ROOT / "skills" / named / "SKILL.md").exists(), named


def test_the_forks_say_what_they_changed():
    """A fork with no note is a fork nobody can re-merge."""
    for name in FORKED:
        text = _skill(name)
        assert "mattpocock/skills" in text, name
        assert "Adapted from" in text, name


def test_research_resolves_the_ticket_rather_than_writing_a_file():
    text = _skill("research")
    assert "task <uuid> annotate" in text and "task <uuid> done" in text
    assert "no ability to write files" in text


def test_prototype_refuses_to_run_without_somewhere_to_live():
    text = _skill("prototype")
    assert "If there is no repository, stop and say so" in text


def test_domain_modeling_has_no_adrs_and_keeps_the_glossary_out_of_the_graph():
    text = _skill("domain-modeling")
    # The attribution footer says ADRs were removed, so look for the
    # instruction rather than the word.
    assert "Offer ADRs" not in text
    assert "docs/adr" not in text
    assert not (ROOT / "skills" / "domain-modeling" / "ADR-FORMAT.md").exists()
    assert "Not in the spec store" in text


def test_setup_project_refuses_to_run_inside_a_unit():
    """Units are enclosed and swappable; writing a queue pointer into one
    creates the hardcoded dependency that arrangement exists to prevent."""
    text = _skill("setup-project")
    assert "UNIT_CONTRACT.md" in text
    assert "disable-model-invocation: true" in text
    assert "onboard-trust" in text, "must defer verification setup, not duplicate it"
