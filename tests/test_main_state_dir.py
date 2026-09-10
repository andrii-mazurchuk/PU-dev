"""Where pu keeps its state, and where the Taskwarrior binary is told to
find its half of it.

The unit keeps all private state under one directory, learned from
HOLONIC_STATE_DIR -- see holonic-node/docs/UNIT_STANDARDS.md, "Private
storage". pu is the awkward case: one part of that state is read by a
binary rather than by us, and under the WSL arrangement that binary is on
the other side of a filesystem boundary. So the path pu writes to and the
path pu reports are deliberately not the same string, and these tests pin
both.
"""

from __future__ import annotations

from pathlib import Path

from pu import taskstore
from pu.main import (
    DEFAULT_STATE_DIR,
    TASKDATA_SUBDIR,
    default_state_dir,
    warn_if_legacy_taskdata_is_unmigrated,
)


# --- which directory ---------------------------------------------------


def test_state_dir_comes_from_the_injected_environment():
    assert default_state_dir({"HOLONIC_STATE_DIR": "/var/lib/holonic/pu"}) == (
        "/var/lib/holonic/pu"
    )


def test_the_older_pu_state_dir_still_wins_when_the_standard_one_is_absent():
    """A deployment pinning PU_STATE_DIR predates the standard and must
    not silently start writing somewhere else."""
    assert default_state_dir({"PU_STATE_DIR": "/opt/pu-state"}) == "/opt/pu-state"


def test_the_standard_variable_takes_precedence_over_the_older_one():
    assert default_state_dir(
        {"HOLONIC_STATE_DIR": "/injected", "PU_STATE_DIR": "/legacy"}
    ) == "/injected"


def test_it_falls_back_so_the_unit_runs_without_a_gateway():
    assert default_state_dir({}) == DEFAULT_STATE_DIR


# --- the WSL boundary --------------------------------------------------


def test_the_taskdata_path_is_translated_for_a_binary_behind_wsl():
    """A Windows path means nothing to the Linux binary. Handing it one
    does not fail loudly -- Taskwarrior would open a different store."""
    translated = taskstore.path_for_binary(
        Path("C:/agents/units/pu/state") / TASKDATA_SUBDIR, ("wsl", "-d", "Ubuntu", "-e", "task")
    )
    assert translated == f"/mnt/c/agents/units/pu/state/{TASKDATA_SUBDIR}"


def test_the_translation_does_not_depend_on_the_platform_running_it():
    """The bug this pins: the drive letter used to come from
    `Path(text).drive`, which is "C:" on Windows and "" on POSIX. The
    same input therefore translated on a Windows host and passed through
    untouched on Linux -- and this test could only ever pass on one of
    them, which is how it sat red in CI.

    This function speaks for the binary on the *other* side of the
    boundary. What it answers must depend on the path it is given, never
    on the machine doing the answering."""
    wsl = ("wsl", "-d", "Ubuntu", "-e", "task")
    for written in ("C:/agents/pu/state", r"C:\agents\pu\state"):
        assert taskstore.path_for_binary(written, wsl) == "/mnt/c/agents/pu/state"
    assert taskstore.path_for_binary("d:/x/y", wsl) == "/mnt/d/x/y"


def test_an_already_posix_path_is_left_alone_behind_wsl():
    r"""It is already written for the binary's side. Resolving it would be
    actively wrong: on a Windows host `Path("/srv/x").resolve()` is
    `C:\srv\x`, so the old code answered `/mnt/c/srv/x` and pointed the
    binary at a store that does not exist -- silently, which is the whole
    class of mistake this function exists to prevent."""
    assert taskstore.path_for_binary(
        "/srv/pu/state/taskdata", ("wsl", "-d", "Ubuntu", "-e", "task")
    ) == "/srv/pu/state/taskdata"


def test_the_translation_is_the_identity_for_a_native_binary():
    """The container and Linux case: state/taskdata, unchanged. This is
    the path that matters once pu stops running behind WSL at all."""
    native = taskstore.path_for_binary(f"/srv/pu/state/{TASKDATA_SUBDIR}", ("task",))
    assert native == f"/srv/pu/state/{TASKDATA_SUBDIR}"


