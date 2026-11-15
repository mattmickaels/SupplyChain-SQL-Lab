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
    lesson = next(item for item in lessons if item.id == "00-sample")
    assert lesson.number == 0
    assert lesson.title == "Sample lesson"
    assert lesson.summary == "A tiny lesson used to test the loader."
    assert "This sample is not part of the course." in lesson.body_md
    assert len(lesson.exercises) == 3
    plants, counted, drill = lesson.exercises
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
    assert drill.id == "sample-drill"
    assert drill.is_drill is True
    assert "00-sample" not in {item.id for item in load_lessons()}


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


def test_duplicate_exercise_id_names_both_lessons(tmp_path: Path) -> None:
    write_lesson(tmp_path / "01-early", "Early", "same-id", ["a", "b", "c"])
    write_lesson(tmp_path / "02-later", "Later", "same-id", ["a", "b", "c"])
    with pytest.raises(ValueError, match="same-id") as caught:
        load_lessons(tmp_path)
    message = str(caught.value)
    assert "01-early" in message
    assert "02-later" in message


def test_exercise_requires_three_hints(tmp_path: Path) -> None:
    write_lesson(tmp_path / "01-bad", "Bad", "bad-hints", ["only one", "only two"])
    with pytest.raises(ValueError, match="bad-hints"):
        load_lessons(tmp_path)


def test_drill_fields_parsed() -> None:
    lessons = load_lessons(include_sample=True)
    lesson = next(item for item in lessons if item.id == "00-sample")
    drill = next(item for item in lesson.exercises if item.id == "sample-drill")
    assert drill.is_drill is True
    assert drill.time_limit_s == 900
    assert drill.databricks_note == "count(*) is spelled the same way on Databricks."
    assert drill.summary_prompt == (
        "Write three lines: the finding, the number that matters, and what you would do."
    )
    assert drill.model_summary == (
        "There are 3 plants.\nThe number that matters is 3.\nNo action is needed."
    )
    assert drill.summary_checklist == (
        "Names the number that matters",
        "Recommends an action",
        "Three lines or fewer",
    )
    plants = next(item for item in lesson.exercises if item.id == "sample-plants")
    assert plants.is_drill is False
    assert plants.time_limit_s is None
    assert plants.databricks_note is None
    assert plants.summary_prompt is None
    assert plants.model_summary is None
    assert plants.summary_checklist == ()


def _write_drill(
    folder: Path,
    *,
    summary_prompt: str | None = "Write three lines.",
    model_summary: str | None = "There are 3 plants.\nThe number is 3.\nNo action.",
    checklist_count: int = 3,
) -> None:
    folder.mkdir(parents=True)
    (folder / "lesson.md").write_text(
        '+++\ntitle = "Timed"\nsummary = "A drill."\n+++\n\nBody.\n',
        encoding="utf-8",
    )
    prompt_line = "" if summary_prompt is None else f'summary_prompt = "{summary_prompt}"\n'
    summary_line = "" if model_summary is None else 'model_summary = "A summary."\n'
    items = ", ".join(f'"Item {index}"' for index in range(checklist_count))
    (folder / "exercises.toml").write_text(
        "[[exercise]]\n"
        'id = "timed-plants"\n'
        'title = "Timed plant count"\n'
        'prompt_md = "Count the plants."\n'
        'hints = ["Use count.", "Count every row.", "Then summarize."]\n'
        'reference_sql = ["SELECT count(*) AS plant_count FROM plant"]\n'
        "order_matters = false\n"
        "names_matter = true\n"
        "time_limit_s = 900\n"
        f"{prompt_line}"
        f"{summary_line}"
        f"summary_checklist = [{items}]\n",
        encoding="utf-8",
    )


def test_drill_without_model_summary_rejected(tmp_path: Path) -> None:
    missing_summary = tmp_path / "missing-summary"
    _write_drill(missing_summary / "01-timed", model_summary=None)
    with pytest.raises(ValueError, match="timed-plants"):
        load_lessons(missing_summary)

    missing_prompt = tmp_path / "missing-prompt"
    _write_drill(missing_prompt / "01-timed", summary_prompt=None)
    with pytest.raises(ValueError, match="timed-plants"):
        load_lessons(missing_prompt)

    short_list = tmp_path / "short-list"
    _write_drill(short_list / "01-timed", checklist_count=2)
    with pytest.raises(ValueError, match="timed-plants"):
        load_lessons(short_list)

    long_list = tmp_path / "long-list"
    _write_drill(long_list / "01-timed", checklist_count=6)
    with pytest.raises(ValueError, match="timed-plants"):
        load_lessons(long_list)


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


def _count_exercise() -> Exercise:
    return Exercise(
        id="sample-count",
        title="Count",
        prompt_md="How many plants are there?",
        hints=("Use count.", "Count every row.", "The result is one number."),
        reference_sql=("SELECT count(*) FROM plant",),
        order_matters=False,
        names_matter=False,
        excel_tip=None,
    )


def test_truncated_learner_result_is_not_a_pass() -> None:
    calls: list[str] = []

    def runner(sql: str) -> QueryResult:
        calls.append(sql)
        return QueryResult(
            columns=["n"],
            rows=[(1,)],
            total_rows=200_001,
            truncated=True,
            elapsed_ms=1,
        )

    outcome = check_answer(_count_exercise(), "SELECT * FROM big", runner)
    assert outcome.passed is False
    assert outcome.summary == (
        "Your result has more than 200,000 rows. Add a filter or GROUP BY so it can be checked."
    )
    assert outcome.missing_rows == []
    assert outcome.extra_rows == []
    assert calls == ["SELECT * FROM big"]


def test_truncated_reference_result_names_the_exercise() -> None:
    def runner(sql: str) -> QueryResult:
        if sql == "SELECT count(*) FROM plant":
            return QueryResult(
                columns=["n"],
                rows=[(3,)],
                total_rows=200_001,
                truncated=True,
                elapsed_ms=1,
            )
        return result(["n"], [(3,)])

    with pytest.raises(ValueError, match="sample-count"):
        check_answer(_count_exercise(), "select count(*) from plant", runner)
