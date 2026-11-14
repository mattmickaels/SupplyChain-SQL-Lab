from pathlib import Path

import pytest

from sclab.paths import data_dir, lab_db_path


def test_sclab_home_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCLAB_HOME", str(tmp_path))
    assert data_dir() == tmp_path
    assert lab_db_path() == tmp_path / "lab.duckdb"


def test_paths_with_spaces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "My Folder"
    monkeypatch.setenv("SCLAB_HOME", str(folder))
    assert data_dir() == folder
    assert folder.is_dir()
    assert lab_db_path() == folder / "lab.duckdb"
