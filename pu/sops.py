"""SOPs: procedures a task's tags make mandatory.

An SOP is a directory `sops/sop-<tag>/SKILL.md`. The tag *is* the directory
suffix, so the filesystem is the index -- there is no manifest to go stale,
and `validate()` checks the tree against itself rather than against a list
someone has to remember to update.

This is deliberately distinct from a Claude Code skill. A skill is
optional and judgement-triggered; the model decides it is relevant. An SOP
is required by the task's own tag, resolved by exact string match, no
fuzzy search and no agent discretion. Hence `sops/`, not `skills/`.

Selection is a **union**: every tag that resolves contributes a mandatory
procedure, and there is no precedence between them. Nothing here ranks or
picks.

**Zero SOPs is a supported state.** The catalogue ships empty; a session
with no resolving tags proceeds with none.
"""

from __future__ import annotations

from pathlib import Path

SOPS_DIR_NAME = "sops"
PREFIX = "sop-"


def _frontmatter(path: Path) -> dict[str, str]:
    """The YAML frontmatter as flat key -> value.

    Deliberately not a YAML parser: this unit has no dependencies, and the
    frontmatter it needs is two or three scalar fields. Handles inline
    values and folded (`>`) block scalars, which is what the fields
    actually use."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    if not lines or lines[0].strip() != "---":
        return {}
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}

    fields: dict[str, str] = {}
    key: str | None = None
    parts: list[str] = []

    def flush() -> None:
        if key is not None:
            fields[key] = " ".join(parts).strip()

    for line in lines[1:end]:
        if key is not None and line.strip() and line.startswith((" ", "\t")):
            parts.append(line.strip())
            continue
        if ":" in line and not line.startswith((" ", "\t")):
            flush()
            name, _, value = line.partition(":")
            key = name.strip()
            value = value.strip()
            parts = [] if value in (">", "|", ">-", "|-") else [value.strip("\"'")]
    flush()
    return fields


def resolve(root: Path, tag: str) -> Path | None:
    """The SOP file for a tag, or None. Exact match on the directory name;
    no fuzzy search, because a procedure that applies approximately is a
    procedure nobody can rely on."""
    candidate = Path(root) / SOPS_DIR_NAME / f"{PREFIX}{tag}" / "SKILL.md"
    return candidate if candidate.exists() else None


def describe(path: Path) -> tuple[str, tuple[str, ...]]:
    """`description`, and the kinds the procedure applies to.

    `kinds` is advisory and optional. It exists because a procedure for
    implementing code and one for conducting research may both want the
    word `testing`; naming the kinds lets a session skip an SOP that is
    not for the work it is doing. Empty means "applies to any kind"."""
    fields = _frontmatter(path)
    kinds = tuple(
        k.strip() for k in fields.get("kinds", "").replace(",", " ").split() if k.strip()
    )
    return fields.get("description", ""), kinds


def catalogue(root: Path) -> list[dict[str, object]]:
    """Every available SOP, for a model deciding which tags to attach.

    This is what makes the set discoverable rather than folklore -- a
    session or an external skill can ask what procedures exist instead of
    having to already know."""
    sops_dir = Path(root) / SOPS_DIR_NAME
    if not sops_dir.exists():
        return []
    out: list[dict[str, object]] = []
    for d in sorted(sops_dir.iterdir()):
        if not (d.is_dir() and d.name.startswith(PREFIX)):
            continue
        skill = d / "SKILL.md"
        if not skill.exists():
            continue
        description, kinds = describe(skill)
        out.append({
            "tag": d.name[len(PREFIX):],
            "description": description,
            "kinds": list(kinds),
        })
    return out


def match(root: Path, tags: list[str] | tuple[str, ...]) -> tuple[list[str], list[str]]:
    """Split tags into those that name a real SOP and those that do not.

    An unmatched tag is **not an error** -- free-form labelling is useful
    and rejecting it would make tagging brittle. But it is reported,
    because a tag that looks like an SOP and is misspelled means the
    procedure silently does not apply, which is a green path with no
    procedure on it."""
    matched, unmatched = [], []
    for tag in tags:
        (matched if resolve(root, tag) else unmatched).append(tag)
    return matched, unmatched


def validate(root: Path) -> list[str]:
    """Directories that claim to be an SOP and are not usable. Checked
    against the filesystem rather than a fixed list, so it cannot go
    stale the way a hardcoded catalogue would."""
    sops_dir = Path(root) / SOPS_DIR_NAME
    if not sops_dir.exists():
        return []
    broken = []
    for d in sorted(sops_dir.iterdir()):
        if not (d.is_dir() and d.name.startswith(PREFIX)):
            continue
        skill = d / "SKILL.md"
        if not skill.exists():
            broken.append(f"{d.name} (no SKILL.md)")
        elif not describe(skill)[0]:
            broken.append(f"{d.name} (SKILL.md has no parseable description)")
    return broken
