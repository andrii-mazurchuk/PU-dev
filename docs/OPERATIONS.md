# Operations

## Configuration

All of it is environment, injected by the gateway from `units.yaml` at
process start. **There is deliberately no config-file convention** —
`pu/main.py:1` says so, and the Taskwarrior schema is passed as `rc.`
overrides for the same reason (`DECISIONS.md` §no-rc-file).

| var | default | what it is |
|---|---|---|
| `PU_TASK_CMD` | `task` | the command up to the arguments. `task` on Linux; `wsl -d Ubuntu -e task` on a Windows host. |
| `PU_TASKDATA` | `~/.pu-taskdata` | Taskwarrior data location — **a path on the binary's side** |
| `PU_STATE_DIR` | `state` | this unit's private storage — **a path this process sees** |
| `PU_PORT` | `9001` | listen port; matches the registered `base_url` |
| `PU_CLAUDE_CMD` | resolved via `shutil.which` | overrides how `claude` is launched |

`--host`, `--port`, `--taskdata`, `--state-dir` and `--prompts-dir` are
also CLI flags on `python -m pu.main`; the environment supplies their
defaults.

## Running it

```bash
uv sync && uv run pytest          # or: pip install -e ".[dev]" && pytest
python -m pu.main                 # defaults to 127.0.0.1:9001
```

The dev dependency is declared **twice** in `pyproject.toml`, as a PEP 735
`[dependency-groups]` *and* as a `[project.optional-dependencies]` extra,
and they must stay in step. `uv sync` reads the group and ignores extras;
`pip install -e ".[dev]"` — what CI runs — reads the extra. With only one
of them, the other toolchain silently produces an environment with no
pytest in it.

### What happens at startup (`pu/main.py:23`)

1. Construct the three stores from the resolved paths.
2. **Render `state/taskrc`** from `RC_SCHEMA`, with `newline="\n"`, so an
   outside session running bare `task` sees the same UDAs pu passes as
   overrides. A write failure degrades — pu's own writes never needed it.
3. **Warm the store in a daemon thread.** When `task` lives inside WSL,
   the first call after the distro has gone idle pays for booting it:
   measured ~9.4s cold against ~0.37s warm. Long enough to time out the
   first real request after a quiet period, so it is paid off the request
   path.
4. Start a `ThreadingHTTPServer`, and print the banner with `flush=True` —
   stdout is a pipe whenever the gateway redirects it, and a startup line
   nobody sees is a path nobody can point a session at.

## Where the `task` binary lives

**The unit does not care**, and nothing outside `pu/taskstore.py` knows.
The whole of it is `PU_TASK_CMD`.

**On Linux, set nothing.** The default is `task`, so a native deployment
needs no configuration at all. That is the ordinary case.

**On Windows there is no native Taskwarrior** — upstream ships source only
— so the binary lives in WSL and is reached through a prefix:

```
PU_TASK_CMD="wsl -d Ubuntu -e task"
```

### `-e`, never `--`

`normalise_base_cmd` (`pu/taskstore.py:114`) rewrites `--` to `-e`
wherever it comes from. **Do not undo this.**

`wsl.exe -- <cmd>` hands the command to the default login shell, which
expands it: backticks vanish, `${HOME}` interpolates, and `$(echo PWNED)`
**executes**. `wsl.exe -e <cmd>` execs directly and every byte survives.

That matters more here than it might elsewhere, because the text reaching
that module is not all trusted — a task's annotations carry a session's
own final message, so a session could otherwise write shell into a store
write and have it run.

It is normalised rather than documented because `PU_TASK_CMD` is config a
human sets, and a comment cannot stop someone typing `--`.

### Which side of the boundary a path is on

Under the WSL arrangement:

| | seen by | so on Windows it is |
|---|---|---|
| `PU_TASKDATA` | the **binary** | a Linux path — `~` is the WSL user's home |
| `PU_STATE_DIR` | **this process** | a Windows path |
| a task's `repo` | **this process** | a Windows path |

`path_for_binary` (`pu/taskstore.py:136`) translates a local path into
what the binary will read — Windows drives are mounted at `/mnt/<letter>`,
so the translation is deterministic and costs no subprocess. Natively on
Linux it does nothing at all, which is the point: the boundary exists in
one arrangement only.

**Never put the task data in `/tmp`.** WSL wipes it when the distro shuts
down. The default `~/.pu-taskdata` is correct.

**Git Bash rewrites `/tmp/...` arguments into Windows paths** before the
process ever sees them. Drive pu from PowerShell, or set
`MSYS_NO_PATHCONV=1`.

## Verifying it

```bash
pytest                                          # 129 tests
python scripts/tag_sop_lookup.py --validate-all # every SOP directory usable
python scripts/smoke.py --base-url http://127.0.0.1:9001
curl -X POST http://127.0.0.1:9001/trigger      # spends a real session
```

**The suite runs against the real `task` binary**, not only an injected
fake — CI installs Taskwarrior before running it (`.github/workflows/ci.yml`).
That is deliberate: a fake that also builds the argv tests nothing, and
this exact gap let six launch bugs survive a fully green suite in a
sibling unit. The seam is the *subprocess*, never the argv construction,
which is built in-process and asserted on.

