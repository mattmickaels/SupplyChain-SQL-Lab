"""Reference queries stay portable, ordered, and pinned to a snapshot."""

import hashlib
import json
import re
from pathlib import Path

import pytest

from sclab.engine.checker import compare, normalize_value
from sclab.engine.query import QueryError, run_query
from sclab.lessons.model import Exercise, load_lessons

_SNAPSHOT = Path(__file__).parent / "snapshots" / "exercises.json"
_MAX_ROWS = 200_000
_FORBIDDEN = re.compile(
    r"GROUP\s+BY\s+ALL|\bEXCLUDE\b|\bQUALIFY\b|\bCURRENT_DATE\b|\blist_|\bstrftime\b|\bdate_diff\b",
    re.IGNORECASE,
)
_ORDER_BY = re.compile(r"\bORDER\s+BY\b", re.IGNORECASE)
_LIMIT = re.compile(r"\bLIMIT\s+(\d+)\b", re.IGNORECASE)
_TRAILING_LIMIT = re.compile(r"\s+LIMIT\s+\d+\s*;?\s*$", re.IGNORECASE)
_DIRECTION = re.compile(
    r"\s+(?:ASC|DESC|NULLS\s+FIRST|NULLS\s+LAST)\s*$",
    re.IGNORECASE,
)


def _exercises() -> list[Exercise]:
    return [exercise for lesson in load_lessons() for exercise in lesson.exercises]


def _run(sql: str):
    try:
        return run_query(sql, max_rows=_MAX_ROWS)
    except QueryError as exc:
        pytest.fail(f"{exc.message}\n{sql}")


def _fingerprint(result) -> dict[str, int | str]:
    rows = [[normalize_value(value) for value in row] for row in result.rows]
    payload = json.dumps(rows, separators=(",", ":"), ensure_ascii=False)
    return {
        "row_count": result.total_rows,
        "md5": hashlib.md5(payload.encode("utf-8")).hexdigest(),
    }


def _split_order_items(clause: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in clause:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _order_item(token: str) -> str:
    text = token.strip()
    while True:
        trimmed = _DIRECTION.sub("", text).strip()
        if trimmed == text:
            return text
        text = trimmed


def _order_column_indexes(sql: str, columns: list[str]) -> list[int]:
    matches = list(_ORDER_BY.finditer(sql))
    if not matches:
        pytest.fail(f"LIMIT query has no ORDER BY:\n{sql}")
    rest = sql[matches[-1].end() :]
    clause = re.split(r"\bLIMIT\b", rest, maxsplit=1, flags=re.IGNORECASE)[0]
    names = {name.casefold(): index for index, name in enumerate(columns)}
    indexes: list[int] = []
    for part in _split_order_items(clause.strip().rstrip(";")):
        token = _order_item(part).strip().strip('"')
        if token.isdigit():
            indexes.append(int(token) - 1)
            continue
        index = names.get(token.casefold())
        if index is None:
            pytest.fail(f"ORDER BY {token} is not a result column:\n{sql}")
        indexes.append(index)
    if not indexes:
        pytest.fail(f"ORDER BY list is empty:\n{sql}")
    return indexes


@pytest.fixture(scope="module")
def reference_results(lab_home):
    assert lab_home is not None
    cached: dict[str, object] = {}
    for exercise in _exercises():
        for sql in exercise.reference_sql:
            cached[sql] = _run(sql)
    return cached


def test_every_reference_query_runs(reference_results) -> None:
    for exercise in _exercises():
        for sql in exercise.reference_sql:
            result = reference_results[sql]
            assert result.total_rows > 0, exercise.id
            assert result.truncated is False, exercise.id
            assert result.elapsed_ms < 2000, f"{exercise.id} took {result.elapsed_ms} ms"


def test_alternate_references_agree(reference_results) -> None:
    for exercise in _exercises():
        first = reference_results[exercise.reference_sql[0]]
        for sql in exercise.reference_sql[1:]:
            outcome = compare(
                reference_results[sql],
                first,
                order_matters=exercise.order_matters,
                names_matter=exercise.names_matter,
            )
            assert outcome.passed, f"{exercise.id}: {outcome.summary}"


def test_reference_results_match_snapshot(request, reference_results) -> None:
    exercises = _exercises()
    ids = [exercise.id for exercise in exercises]
    assert len(ids) == len(set(ids))
    found = {
        exercise.id: _fingerprint(reference_results[exercise.reference_sql[0]])
        for exercise in exercises
    }
    if request.config.getoption("--update-snapshots"):
        _SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(found, indent=2, sort_keys=True) + "\n"
        _SNAPSHOT.write_text(text, encoding="utf-8")
        return
    if not found:
        return
    assert _SNAPSHOT.is_file(), "Missing snapshots/exercises.json. Re-run with --update-snapshots."
    saved = json.loads(_SNAPSHOT.read_text(encoding="utf-8"))
    assert found == saved


def test_reference_sql_is_portable() -> None:
    for exercise in _exercises():
        for sql in exercise.reference_sql:
            assert _FORBIDDEN.search(sql) is None, f"{exercise.id} uses non-portable SQL"


def test_ordered_exercises_have_order_by() -> None:
    for exercise in _exercises():
        if not exercise.order_matters:
            continue
        for sql in exercise.reference_sql:
            assert _ORDER_BY.search(sql), f"{exercise.id} sets order_matters but has no ORDER BY"


def test_no_ties_at_limit(lab_home) -> None:
    assert lab_home is not None
    for exercise in _exercises():
        for sql in exercise.reference_sql:
            if _LIMIT.search(sql) is None:
                continue
            stripped = sql.strip()
            trailing = _TRAILING_LIMIT.search(stripped)
            assert trailing is not None, f"{exercise.id} uses LIMIT away from the end of the query"
            limit = int(_LIMIT.findall(stripped)[-1])
            probe = f"{_TRAILING_LIMIT.sub('', stripped).strip()} LIMIT {limit + 1}"
            result = _run(probe)
            if len(result.rows) <= limit:
                continue
            indexes = _order_column_indexes(sql, result.columns)
            at_limit = tuple(result.rows[limit - 1][index] for index in indexes)
            after = tuple(result.rows[limit][index] for index in indexes)
            assert at_limit != after, f"{exercise.id} has a tie at LIMIT {limit}"
