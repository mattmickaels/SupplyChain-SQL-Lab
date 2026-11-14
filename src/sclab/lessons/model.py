import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sclab.engine.checker import CheckOutcome, compare
from sclab.engine.types import QueryResult

_FOLDER_NAME = re.compile(r"^(\d{2})-.+$")
_MAX_ROWS = 200_000


@dataclass(frozen=True)
class Exercise:
    id: str
    title: str
    prompt_md: str
    hints: tuple[str, str, str]
    reference_sql: tuple[str, ...]
    order_matters: bool
    names_matter: bool
    excel_tip: str | None


@dataclass(frozen=True)
class Lesson:
    id: str
    number: int
    title: str
    summary: str
    body_md: str
    exercises: tuple[Exercise, ...]


def load_lessons(content_dir: Path | None = None, include_sample: bool = False) -> list[Lesson]:
    root = Path(__file__).resolve().parent / "content" if content_dir is None else content_dir
    if not root.is_dir():
        return []
    lessons: list[Lesson] = []
    for folder in root.iterdir():
        if not folder.is_dir():
            continue
        if folder.name == "00-sample" and not include_sample:
            continue
        lessons.append(_lesson_from_folder(folder))
    lessons.sort(key=lambda lesson: (lesson.number, lesson.id))
    return lessons


def check_answer(
    exercise: Exercise,
    sql: str,
    runner: Callable[[str], QueryResult] | None = None,
) -> CheckOutcome:
    if runner is None:
        from sclab.engine.query import run_query

        def runner(statement: str) -> QueryResult:
            return run_query(statement, max_rows=_MAX_ROWS)

    actual = runner(sql)
    first_failure: CheckOutcome | None = None
    for index, reference in enumerate(exercise.reference_sql):
        outcome = compare(
            actual,
            runner(reference),
            order_matters=exercise.order_matters,
            names_matter=exercise.names_matter,
        )
        if outcome.passed:
            return outcome
        if index == 0:
            first_failure = outcome
    if first_failure is None:
        raise ValueError(f"Exercise {exercise.id} needs at least one reference query.")
    return first_failure


def _lesson_from_folder(folder: Path) -> Lesson:
    match = _FOLDER_NAME.match(folder.name)
    if match is None:
        raise ValueError(f"Lesson folder {folder.name} must look like NN-slug.")
    title, summary, body = _read_lesson_markdown(folder / "lesson.md", folder.name)
    exercises = _read_exercises(folder / "exercises.toml", folder.name)
    return Lesson(
        id=folder.name,
        number=int(match.group(1)),
        title=title,
        summary=summary,
        body_md=body,
        exercises=exercises,
    )


def _read_lesson_markdown(path: Path, folder_name: str) -> tuple[str, str, str]:
    if not path.is_file():
        raise ValueError(f"Lesson {folder_name} needs lesson.md.")
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "+++":
        raise ValueError(f"Lesson {folder_name} must start with +++.")
    try:
        end = lines.index("+++", 1)
    except ValueError as exc:
        raise ValueError(f"Lesson {folder_name} is missing the closing +++.") from exc
    try:
        meta = tomllib.loads("\n".join(lines[1:end]))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Lesson {folder_name} has invalid front matter.") from exc
    title = meta.get("title")
    summary = meta.get("summary")
    if not isinstance(title, str) or not isinstance(summary, str):
        raise ValueError(f"Lesson {folder_name} needs a title and summary.")
    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    return title, summary, body


def _read_exercises(path: Path, folder_name: str) -> tuple[Exercise, ...]:
    if not path.is_file():
        raise ValueError(f"Lesson {folder_name} needs exercises.toml.")
    with path.open("rb") as handle:
        try:
            data = tomllib.load(handle)
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"Lesson {folder_name} has invalid exercises.toml.") from exc
    rows = data.get("exercise", [])
    if not isinstance(rows, list):
        raise ValueError(f"Lesson {folder_name} exercises must be a list.")
    return tuple(_parse_exercise(row, folder_name) for row in rows)


def _parse_exercise(row: object, folder_name: str) -> Exercise:
    if not isinstance(row, dict):
        raise ValueError(f"Lesson {folder_name} has an exercise that is not a table.")
    exercise_id = row.get("id")
    if not isinstance(exercise_id, str) or not exercise_id:
        raise ValueError(f"Lesson {folder_name} has an exercise with no id.")
    title = row.get("title")
    prompt = row.get("prompt_md")
    if not isinstance(title, str) or not isinstance(prompt, str):
        raise ValueError(f"Exercise {exercise_id} needs a title and prompt_md.")
    return Exercise(
        id=exercise_id,
        title=title,
        prompt_md=prompt,
        hints=_three_hints(row.get("hints"), exercise_id),
        reference_sql=_reference_sql(row.get("reference_sql"), exercise_id),
        order_matters=_flag(row.get("order_matters", False), exercise_id, "order_matters"),
        names_matter=_flag(row.get("names_matter", False), exercise_id, "names_matter"),
        excel_tip=_optional_text(row.get("excel_tip"), exercise_id),
    )


def _three_hints(value: object, exercise_id: str) -> tuple[str, str, str]:
    hints_ok = (
        isinstance(value, list) and len(value) == 3 and all(isinstance(item, str) for item in value)
    )
    if not hints_ok:
        raise ValueError(f"Exercise {exercise_id} needs exactly 3 hints.")
    return (value[0], value[1], value[2])


def _reference_sql(value: object, exercise_id: str) -> tuple[str, ...]:
    sql_ok = (
        isinstance(value, list)
        and len(value) >= 1
        and all(isinstance(item, str) and item.strip() for item in value)
    )
    if not sql_ok:
        raise ValueError(f"Exercise {exercise_id} needs at least one reference query.")
    return tuple(value)


def _flag(value: object, exercise_id: str, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"Exercise {exercise_id} needs {label} to be true or false.")
    return value


def _optional_text(value: object, exercise_id: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Exercise {exercise_id} excel_tip must be text.")
    return value
