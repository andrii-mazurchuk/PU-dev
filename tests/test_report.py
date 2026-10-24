"""The report contract: six fields read back, a cap, and a run record
that carries what pu measured beside what the agent wrote."""

from __future__ import annotations

from pathlib import Path

from pu import pipeline, report, runner, sessions

UNIT_ROOT = Path(__file__).resolve().parent.parent

WELL_FORMED = (
    "OUTCOME: done\n"
    "GOAL: say what it costs\n"
    "ACTIONS: read the price page\n"
    "REASONING: it is the only source\n"
    "EVIDENCE: the page says free\n"
    "NEEDS HUMAN: none\n"
    "\n"
    "It costs nothing."
)


def test_a_well_formed_report_parses_into_outcome_and_needs_human():
    got = report.parse(WELL_FORMED)
    assert got.structured is True
    assert got.outcome == "done"
    assert got.needs_human is None


def test_needs_human_is_read_when_it_is_not_none():
    got = report.parse(WELL_FORMED.replace("NEEDS HUMAN: none",
                                           "NEEDS HUMAN: approve the spend"))
    assert got.needs_human == "approve the spend"


def test_a_markdown_bolded_header_still_parses():
    """Models bold things. A contract that breaks on `**OUTCOME:**` would
    mark most real runs unstructured."""
    bolded = "\n".join(f"**{line.split(':')[0]}:**{line.split(':', 1)[1]}"
                       if ":" in line else line
                       for line in WELL_FORMED.splitlines())
    assert report.parse(bolded).structured is True


def test_a_malformed_report_is_unstructured_not_an_error():
    got = report.parse("I did the thing, it went fine.")
    assert (got.structured, got.outcome) == (False, None)
    assert got.text == "I did the thing, it went fine."


def test_a_report_is_cut_at_the_cap_and_says_so():
    got = report.parse("x" * 20_000)
    assert got.truncated is True
    assert got.text.startswith("x" * report.MAX_CHARS)
    assert got.text.endswith(report.TRUNCATION_MARK)


def test_every_prompt_carries_the_contract(store, body_store):
    uuid = store.add("what does it cost", tags=["afk"], kind="research")
    prompt = pipeline.build_prompt(store.get(uuid), None, UNIT_ROOT)
    assert "OUTCOME: done | partial | failed | nothing-to-do" in prompt
    assert "at-least-once" in prompt


def test_the_run_record_is_the_report(store, body_store, tmp_path):
    """Header measured by pu, body written by the agent, one record."""
    uuid = store.add("what does it cost", tags=["afk"], kind="research",
                     trigger="price-watch")
    session_store = sessions.SessionStore(tmp_path / "sessions")

    pipeline.tick(
        store, body_store, session_store, UNIT_ROOT,
        tmp_path / "peers.json", tmp_path / "cost_policy.json",
        session_runner=lambda **k: runner.SessionResult(
            0, text=WELL_FORMED, cost_usd=0.1, is_error=False),
    )

    [run] = session_store.runs()
    assert run["report_outcome"] == "done"
    assert run["report_structured"] is True
    assert run["report"].endswith("It costs nothing.")
    assert run["started_by"] == "trigger:price-watch"
    assert run["session_type_version"] and len(run["session_type_version"]) == 12
    assert run["agent_role"] == "system"
