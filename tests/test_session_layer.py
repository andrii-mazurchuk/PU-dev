"""The session layer: SOPs, discovery, argv, intake validation, the tick."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pu import (
    intake,
    logs_client,
    pipeline,
    records,
    repo_setup,
    runner,
    session_types,
    sessions,
    sops,
)

UNIT_ROOT = Path(__file__).resolve().parent.parent


# -- SOPs ------------------------------------------------------------------


def test_catalogue_is_empty_and_that_is_fine():
    """Zero SOPs is a supported state, not a broken install."""
    assert sops.catalogue(UNIT_ROOT) == []
    assert sops.validate(UNIT_ROOT) == []
    assert sops.resolve(UNIT_ROOT, "anything") is None


def _write_sop(root: Path, tag: str, description: str, kinds: str = "") -> None:
    d = root / "sops" / f"sop-{tag}"
    d.mkdir(parents=True)
    front = f"---\nname: sop-{tag}\ndescription: {description}\n"
    if kinds:
        front += f"kinds: {kinds}\n"
    (d / "SKILL.md").write_text(front + "---\n\n# procedure\n", encoding="utf-8")


def test_a_tag_resolves_to_its_procedure(tmp_path):
    _write_sop(tmp_path, "integration", "How to integrate a thing.", "execution")
    assert sops.resolve(tmp_path, "integration") is not None
    entry = sops.catalogue(tmp_path)[0]
    assert entry == {
        "tag": "integration",
        "description": "How to integrate a thing.",
        "kinds": ["execution"],
    }


def test_unmatched_tags_are_reported_not_rejected(tmp_path):
    """A misspelled tag means a procedure silently does not apply. It stays
    legal -- free-form labelling is useful -- but it is visible."""
    _write_sop(tmp_path, "integration", "d")
    matched, unmatched = sops.match(tmp_path, ["integration", "integraton", "afk"])
    assert matched == ["integration"]
    assert unmatched == ["integraton", "afk"]


def test_a_sop_without_a_description_is_broken(tmp_path):
    d = tmp_path / "sops" / "sop-bad"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("no frontmatter here\n", encoding="utf-8")
    assert sops.validate(tmp_path) == ["sop-bad (SKILL.md has no parseable description)"]


# -- session types ---------------------------------------------------------


def test_the_three_session_types_are_discovered():
    found = session_types.discover(UNIT_ROOT / "session_types")
    assert set(found) == {"intake", "research", "execution"}


def test_every_discovered_type_has_a_tool_grant():
    """A type with no grant would stall silently: every tool call would sit
    behind a permission prompt nobody is there to answer."""
    for name, stype in session_types.discover(UNIT_ROOT / "session_types").items():
        assert stype.allowed_tools, name


def test_research_may_not_write_files():
    research = session_types.discover(UNIT_ROOT / "session_types")["research"]
    assert "Write" not in research.allowed_tools
    assert "Edit" not in research.allowed_tools


def test_intake_reaches_neither_the_network_nor_the_shell():
    grant = session_types.discover(UNIT_ROOT / "session_types")["intake"].allowed_tools
    assert "WebFetch" not in grant and "WebSearch" not in grant
    # The only Bash it has is the lookup script, not Bash at large.
    assert "Bash," not in grant + ","
    assert "tag_sop_lookup" in grant


def test_a_directory_without_claude_md_is_not_a_session_type(tmp_path):
    (tmp_path / "notatype").mkdir()
    (tmp_path / "atype").mkdir()
    (tmp_path / "atype" / "CLAUDE.md").write_text("x", encoding="utf-8")
    assert set(session_types.discover(tmp_path)) == {"atype"}


# -- the runner ------------------------------------------------------------


def test_argv_always_asks_for_the_stream():
    argv = runner.build_argv(allowed_tools="Read")
    assert argv[:2] == ["claude", "-p"]
    # --verbose is not optional: the CLI refuses stream-json without it.
    assert argv[-3:] == ["--output-format", "stream-json", "--verbose"]
    assert "--allowedTools" in argv and "Read" in argv


def test_nothing_multi_line_ever_reaches_argv():
    """A newline in a command line is truncated by the npm batch shim on
    Windows -- silently, taking every flag after it too. Measured: the
    model saw only line one AND the output-format flags were lost, so the
    session both misunderstood its task and reported in plain text."""
    argv = runner.build_argv(allowed_tools="Read", model="m",
                             mcp_bridge_url="http://h/mcp")
    assert not any("\n" in part for part in argv), argv


def test_the_prompt_goes_on_stdin_not_argv():
    seen = {}
    multi_line = "line one\n\nline two"

    def fake(argv, cwd, stdin):
        seen.update(argv=argv, stdin=stdin)
        return runner.CompletedRun(0, "", "")

    runner.run_session(multi_line, cwd=None, runner=fake)
    assert seen["stdin"] == multi_line
    assert "line one" not in " ".join(seen["argv"])


def test_argv_carries_the_model():
    argv = runner.build_argv(model="m")
    assert argv[argv.index("--model") + 1] == "m"


def test_bridge_grant_is_added_only_with_a_bridge():
    plain = runner.build_argv(allowed_tools="Read")
    assert "--mcp-config" not in plain
    bridged = runner.build_argv(allowed_tools="Read", mcp_bridge_url="http://h/mcp")
    assert "mcp__mcp-bridge__*" in bridged[bridged.index("--allowedTools") + 1]


def test_parse_stream_folds_tool_calls_in_order():
    stream = "\n".join([
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Read"}]}}),
        "not json at all",
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash"},
            {"type": "tool_use", "name": "Read"}]}}),
        json.dumps({"type": "result", "result": "done", "total_cost_usd": 0.4,
                    "duration_ms": 1200, "is_error": False,
                    "modelUsage": {"claude-x": {}}}),
    ])
    parsed = runner.parse_stream(stream)
    assert parsed.tool_calls == ("Read", "Bash", "Read")
    assert parsed.text == "done"
    assert parsed.cost_usd == 0.4
    assert parsed.models_used == ("claude-x",)
    assert parsed.raw_stream == stream


def test_parse_stream_of_a_killed_process_degrades():
    """No final result line is missing data, not an error."""
    parsed = runner.parse_stream(json.dumps(
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read"}]}}
    ))
    assert parsed.tool_calls == ("Read",)
    assert parsed.cost_usd is None and parsed.text == ""


def test_the_claude_command_resolves_to_something_that_can_start():
    """On Windows the CLI is an npm shim whose real name is `claude.cmd`;
    CreateProcess cannot run the bare name, so a plain ["claude", ...]
    fails with "the system cannot find the file specified". Resolved
    rather than assumed."""
    import os
    import shutil
    resolved = runner.claude_command()
    assert resolved
    if shutil.which("claude"):
        assert os.path.exists(resolved[0]), resolved
    assert runner.claude_command({"PU_CLAUDE_CMD": "wsl -e claude"}) == (
        "wsl", "-e", "claude",
    )


def test_exit_code_comes_from_the_process_not_the_stream():
    result = runner.run_session(
        "x", cwd=None,
        runner=lambda argv, cwd, stdin: runner.CompletedRun(2, "", "boom"),
    )
    assert result.exit_code == 2 and result.ok is False


# -- intake ----------------------------------------------------------------


def test_extract_block_finds_json_in_prose():
    text = 'Sure, here you go:\n\n```json\n{"tasks": []}\n```\n\nHope that helps.'
    assert intake.extract_block(text) == {"tasks": []}


def test_extract_block_tolerates_a_truncated_fence():
    assert intake.extract_block('```json\n{"tasks": []}') == {"tasks": []}


def test_no_json_at_all_raises():
    with pytest.raises(intake.IntakeError):
        intake.extract_block("I could not decide.")


def test_a_task_must_trace_to_the_message():
    """The mechanical half of 'transcribe, do not invent'."""
    with pytest.raises(intake.IntakeError) as excinfo:
        intake.validate({"tasks": [{"title": "t", "kind": "execution"}]})
    assert "traces_to" in str(excinfo.value)


def test_an_unknown_kind_is_raised_on_never_defaulted():
    with pytest.raises(records.RecordError):
        intake.validate({"tasks": [
            {"title": "t", "kind": "invent", "traces_to": "x"}]})


def test_a_missing_kind_is_raised_on_never_defaulted():
    with pytest.raises(intake.IntakeError):
        intake.validate({"tasks": [{"title": "t", "traces_to": "x"}]})


def test_bounce_conditions_are_a_closed_list():
    with pytest.raises(intake.IntakeError):
        intake.validate({"bounce": {"condition": "dunno", "reason": "r"}})
    ok = intake.validate({"bounce": {"condition": "needs_a_map", "reason": "r"}})
    assert ok["bounce"]["condition"] == "needs_a_map"


def test_a_bounce_must_carry_a_reason():
    with pytest.raises(intake.IntakeError):
        intake.validate({"bounce": {"condition": "needs_a_map", "reason": "  "}})


def test_apply_is_all_or_none(store, body_store):
    """A proposal that fails partway must leave nothing pending behind --
    half a plan is worse than none, because someone has to reconcile it."""
    calls = {"n": 0}
    original = body_store.set

    def flaky(uuid, text):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        return original(uuid, text)

    body_store.set = flaky
    plan = {"tasks": [
        {"title": "first", "kind": "execution", "project": None, "tags": [],
         "afk": True, "body": "one", "traces_to": "a"},
        {"title": "second", "kind": "execution", "project": None, "tags": [],
         "afk": False, "body": "two", "traces_to": "b"},
    ]}
    with pytest.raises(OSError):
        intake.apply(store, body_store, plan)
    assert store.count("status:pending") == 0


def test_an_intake_proposal_can_place_an_execution_task_in_a_repo(
    store, body_store
):
    """Intake is told to identify the repository -- `no_placeable_target`
    names it -- so the proposal has to be able to carry one. Without this
    every intake-created execution task is skipped for having no repo, and
    the miss is quiet: the tick annotates and moves on."""
    plan = intake.validate({"tasks": [{
        "title": "fix the thing", "kind": "execution", "repo": "/srv/app",
        "afk": True, "traces_to": "fix the thing in /srv/app",
    }]})
    assert plan["tasks"][0]["repo"] == "/srv/app"

    task = store.get(intake.apply(store, body_store, plan)[0])
    assert task.repo == "/srv/app"
    assert task.runnable is True


def test_apply_creates_afk_and_non_afk_side_by_side(store, body_store):
    """Intake may split one message into work an agent does and a check a
    person makes. Both come from one proposal."""
    created = intake.apply(store, body_store, {"tasks": [
        {"title": "do it", "kind": "execution", "project": "dev.x", "tags": [],
         "afk": True, "body": "detail", "traces_to": "the ask"},
        {"title": "verify it", "kind": "task", "project": "dev.x", "tags": [],
         "afk": False, "body": None, "traces_to": "the ask"},
    ]})
    assert len(created) == 2
    doer, checker = store.get(created[0]), store.get(created[1])
    assert doer.afk is True and doer.runnable is True
    assert checker.afk is False
    assert body_store.get(created[0]) == "detail"
    assert any("traces to: the ask" in a for a in doer.annotations)


# -- repo setup ------------------------------------------------------------


def test_a_non_git_directory_is_refused(tmp_path):
    with pytest.raises(repo_setup.RepoError):
        repo_setup.check_repo(tmp_path)


def test_prepare_copies_skills_but_never_overwrites(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    stype = tmp_path / "execution"
    (stype / ".claude" / "skills" / "adversarial").mkdir(parents=True)
    (stype / ".claude" / "skills" / "adversarial" / "SKILL.md").write_text(
        "ours", encoding="utf-8")
    (stype / ".claude" / "skills" / "keep").mkdir(parents=True)
    (stype / ".claude" / "skills" / "keep" / "SKILL.md").write_text(
        "ours", encoding="utf-8")

    existing = repo / ".claude" / "skills" / "keep"
    existing.mkdir(parents=True)
    (existing / "SKILL.md").write_text("theirs", encoding="utf-8")

    report = repo_setup.prepare(repo, stype)
    assert report.copied == ("adversarial/SKILL.md",)
    assert report.skipped_existing == ("keep/SKILL.md",)
    # The repo's own version always wins. This unit is a guest.
    assert (existing / "SKILL.md").read_text(encoding="utf-8") == "theirs"


def test_the_skills_this_unit_actually_ships_reach_a_target_repo(tmp_path):
    """The other two tests here check the copy's semantics against synthetic
    files. This one checks what really ships: an execution session runs in
    the target repo, so a skill that is not copied there does not load at
    all, and the failure is silent -- the session simply builds without it.

    Its licence is asserted on too, because that copy is a redistribution
    and MIT asks for the notice to travel with it."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    stype = Path(__file__).resolve().parent.parent / "session_types" / "execution"

    report = repo_setup.prepare(repo, stype)

    assert "ponytail/SKILL.md" in report.copied
    assert "ponytail/LICENSE" in report.copied
    assert (repo / ".claude" / "skills" / "ponytail" / "SKILL.md").exists()

    # NOTICE.md sits outside `.claude/skills/` on purpose: it is this unit's
    # own reasoning, and a repo pu works in has no business knowing pu exists.
    assert not (repo / ".claude" / "NOTICE.md").exists()
    assert not (repo / ".claude" / "skills" / "NOTICE.md").exists()


