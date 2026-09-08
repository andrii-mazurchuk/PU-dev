---
name: setup-project
description: "Prepare a working directory to take part in the holonic system: queue access, permissions, project scope, and git. Run once per project, before first use of wayfinder or the queue skills."
disable-model-invocation: true
---

# Set up a project directory

Scaffold what a project needs to use the shared task queue and be worked
by wayfinder.

This is **prompt-driven, not a script**. Explore, present what you found,
confirm with the user, then write. Skip any section exploration has
already settled, and lead every question with a recommended answer so it
can be accepted in a word.

## Refuse to run in a unit

**First, check for `UNIT_CONTRACT.md` in the target directory.** If it is
there, this is a *unit* of the system, not a project — stop and say so.

Units are enclosed and swappable, addressed at runtime by registry name
and forbidden from knowing their peers by name. Writing "this project's
tasks live in the queue held by the pu unit" into a unit's `CLAUDE.md`
creates exactly the hardcoded dependency that arrangement exists to
prevent. A unit reaches peers through the gateway, not through this.

## 1. Explore

Read what is there; assume nothing.

- `UNIT_CONTRACT.md` — see above, this is the stop condition
- `git remote -v`, `.git/` — is this a repo at all? Is there a remote?
  Which host?
- `CLAUDE.md` and `AGENTS.md` at the root — which exists? Does either
  already carry a `## Task queue` section from a previous run?
- `.claude/settings.json` — what does it already permit and deny?
- Is the processing unit reachable? `curl -s $PU/health` should return
  `{"status": "ok"}`. Its start-up banner prints the `TASKRC` path a
  session needs.
- Which skills are installed: `wayfinder`, `grilling`, `domain-modeling`,
  `research`, `prototype`, `pu-tasks`. A missing `wayfinder` means the
  map half cannot run.
- Has `onboard-trust` already been run here? A `.github/workflows/`
  verify job, or gate config, means yes.

## 2. Present findings and ask

Summarise what is present and what is missing, then take the sections in
order — one section, one answer, then the next. Give a one-line explainer
only where the choice genuinely branches.

**Section A: Project scope.**

> Explainer: every task in the queue carries a `project:` — a dotted
> scope. Work from this directory lands under it, and filtering by a
> parent scope returns everything nested beneath. Pick the name you would
> recognise this project by.

Recommend the repository's directory name, lowercased. Sub-scopes come
later and need no decision now.

**Section B: Permissions.**

> Explainer: without an allowlist every queue command stops for approval,
> including in sessions where nobody is watching.

Recommend allowing: the `task` command as this machine invokes it (plain
`task`, or the WSL form if that is how the unit reaches it), `curl` to the
unit's base URL, and `gh` if there is a GitHub remote.

Merge into any existing `permissions.allow`. **Never touch an existing
`deny` list** — that is the repo's own statement about what it will not
let a session do.

**Section C: Git.** Skip entirely if the directory is already a repo with
a remote.

- Not a repo → offer `git init`.
- Repo, no remote → offer `gh repo create`, or leave it local. A local
  repo is fine; prototypes need somewhere to put a throwaway branch and a
  local branch is somewhere.

**Do not configure verification, CI or branch protection here.** If
`onboard-trust` is installed, that skill owns them; say so and point at
it. Two skills writing the same gate config is how it ends up
contradicting itself.

## 3. Confirm

Show a draft of the `## Task queue` block and of any
`.claude/settings.json` change, as a diff against what is there now. Let
the user edit before anything is written.

## 4. Write

**Pick the file to edit:** `CLAUDE.md` if it exists, else `AGENTS.md`,
else ask which to create — never pick for them, and never create one when
the other already exists.

If a `## Task queue` block is already present, **update it in place**.
Appending a second one leaves two answers to the same question. Leave the
surrounding sections alone.

```markdown
## Task queue

This project's work lives in the shared task queue under
`project:<scope>`. The `pu-tasks` skill has the operations; it is
installed globally, so there is nothing to read in this repo.

Point `TASKRC` at the path the unit prints on start, or `kind:` and
`parent_uuid:` will not exist for you.
```

That block is short on purpose: the queue is one shared thing described in
one place, and a per-repo copy of the operations would be a second copy
that drifts.

## 5. Done

Say what is now possible and what is not. In particular, name anything
missing that blocks a workflow — a `wayfinder` that is not installed, or a
unit that is not running — rather than leaving it to be discovered when it
fails.
