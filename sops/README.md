# SOPs

Procedures a task's tags make mandatory. One directory per procedure:

    sops/sop-<tag>/SKILL.md

with frontmatter `name`, `description`, and an optional `kinds:` line
naming which kinds it applies to. A task tagged `<tag>` must follow it;
this is not optional reading, and it is resolved by exact string match —
no fuzzy search, no agent discretion.

Distinct from `.claude/skills/`, which are optional and which the model
reaches for on its own judgement. Hence `sops/`, not `skills/`.

**This directory is deliberately empty.** Zero SOPs is a supported state:
sessions run with none, and `tag_sop_lookup.py --list` says so plainly.
Procedures get added when there is a real one worth binding people to,
not to populate a folder.