`scripts/smoke.py` exercises the HTTP surface a peer sees. It is
read-mostly but **does write** — one inbox message, one throwaway map and
ticket — so point it at a scratch `--taskdata`, not a store you care
about. It does not spend a session; that is `/trigger`, deliberately
separate because it costs money.

`scripts/install_skill.py` installs the skills this unit ships. **Globally
by default**, to `~/.claude/skills/`, because there is one queue and one
set of efforts — a per-repo copy would be N copies of one fact.
`--repo <path>` exists for a repository that genuinely needs its own. It
never overwrites, and it writes no pointer into anyone's `CLAUDE.md`.

Skills for **execution sessions are a separate, project-scoped set** and
that script does not touch them. They live in
`session_types/execution/.claude/skills/` and are copied into each target
repo by `repo_setup.prepare` on every tick, because an execution session's
cwd is that repo and nothing in this unit's tree loads there. See
`session_types/execution/.claude/NOTICE.md`.

## What degrades, and what does not

The rule is that **absence is normal** and almost everything degrades. The
exceptions are the guardian invariants, and they are exceptions on
purpose.

### Degrades silently

| condition | becomes |
|---|---|
| no `peers.json`, or no `log_write` peer | session logs are dropped, `record_session_run` → `False` |
| the logs peer is unreachable or rejects | the same |
| no `cost_policy.json`, or unparseable | `{}` — enforce nothing |
| a missing or corrupt `repeat_tracker.json` | "nothing seen yet"; the breaker is slower to fire |
| an unreachable or uninitialised task store, in `/stats` | zero tasks |
| a filter matching nothing | `[]` |
| a task that does not exist, in `get_task` | `null`, not an error |
| a non-JSON line in the session stream | skipped; the tool sequence already recorded survives |
| a session killed mid-stream | every result field `None`, `exit_code` from the real process |
| an unparseable `start` stamp | the claim is left alone |
| an `OSError` writing run artifacts | swallowed; the session still counts as done |
| an `OSError` writing `state/taskrc` | skipped; pu's own writes never needed it |
| a `.md.tmp` write failure in the body store | raises `OSError` to the caller — not caught |

### Raises

| condition | why |
|---|---|
| an unknown `kind` | a task no session type can ever run would enter a queue agents act on |
| a record with no uuid | a record with no identity is not a record |
| a body key that is not a uuid | the path is built from untrusted HTTP input |
| a multi-line `key:value` to `task` | the binary exits 0, discards the value, **and overwrites the description** |
| an execution target with no `.git` | an agent with `Write` and no way back |
| a malformed intake proposal | see `FLOWS.md` §2 |

The first, notably: **no `peers.json` means every session log is silently
dropped.** It degrades correctly, which is exactly why nobody notices.
Prove the path after registering.

## Registering with the gateway

The manifest is `units.yaml` in the gateway repo. It is **gitignored and
per-deployment**, so a fresh machine has only `units.yaml.example` — the
entry has to be written, not edited.

An entry needs, at minimum:

```yaml
  - name: pu
    source:
      git_url: https://github.com/andrii-mazurchuk/PU-dev.git
      ref: master
    local_path: units/pu
    lifecycle: persistent
    base_url: http://127.0.0.1:9001
    unit_type: processing
    start_cmd: ["python3", "-m", "pu.main"]
    trigger:
      type: interval
      seconds: 300
    prompts:
      - path: prompts/default.md
        tier: default
    env:
      PU_TASK_CMD: "wsl -d Ubuntu -e task"
      PU_TASKDATA: "~/.pu-taskdata"
```

`units.yaml.example` in the gateway repo carries a processing-unit entry
to copy from; `docs/UNIT_STANDARDS.md` there is the contract itself and
wins over anything in this repo if the two disagree.

After `holonic start`, three files should appear in this repository root:
`peers.json`, `cost_policy.json`, `delivery_policy.json`. All are
gitignored. Until `peers.json` exists there is no logs peer, and see
above.

**Then prove one real tick lands a `session_run` entry at the logs unit.**
Until that passes, the logging path is unverified in both directions —
and it is the one failure that reports success.

## Known gaps

Stated so nobody has to rediscover them:

- **No MCP bridge.** `runner.build_argv` accepts an `mcp_bridge_url` and
  wires `--mcp-config` plus the `mcp__mcp-bridge__*` grant, but nothing
  passes one. Sessions cannot reach any peer unit's tools.
- **No GitHub mirror.** The `url` and `repo` fields exist and nothing
  populates them, so the source that feeds `execution` is not connected.
- **`MODELS` is empty** (`pu/session_types.py:59`). Every session type
  uses whatever the CLI defaults to.
- **`delivery_policy.json` is written and never read.**
- **The full loop has never run with the real skills** — `/wayfinder`
  calling grilling and domain-modeling, tickets landing here, pu resolving
  the `+afk` ones. Every piece is proven individually. It needs a real
  project directory, not a unit.
