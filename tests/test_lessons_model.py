from pathlib import Path

import pytest

from sclab.engine.types import QueryResult
from sclab.lessons.model import Exercise, check_answer, load_lessons


def result(columns: list[str], rows: list[tuple]) -> QueryResult:
    return QueryResult(
        columns=columns,
        rows=rows,
        total_rows=len(rows),
        truncated=False,
        elapsed_ms=1,
    )


def write_lesson(folder: Path, title: str, exercise_id: str, hints: list[str]) -> None:
    folder.mkdir(parents=True)
    (folder / "lesson.md").write_text(
        f'+++\ntitle = "{title}"\nsummary = "Short summary."\n+++\n\nLesson body.\n',
        encoding="utf-8",
    )
    hint_lines = ", ".join(f'"{hint}"' for hint in hints)
    (folder / "exercises.toml").write_text(
        "[[exercise]]\n"
        f'id = "{exercise_id}"\n'
        'title = "An exercise"\n'
        'prompt_md = "Write a query."\n'
        f"hints = [{hint_lines}]\n"
        'reference_sql = ["SELECT 1"]\n'
        "order_matters = false\n"
        "names_matter = false\n",
        encoding="utf-8",
    )


def test_load_sample_lesson() -> None:
    lessons = load_lessons(include_sample=True)
    assert [lesson.id for lesson in lessons] == ["00-sample"]
    lesson = lessons[0]
    assert lesson.number == 0
    assert lesson.title == "Sample lesson"
    assert lesson.summary == "A tiny lesson used to test the loader."
    assert "This sample is not part of the course." in lesson.body_md
    assert len(lesson.exercises) == 2
    plants, counted = lesson.exercises
    assert plants.id == "sample-plants"
    assert plants.title == "List the plants"
    assert plants.hints == (
        "Start from the plant table.",
        "Select the plant code column.",
        "You do not need an ORDER BY.",
    )
    assert plants.reference_sql == (
        "SELECT plant_code FROM plant",
        "SELECT plant_code FROM plant ORDER BY 1",
    )
    assert plants.order_matters is False
    assert plants.names_matter is False
    assert plants.excel_tip == "This is like listing one column in a sheet."
    counted_sql = ("SELECT count(*) AS plant_count FROM plant",)
    assert counted.id == "sample-count"
    assert counted.names_matter is True
    assert counted.excel_tip is None
    assert counted.reference_sql == counted_sql
    assert load_lessons() == []


def test_lessons_sort_by_number_and_skip_sample(tmp_path: Path) -> None:
    write_lesson(tmp_path / "02-later", "Later", "later", ["a", "b", "c"])
    write_lesson(tmp_path / "01-early", "Early", "early", ["a", "b", "c"])
    write_lesson(tmp_path / "00-sample", "Sample", "sample-ex", ["a", "b", "c"])
    lessons = load_lessons(tmp_path)
    assert [(lesson.number, lesson.id) for lesson in lessons] == [
        (1, "01-early"),
        (2, "02-later"),
    ]
    with_sample = load_lessons(tmp_path, include_sample=True)
    assert [lesson.id for lesson in with_sample] == ["00-sample", "01-early", "02-later"]


def test_exercise_requires_three_hints(tmp_path: Path) -> None:
    write_lesson(tmp_path / "01-bad", "Bad", "bad-hints", ["only one", "only two"])
    with pytest.raises(ValueError, match="bad-hints"):
        load_lessons(tmp_path)


def test_check_answer_passes_equivalent_query() -> None:
    exercise = Exercise(
        id="sample-count",
        title="Count",
        prompt_md="How many plants are there?",
        hints=("Use count.", "Count every row.", "The result is one number."),
        reference_sql=(
            "SELECT count(*) FROM plant",
            "SELECT count(*) AS plant_count FROM plant",
        ),
        order_matters=False,
        names_matter=False,
        excel_tip=None,
    )
    calls: list[str] = []

    def runner(sql: str) -> QueryResult:
        calls.append(sql)
        if sql in {"select count(*) from plant", "SELECT count(*) AS plant_count FROM plant"}:
            return result(["plant_count"], [(3,)])
        return result(["n"], [(9,)])

    outcome = check_answer(exercise, "select count(*) from plant", runner)
    assert outcome.passed is True
    assert outcome.summary == "Result matches."
    assert calls == [
        "select count(*) from plant",
        "SELECT count(*) FROM plant",
        "SELECT count(*) AS plant_count FROM plant",
    ]


def test_check_answer_reports_mismatch() -> None:
    exercise = Exercise(
        id="sample-plants",
        title="Plants",
        prompt_md="List the plant codes.",
        hints=("Start from plant.", "Select the code.", "Order does not matter."),
        reference_sql=(
            "SELECT plant_code FROM plant",
            "SELECT code FROM plant",
        ),
        order_matters=False,
        names_matter=False,
        excel_tip=None,
    )
    calls: list[str] = []

    def runner(sql: str) -> QueryResult:
        calls.append(sql)
        if sql == "SELECT wrong":
            return result(["plant_code"], [("P9",)])
        if sql == "SELECT plant_code FROM plant":
            return result(["plant_code"], [("P1",)])
        return result(["plant_code"], [("P1",), ("P2",)])

    outcome = check_answer(exercise, "SELECT wrong", runner)
    assert outcome.passed is False
    assert outcome.missing_rows == [("P1",)]
    assert outcome.extra_rows == [("P9",)]
    assert outcome.summary == "1 missing row and 1 extra row."
    assert calls == [
        "SELECT wrong",
        "SELECT plant_code FROM plant",
        "SELECT code FROM plant",
    ]