# --- the migration warning ---------------------------------------------


def test_no_warning_when_there_is_no_legacy_store(tmp_path, monkeypatch):
    monkeypatch.setattr("pu.main.LEGACY_TASKDATA", str(tmp_path / "absent"))
    taskdata = tmp_path / "taskdata"
    taskdata.mkdir()
    assert warn_if_legacy_taskdata_is_unmigrated(taskdata) is False


def test_a_store_task_has_only_touched_still_counts_as_empty(tmp_path, monkeypatch):
    """`task` creates pending.data and completed.data empty on first
    contact, so counting directory entries would call a store it has
    never written to populated -- and suppress the one warning that
    matters."""
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "pending.data").write_text("[]", encoding="utf-8")
    monkeypatch.setattr("pu.main.LEGACY_TASKDATA", str(legacy))
    taskdata = tmp_path / "taskdata"
    taskdata.mkdir()
    (taskdata / "pending.data").write_text("", encoding="utf-8")
    (taskdata / "completed.data").write_text("", encoding="utf-8")

    assert warn_if_legacy_taskdata_is_unmigrated(taskdata) is True


def test_behind_wsl_it_says_it_cannot_see_rather_than_reporting_all_clear(
    tmp_path, monkeypatch, capsys
):
    """The old default's `~` was the *binary's* home under WSL, not this
    process's. Expanding it here checks the wrong machine, so a confident
    "nothing to migrate" would be a lie in the one arrangement where the
    migration is real."""
    monkeypatch.setattr("pu.main.LEGACY_TASKDATA", str(tmp_path / "never-checked"))
    taskdata = tmp_path / "taskdata"
    taskdata.mkdir()

    assert warn_if_legacy_taskdata_is_unmigrated(
        taskdata, ("wsl", "-d", "Ubuntu", "-e", "task")
    ) is True
    assert "cannot see there from here" in capsys.readouterr().out


def test_behind_wsl_a_populated_store_says_nothing(tmp_path, monkeypatch):
    taskdata = tmp_path / "taskdata"
    taskdata.mkdir()
    (taskdata / "pending.data").write_text("[{}]", encoding="utf-8")

    assert warn_if_legacy_taskdata_is_unmigrated(
        taskdata, ("wsl", "-d", "Ubuntu", "-e", "task")
    ) is False


def test_warns_when_the_legacy_store_holds_data_and_the_new_one_is_empty(
    tmp_path, monkeypatch, capsys
):
    """The failure this exists to prevent: pu starts clean, reports itself
    healthy, and the only symptom is a queue that used to have work."""
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "pending.data").write_text("[]", encoding="utf-8")
    monkeypatch.setattr("pu.main.LEGACY_TASKDATA", str(legacy))
    taskdata = tmp_path / "taskdata"
    taskdata.mkdir()

    assert warn_if_legacy_taskdata_is_unmigrated(taskdata) is True
    assert "WARNING" in capsys.readouterr().out


def test_silent_once_the_new_store_has_been_populated(tmp_path, monkeypatch):
    """Migrated, or simply used since. Either way there is nothing to say,
    and a warning that never stops is one nobody reads."""
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "pending.data").write_text("[]", encoding="utf-8")
    monkeypatch.setattr("pu.main.LEGACY_TASKDATA", str(legacy))
    taskdata = tmp_path / "taskdata"
    taskdata.mkdir()
    (taskdata / "pending.data").write_text("[]", encoding="utf-8")

    assert warn_if_legacy_taskdata_is_unmigrated(taskdata) is False


def test_an_empty_legacy_directory_is_not_worth_warning_about(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    monkeypatch.setattr("pu.main.LEGACY_TASKDATA", str(legacy))
    taskdata = tmp_path / "taskdata"
    taskdata.mkdir()
    assert warn_if_legacy_taskdata_is_unmigrated(taskdata) is False