def test_prepare_never_writes_a_settings_file(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    stype = tmp_path / "execution"
    stype.mkdir()
    report = repo_setup.prepare(repo, stype)
    assert report.settings_present is False
    assert not (repo / ".claude" / "settings.json").exists()


# -- the logs client -------------------------------------------------------


def test_logs_peer_is_found_by_capability_never_by_name(tmp_path):
    peers = tmp_path / "peers.json"
    peers.write_text(json.dumps({"units": [
        {"name": "cu", "base_url": "http://127.0.0.1:9002", "capabilities": []},
        {"name": "anything-at-all", "base_url": "http://127.0.0.1:9004",
         "capabilities": ["log_write", "log_query"]},
    ]}), encoding="utf-8")
    peer = logs_client.find_logs_peer(peers)
    assert peer["base_url"] == "http://127.0.0.1:9004"


def test_no_peers_file_is_a_normal_state(tmp_path):
    assert logs_client.find_logs_peer(tmp_path / "absent.json") is None
    assert logs_client.record_session_run(
        tmp_path / "absent.json", "pu", "ok", 1.0, 0.1) is False


def test_an_unreachable_logs_unit_never_raises(tmp_path):
    peers = tmp_path / "peers.json"
    peers.write_text(json.dumps({"units": [
        {"name": "x", "base_url": "http://127.0.0.1:1", "capabilities": ["log_write"]},
    ]}), encoding="utf-8")

    def explode(*a, **k):
        raise OSError("refused")

    assert logs_client.record_session_run(
        peers, "pu", "ok", 1.0, 0.1, opener=explode) is False


def test_the_entry_matches_the_logs_unit_contract(tmp_path):
    peers = tmp_path / "peers.json"
    peers.write_text(json.dumps({"units": [
        {"name": "x", "base_url": "http://h", "capabilities": ["log_write"]},
    ]}), encoding="utf-8")
    captured = {}

    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def opener(request, timeout=None):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return Resp()

    assert logs_client.record_session_run(
        peers, "pu", "resolved", 4.2, 0.31,
        extra={"session_type": "research"}, opener=opener) is True
    assert captured["url"] == "http://h/entries"
    assert captured["body"]["entry_type"] == "session_run"
    assert captured["body"]["source_unit"] == "pu"
    assert captured["body"]["payload"]["outcome"] == "resolved"
    assert captured["body"]["payload"]["duration_seconds"] == 4.2
    assert captured["body"]["payload"]["cost"] == 0.31


# -- session artifacts and aggregates --------------------------------------


def test_run_artifacts_keep_the_raw_stream(tmp_path):
    store = sessions.SessionStore(tmp_path)
    result = runner.SessionResult(
        exit_code=0, text="done", cost_usd=0.5, duration_ms=1000,
        tool_calls=("Read",), raw_stream='{"type":"result"}')
    run_dir = store.record("11111111-2222-3333-4444-555555555555",
                           "research", "the prompt", result, "resolved")
    assert (run_dir / "prompt.txt").read_text(encoding="utf-8") == "the prompt"
    assert (run_dir / "stream.jsonl").read_text(encoding="utf-8") == '{"type":"result"}'
    assert json.loads((run_dir / "result.json").read_text(encoding="utf-8"))["outcome"] == "resolved"


def test_aggregates_are_numbers_not_verdicts(tmp_path):
    store = sessions.SessionStore(tmp_path)
    uuid = "11111111-2222-3333-4444-555555555555"
    store.record(uuid, "research", "p",
                 runner.SessionResult(0, cost_usd=1.0, duration_ms=1000), "resolved")
    store.record(uuid, "research", "p",
                 runner.SessionResult(1, cost_usd=0.5, duration_ms=3000), "failed")
    agg = store.aggregates()
    assert agg["runs"] == 2
    assert agg["cost_usd_total"] == 1.5
    assert agg["by_session_type"]["research"]["failures"] == 1
    assert agg["by_session_type"]["research"]["avg_duration_ms"] == 2000
    assert agg["by_outcome"] == {"resolved": 1, "failed": 1}


def test_empty_history_aggregates_to_zero(tmp_path):
    assert sessions.SessionStore(tmp_path / "nope").aggregates()["runs"] == 0


# -- the tick --------------------------------------------------------------


def _session_store(tmp_path):
    return sessions.SessionStore(tmp_path / "sessions")


def test_tick_with_nothing_runnable(store, body_store, tmp_path):
    result = pipeline.tick(
        store, body_store, _session_store(tmp_path), UNIT_ROOT,
        tmp_path / "peers.json", tmp_path / "cost_policy.json",
        session_runner=lambda **k: pytest.fail("must not run a session"),
    )
    assert result.ran is False and result.reason == "nothing runnable"


def test_the_cost_gate_runs_before_selection(store, body_store, tmp_path):
    """A gate that runs after selection can be routed around by whatever
    selected differently."""
    store.add("work", tags=["afk"], kind="research")
    session_store = _session_store(tmp_path)
    session_store.record("11111111-2222-3333-4444-555555555555", "research", "p",
                         runner.SessionResult(0, cost_usd=9.0), "resolved")
    policy = tmp_path / "cost_policy.json"
    policy.write_text(json.dumps({"daily_cost_cap_usd": 1.0}), encoding="utf-8")

    result = pipeline.tick(
        store, body_store, session_store, UNIT_ROOT,
        tmp_path / "peers.json", policy,
        session_runner=lambda **k: pytest.fail("gate must block before selection"),
    )
    assert result.ran is False and "daily cost cap" in result.reason


def test_an_absent_cost_policy_enforces_nothing(tmp_path):
    assert pipeline.read_cost_policy(tmp_path / "absent.json") == {}
    assert pipeline.check_cost_gate({}, _session_store(tmp_path), "2026-09-08") is None


def test_a_research_tick_claims_runs_and_resolves(store, body_store, tmp_path):
    uuid = store.add("what does it cost", tags=["afk"], kind="research")
    seen = {}

    def fake_session(prompt, cwd, allowed_tools, model):
        seen.update(prompt=prompt, cwd=cwd, allowed_tools=allowed_tools)
        # The claim must already be in place before any work happens.
        assert store.get(uuid).active is True
        return runner.SessionResult(0, text="It costs nothing.", cost_usd=0.2,
                                    duration_ms=1000, is_error=False)

    result = pipeline.tick(
        store, body_store, _session_store(tmp_path), UNIT_ROOT,
        tmp_path / "peers.json", tmp_path / "cost_policy.json",
        session_runner=fake_session,
    )
    assert result.ran and result.outcome == "resolved"
    assert seen["cwd"].name == "research"
    assert "what does it cost" in seen["prompt"]

    done = store.get(uuid)
    assert done.status == "completed"
    assert "It costs nothing." in done.annotations


def test_a_failed_session_releases_the_claim(store, body_store, tmp_path):
    """A task must never be left claimed forever by a session that died."""
    uuid = store.add("work", tags=["afk"], kind="research")
    result = pipeline.tick(
        store, body_store, _session_store(tmp_path), UNIT_ROOT,
        tmp_path / "peers.json", tmp_path / "cost_policy.json",
        session_runner=lambda **k: runner.SessionResult(1, text=""),
    )
    assert result.outcome == "failed"
    task = store.get(uuid)
    assert task.active is False and task.status == "pending"


def test_a_launch_failure_releases_the_claim(store, body_store, tmp_path):
    uuid = store.add("work", tags=["afk"], kind="research")

    def explode(**k):
        raise FileNotFoundError("claude not installed")

    result = pipeline.tick(
        store, body_store, _session_store(tmp_path), UNIT_ROOT,
        tmp_path / "peers.json", tmp_path / "cost_policy.json",
        session_runner=explode,
    )
    assert result.outcome == "launch_failed"
    assert store.get(uuid).active is False


def test_an_execution_task_without_a_repo_is_skipped_with_a_reason(
    store, body_store, tmp_path
):
    uuid = store.add("build it", tags=["afk"], kind="execution")
    result = pipeline.tick(
        store, body_store, _session_store(tmp_path), UNIT_ROOT,
        tmp_path / "peers.json", tmp_path / "cost_policy.json",
        session_runner=lambda **k: pytest.fail("must not run without a repo"),
    )
    assert result.ran is False
    assert any("needs a `repo`" in a for a in store.get(uuid).annotations)


def test_an_execution_tick_runs_in_the_repo_with_injected_instructions(
    store, body_store, tmp_path
):
    repo = tmp_path / "target"
    (repo / ".git").mkdir(parents=True)
    store.add("build it", tags=["afk"], kind="execution", repo=str(repo))
    seen = {}

    def fake_session(prompt, cwd, allowed_tools, model):
        seen.update(cwd=cwd, prompt=prompt, allowed_tools=allowed_tools)
        return runner.SessionResult(0, text="changed one file", is_error=False)

    result = pipeline.tick(
        store, body_store, _session_store(tmp_path), UNIT_ROOT,
        tmp_path / "peers.json", tmp_path / "cost_policy.json",
        session_runner=fake_session,
    )
    assert result.ran
    # cwd is the repo, so its own CLAUDE.md and hooks apply...
    assert seen["cwd"] == repo
    # ...which is exactly why ours is folded into the prompt instead.
    assert "Execution session" in seen["prompt"]
    assert "Write" in seen["allowed_tools"]


def test_an_intake_tick_creates_the_proposed_tasks(store, body_store, tmp_path):
    from pu import service
    accepted = service.push_inbox(store, body_store, "cu", "please fix the deploy")

    proposal = json.dumps({"tasks": [{
        "title": "fix the failing deploy", "kind": "execution",
        "project": "dev.thing", "tags": [], "afk": False,
        "traces_to": "please fix the deploy",
    }]})

    def fake_session(prompt, cwd, allowed_tools, model):
        # The catalogue is handed to intake up front: it cannot choose tags
        # from a set it does not know exists.
        assert "Available procedures" in prompt
        assert "please fix the deploy" in prompt
        return runner.SessionResult(0, text=f"```json\n{proposal}\n```",
                                    is_error=False)

    result = pipeline.tick(
        store, body_store, _session_store(tmp_path), UNIT_ROOT,
        tmp_path / "peers.json", tmp_path / "cost_policy.json",
        session_runner=fake_session,
    )
    assert result.outcome == "converted"
    assert store.get(accepted["uuid"]).status == "completed"
    created = [t for t in store.export("status:pending") if t.kind == "execution"]
    assert len(created) == 1 and created[0].afk is False


def test_an_intake_bounce_closes_the_record_and_creates_nothing(
    store, body_store, tmp_path
):
    from pu import service
    accepted = service.push_inbox(store, body_store, "cu", "make it better somehow")
    bounce = json.dumps({"bounce": {
        "condition": "no_stateable_outcome",
        "reason": "the message never says what 'better' would look like",
    }})

    result = pipeline.tick(
        store, body_store, _session_store(tmp_path), UNIT_ROOT,
        tmp_path / "peers.json", tmp_path / "cost_policy.json",
        session_runner=lambda **k: runner.SessionResult(
            0, text=bounce, is_error=False),
    )
    assert result.outcome == "bounced"
    assert store.get(accepted["uuid"]).status == "completed"
    assert store.count("status:pending") == 0


def test_an_unusable_intake_proposal_creates_nothing_and_releases(
    store, body_store, tmp_path
):
    from pu import service
    accepted = service.push_inbox(store, body_store, "cu", "do a thing")
    result = pipeline.tick(
        store, body_store, _session_store(tmp_path), UNIT_ROOT,
        tmp_path / "peers.json", tmp_path / "cost_policy.json",
        session_runner=lambda **k: runner.SessionResult(
            0, text="I decided not to answer in JSON.", is_error=False),
    )
    assert result.outcome == "invalid_proposal"
    task = store.get(accepted["uuid"])
    assert task.status == "pending" and task.active is False
    assert store.count("status:pending") == 1


def test_the_prompt_names_mandatory_procedures(store, body_store, tmp_path):
    """A tag that resolves to an SOP must reach the session as binding."""
    task = records.Task(
        uuid="11111111-2222-3333-4444-555555555555",
        description="do it", status="pending", kind="execution",
        tags=("afk", "integration"),
    )
    _write_sop(tmp_path, "integration", "How to integrate.")
    prompt = pipeline.build_prompt(task, "the detail", tmp_path)
    assert "Mandatory procedures" in prompt
    assert "--tag integration" in prompt
    # the afk marker is not a procedure and must not be presented as one
    assert "--tag afk" not in prompt


def test_the_prompt_names_an_interpreter_the_grant_actually_allows():
    """`python3` does not exist on Windows and `python` often does not on
    Linux. A session told to run one it has no permission for hits a
    permission prompt with nobody there to answer it."""
    task = records.Task(
        uuid="11111111-2222-3333-4444-555555555555",
        description="do it", status="pending", kind="execution",
        tags=("afk", "integration"),
    )
    import tempfile
    root = Path(tempfile.mkdtemp())
    _write_sop(root, "integration", "How to integrate.")
    prompt = pipeline.build_prompt(task, None, root)
    grant = session_types.ALLOWED_TOOLS["execution"]
    assert session_types.LOOKUP_COMMAND in prompt
    assert f"Bash({session_types.LOOKUP_COMMAND}:*)" in grant
