"""The account-usage ceilings, pu side.

Standard: holonic-node/docs/UNIT_STANDARDS.md, "Account-usage ceilings".
Most of these pin the two rules that read like bugs and are the opposite
-- absence allows, staleness allows -- because both are what stop the
gate latching shut, and both are exactly what a later "tightening" would
remove.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pu import usage_gate

LIVE_EVENT = {
    "type": "rate_limit_event",
    "rate_limit_info": {
        "status": "allowed",
        "unifiedWindows": {
            "five_hour": {"utilization": 0.02, "resetsAt": 1788985800},
            "seven_day": {"utilization": 0.22, "resetsAt": 1789185600},
        },
    },
}
CEILINGS = {"five_hour": 0.70, "seven_day": 0.80}
FUTURE = 9_999_999_999


def test_parses_a_real_event():
    assert usage_gate.parse_rate_limit_event(LIVE_EVENT) == {
        "five_hour": {"utilization": 0.02, "resets_at": 1788985800},
        "seven_day": {"utilization": 0.22, "resets_at": 1789185600},
    }


def test_an_unfamiliar_payload_contributes_nothing_rather_than_raising():
    """Parsed while handling a session that already succeeded."""
    assert usage_gate.parse_rate_limit_event({"rate_limit_info": {}}) == {}


def test_under_both_ceilings_proceeds():
    windows = {"five_hour": {"utilization": 0.02, "resets_at": FUTURE},
               "seven_day": {"utilization": 0.22, "resets_at": FUTURE}}
    assert usage_gate.check_usage_gate(windows, CEILINGS, now=0) is None


@pytest.mark.parametrize("window,value", [("five_hour", 0.71), ("seven_day", 0.81)])
def test_either_window_alone_blocks(window, value):
    """Two separate limits, not an average."""
    windows = {window: {"utilization": value, "resets_at": FUTURE}}
    reason = usage_gate.check_usage_gate(windows, CEILINGS, now=0)
    assert reason and window in reason


def test_no_reading_allows():
    """A node that has never run a session knows nothing. Blocking on
    absence would mean it could never run the first one."""
    assert usage_gate.check_usage_gate({}, CEILINGS, now=0) is None


def test_a_reading_past_its_reset_allows():
    """A blocked node runs no sessions, so nothing refreshes the reading.
    Without expiry it would stay blocked permanently."""
    windows = {"five_hour": {"utilization": 0.99, "resets_at": 1000}}
    assert usage_gate.check_usage_gate(windows, CEILINGS, now=1001) is None
    assert usage_gate.check_usage_gate(windows, CEILINGS, now=999) is not None


def test_ceilings_come_from_the_injected_environment():
    assert usage_gate.ceilings_from_env(
        {"HOLONIC_CONTEXT_CEILING_FIVE_HOUR": "0.5",
         "HOLONIC_CONTEXT_CEILING_SEVEN_DAY": "0.6"}
    ) == {"five_hour": 0.5, "seven_day": 0.6}


def test_a_missing_or_unparseable_ceiling_falls_back_to_the_default():
    """Never to "no limit": a typo must not remove the ceiling."""
    assert usage_gate.ceilings_from_env({}) == usage_gate.DEFAULT_CEILINGS
    assert usage_gate.ceilings_from_env(
        {"HOLONIC_CONTEXT_CEILING_FIVE_HOUR": "soon"}
    ) == usage_gate.DEFAULT_CEILINGS


def test_reading_a_missing_usage_file_is_not_an_error(tmp_path: Path):
    assert usage_gate.read_usage(tmp_path / "absent.json") == {}


def test_reads_what_the_gateway_wrote(tmp_path: Path):
    path = tmp_path / "usage.json"
    windows = usage_gate.parse_rate_limit_event(LIVE_EVENT)
    path.write_text(json.dumps({"windows": windows}), encoding="utf-8")
    assert usage_gate.read_usage(path) == windows


# --- notify once, not every tick ---------------------------------------


def test_becoming_blocked_is_reported():
    message = usage_gate.blocked_transition(None, "five_hour usage at 71%")
    assert message and "71%" in message


def test_staying_blocked_is_silent():
    """The whole point: blocked can last hours, and a message per tick is
    one every few minutes -- noise that teaches its reader to skip it."""
    reason = "five_hour usage at 71%"
    assert usage_gate.blocked_transition(reason, reason) is None


def test_a_changed_reason_is_reported():
    """Five-hour giving way to seven-day is a different horizon."""
    assert usage_gate.blocked_transition(
        "five_hour usage at 71%", "seven_day usage at 81%"
    ) is not None


def test_recovery_is_reported_once():
    assert usage_gate.blocked_transition("five_hour usage at 71%", None) is not None


def test_staying_unblocked_is_silent():
    assert usage_gate.blocked_transition(None, None) is None


def test_blocked_state_round_trips(tmp_path: Path):
    path = tmp_path / "state" / "usage_gate.json"
    usage_gate.save_blocked_state(path, "five_hour usage at 71%")
    assert usage_gate.load_blocked_state(path) == "five_hour usage at 71%"
    usage_gate.save_blocked_state(path, None)
    assert usage_gate.load_blocked_state(path) is None


def test_missing_state_reads_as_not_blocked(tmp_path: Path):
    """At worst one extra notification, never silence."""
    assert usage_gate.load_blocked_state(tmp_path / "absent.json") is None


# --- reporting back -----------------------------------------------------


def test_reporting_with_no_bridge_or_no_windows_is_a_no_op():
    assert usage_gate.report_usage(None, {"five_hour": {"utilization": 0.1}}) is False
    assert usage_gate.report_usage("http://127.0.0.1:9005", {}) is False


def test_an_unreachable_bridge_does_not_raise():
    """The session that produced this reading already succeeded."""
    assert usage_gate.report_usage(
        "http://127.0.0.1:1", {"five_hour": {"utilization": 0.1}}, timeout=0.2
    ) is False
