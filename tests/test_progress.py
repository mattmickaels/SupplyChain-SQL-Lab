import threading
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


def test_invalid_progress_fields_are_quarantined(tmp_path: Path) -> None:
    path = tmp_path / "progress.json"
    original = '{"exercises":{"x":{"passed":"false"}}}'
    path.write_text(original, encoding="utf-8")
    store = ProgressStore(path)
    assert store.status("x") == "new"
    assert store.summary() == {"attempted": 0, "passed": 0}
    corrupt = list(tmp_path.glob("progress.corrupt-*.json"))
    assert len(corrupt) == 1
    assert corrupt[0].read_text(encoding="utf-8") == original


def test_second_store_sees_first_store_pass(tmp_path: Path) -> None:
    path = tmp_path / "progress.json"
    first = ProgressStore(path)
    second = ProgressStore(path)
    first.record_attempt("plants", "SELECT plant_code FROM plant", True)
    assert second.status("plants") == "passed"
    assert second.last_sql("plants") == "SELECT plant_code FROM plant"
    assert second.summary() == {"attempted": 0, "passed": 1}


def test_two_stores_keep_interleaved_attempts(tmp_path: Path) -> None:
    path = tmp_path / "progress.json"
    first = ProgressStore(path)
    second = ProgressStore(path)
    first.record_attempt("plants", "SELECT 1", True)
    second.record_attempt("plants", "SELECT bad", False)
    second.record_attempt("count", "SELECT 2", False)
    first.record_attempt("orders", "SELECT 3", True)

    saved = ProgressStore(path)
    assert saved.status("plants") == "passed"
    assert saved.last_sql("plants") == "SELECT bad"
    assert saved.status("count") == "attempted"
    assert saved.last_sql("count") == "SELECT 2"
    assert saved.status("orders") == "passed"
    assert saved.last_sql("orders") == "SELECT 3"
    assert saved.summary() == {"attempted": 1, "passed": 2}


def test_eight_threads_keep_every_attempt(tmp_path: Path) -> None:
    path = tmp_path / "progress.json"
    stores = [ProgressStore(path) for _ in range(8)]
    barrier = threading.Barrier(8)
    errors: list[BaseException] = []

    def work(index: int) -> None:
        try:
            barrier.wait(timeout=5)
            stores[index].record_attempt(f"ex-{index}", f"SELECT {index}", index % 2 == 0)
            stores[index].record_attempt("shared", f"SELECT shared {index}", index == 0)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []

    saved = ProgressStore(path)
    for index in range(8):
        expected = "passed" if index % 2 == 0 else "attempted"
        assert saved.status(f"ex-{index}") == expected
        assert saved.last_sql(f"ex-{index}") == f"SELECT {index}"
    assert saved.status("shared") == "passed"
