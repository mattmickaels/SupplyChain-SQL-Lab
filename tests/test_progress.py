from pathlib import Path

from sclab.lessons.progress import ProgressStore


def test_progress_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "progress.json"
    store = ProgressStore(path)
    assert store.status("missing") == "new"
    assert store.last_sql("missing") is None
    store.record_attempt("sample-plants", "SELECT plant_code FROM plant", False)
    store.record_attempt("sample-count", "SELECT count(*) FROM plant", True)
    assert store.summary() == {"attempted": 1, "passed": 1}

    reloaded = ProgressStore(path)
    assert reloaded.status("sample-plants") == "attempted"
    assert reloaded.status("sample-count") == "passed"
    assert reloaded.status("missing") == "new"
    assert reloaded.last_sql("sample-plants") == "SELECT plant_code FROM plant"
    assert reloaded.last_sql("sample-count") == "SELECT count(*) FROM plant"
    assert reloaded.last_sql("missing") is None
    assert reloaded.summary() == {"attempted": 1, "passed": 1}


def test_passed_stays_passed_after_later_failed_attempt(tmp_path: Path) -> None:
    path = tmp_path / "progress.json"
    store = ProgressStore(path)
    store.record_attempt("sample-plants", "SELECT plant_code FROM plant", True)
    store.record_attempt("sample-plants", "SELECT wrong", False)
    assert store.status("sample-plants") == "passed"
    assert store.last_sql("sample-plants") == "SELECT wrong"
    assert store.summary() == {"attempted": 0, "passed": 1}

    reloaded = ProgressStore(path)
    assert reloaded.status("sample-plants") == "passed"
    assert reloaded.last_sql("sample-plants") == "SELECT wrong"


def test_corrupt_progress_file_recovered(tmp_path: Path) -> None:
    path = tmp_path / "progress.json"
    path.write_text("{not json", encoding="utf-8")
    store = ProgressStore(path)
    assert store.status("sample-plants") == "new"
    assert store.summary() == {"attempted": 0, "passed": 0}
    corrupt = list(tmp_path.glob("progress.corrupt-*.json"))
    assert len(corrupt) == 1
    assert corrupt[0].read_text(encoding="utf-8") == "{not json"
    store.record_attempt("sample-plants", "SELECT 1", False)
    assert path.read_text(encoding="utf-8")
    assert store.status("sample-plants") == "attempted"
    assert corrupt[0].read_text(encoding="utf-8") == "{not json"
