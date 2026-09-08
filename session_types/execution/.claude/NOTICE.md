# What execution sessions carry into a target repository

Everything under `.claude/skills/` here is copied into
`<target repo>/.claude/skills/` by `repo_setup.prepare` at the start of
every execution tick — never overwriting, so the repo's own version of
anything always wins.

**This file is deliberately outside that tree**, so it does not travel. It
is pu's own reasoning, and a repository pu happens to work in has no
business knowing this unit exists. What travels is the skill and its
licence, nothing else.

Distinct from the repository-root `skills/`, which holds what
`scripts/install_skill.py` installs **globally** for interactive sessions.
Different consumers, different copyright holders, separate notices.

## What ships

| skill | upstream | taken |
|---|---|---|
| `ponytail` | [DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail), v4.9.0, commit `2ed6c52`, MIT | byte-identical, sha256 `46a57e26a2632e7f…` |

Vendored unchanged for the same reason the other vendored skills are:
anything changed here has to be re-merged every time upstream moves. Its
`LICENSE` sits beside it inside the skill directory so that it travels
with every copy, which is what MIT asks for.

## Why project-scoped, rather than installed globally

An execution session runs with the target repository as its cwd, so a
skill living in this unit's own tree does not load at all. It has to be in
the repo. Per repo rather than machine-wide because this is a property of
how pu runs code somewhere, not of the host.

## Why this skill

An execution session is granted `Bash`, `Read`, `Write` and `Edit` in a
repository it does not own, working from a one-line task with nobody
watching. The two failure modes that costs are building more than the task
asked for, and shrinking a change before understanding it. Ponytail
addresses both, and its own rules — understand the problem first, the
smallest change in the wrong place is a second bug, leave one runnable
check behind — line up with what `session_types/execution/CLAUDE.md`
already tells the session.

## Why only one of the six

Upstream ships `ponytail-audit`, `-debt`, `-gain`, `-help` and `-review`
alongside it. All five are user-invoked one-shot commands: `-help` and
`-gain` are interactive displays, the rest are report generators a person
asks for. An AFK execution session invokes none of them, so copying them
into someone else's repository would be five files of permanent dead
weight.

## Two things measured, so nobody has to wonder

**The `Skill` tool is not permission-gated.** A session run with
`--allowedTools "Bash,Read,Write,Edit"` invoked a project skill with no
denial and no stall, so the execution grant in `pu/session_types.py:51`
needs no widening for this to work.

**No pointer is written into any `CLAUDE.md`.** A skill is discovered from
its own description, and ponytail's says "use on ANY coding task", which
an execution session always is. Writing one would also mean editing a file
in a repository that has no business knowing about this unit — the same
rule `skills/NOTICE.md` states for the globally-installed set.
