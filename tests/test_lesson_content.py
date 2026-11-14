"""Reference queries stay portable, ordered, and pinned to a snapshot."""

import hashlib
import json
import re
from pathlib import Path

import duckdb
import pytest

from sclab.engine.checker import compare, normalize_value
from sclab.engine.query import QueryError, run_query
from sclab.engine.types import QueryResult
from sclab.lessons.model import Exercise, load_lessons

_SNAPSHOT = Path(__file__).parent / "snapshots" / "exercises.json"
_MAX_ROWS = 200_000
_FORBIDDEN = re.compile(
    r"GROUP\s+BY\s+ALL|\bEXCLUDE\b|\bQUALIFY\b|\bCURRENT_DATE\b|\blist_|\bstrftime\b|\bdate_diff\b",
    re.IGNORECASE,
)
_ORDER_BY = re.compile(r"\bORDER\s+BY\b", re.IGNORECASE)
_LIMIT = re.compile(r"\bLIMIT\s+(\d+)\b", re.IGNORECASE)
_LIKE = re.compile(r"\bLIKE\b", re.IGNORECASE)
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


def _fingerprint(result, *, order_matters: bool) -> dict[str, int | str]:
    rows = [[normalize_value(value) for value in row] for row in result.rows]
    if not order_matters:
        rows = sorted(rows, key=lambda row: json.dumps(row, separators=(",", ":")))
    payload = json.dumps(rows, separators=(",", ":"), ensure_ascii=False)
    return {
        "row_count": result.total_rows,
        "md5": hashlib.md5(payload.encode("utf-8")).hexdigest(),
    }


def _neutralize(sql: str) -> str:
    """Blank comments, string literals, and nested paren text, keeping length."""
    chars = list(sql)
    index = 0
    length = len(sql)
    while index < length:
        if sql.startswith("--", index):
            end = sql.find("\n", index)
            if end < 0:
                end = length
            for spot in range(index, end):
                chars[spot] = " "
            index = end
            continue
        if sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            end = length if end < 0 else end + 2
            for spot in range(index, end):
                chars[spot] = " "
            index = end
            continue
        if sql[index] == "'":
            start = index
            index += 1
            while index < length:
                if sql.startswith("''", index):
                    index += 2
                    continue
                if sql[index] == "'":
                    index += 1
                    break
                index += 1
            for spot in range(start, index):
                chars[spot] = " "
            continue
        index += 1
    depth = 0
    for spot, char in enumerate(chars):
        if char == "(":
            depth += 1
            chars[spot] = " "
        elif char == ")":
            if depth:
                depth -= 1
            chars[spot] = " "
        elif depth:
            chars[spot] = " "
    return "".join(chars)


def _top_level_order_clause(sql: str) -> str | None:
    masked = _neutralize(sql)
    matches = list(_ORDER_BY.finditer(masked))
    if not matches:
        return None
    start = matches[-1].end()
    limit = re.search(r"\bLIMIT\b", masked[start:], re.IGNORECASE)
    end = start + limit.start() if limit else len(masked)
    return masked[start:end]


def _has_top_level_order_by(sql: str) -> bool:
    return _top_level_order_clause(sql) is not None


def _top_level_limit(sql: str) -> re.Match[str] | None:
    matches = list(_LIMIT.finditer(_neutralize(sql)))
    if not matches:
        return None
    return matches[-1]


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
    clause = _top_level_order_clause(sql)
    if clause is None:
        pytest.fail(f"LIMIT query has no top-level ORDER BY:\n{sql}")
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
        exercise.id: _fingerprint(
            reference_results[exercise.reference_sql[0]],
            order_matters=exercise.order_matters,
        )
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
            assert _has_top_level_order_by(sql), (
                f"{exercise.id} sets order_matters but has no top-level ORDER BY"
            )


def test_no_ties_at_limit(lab_home) -> None:
    assert lab_home is not None
    for exercise in _exercises():
        for sql in exercise.reference_sql:
            limit_match = _top_level_limit(sql)
            if limit_match is None:
                continue
            assert _has_top_level_order_by(sql), (
                f"{exercise.id} uses LIMIT without a top-level ORDER BY"
            )
            limit = int(limit_match.group(1))
            without_limit = (sql[: limit_match.start()] + sql[limit_match.end() :]).strip()
            probe = f"{without_limit.rstrip(';')} LIMIT {limit + 1}"
            result = _run(probe)
            if len(result.rows) <= limit:
                continue
            indexes = _order_column_indexes(sql, result.columns)
            at_limit = tuple(result.rows[limit - 1][index] for index in indexes)
            after = tuple(result.rows[limit][index] for index in indexes)
            assert at_limit != after, f"{exercise.id} has a tie at LIMIT {limit}"


def _sample(rows: list[tuple]) -> QueryResult:
    return QueryResult(
        columns=["n"],
        rows=rows,
        total_rows=len(rows),
        truncated=False,
        elapsed_ms=1,
    )


def test_unordered_fingerprint_ignores_row_order() -> None:
    forward = _sample([(1,), (1,), (2,)])
    backward = _sample([(2,), (1,), (1,)])
    unique = _sample([(1,), (2,)])
    assert _fingerprint(forward, order_matters=False) == _fingerprint(backward, order_matters=False)
    assert _fingerprint(forward, order_matters=False) != _fingerprint(unique, order_matters=False)


def test_ordered_fingerprint_keeps_row_order() -> None:
    forward = _fingerprint(_sample([(1,), (2,)]), order_matters=True)
    backward = _fingerprint(_sample([(2,), (1,)]), order_matters=True)
    assert forward != backward


def test_order_by_inside_subquery_does_not_count() -> None:
    sql = "SELECT * FROM (SELECT material_no FROM material ORDER BY material_no) AS nested"
    assert _has_top_level_order_by(sql) is False


def test_order_by_inside_comment_does_not_count() -> None:
    line = "SELECT material_no FROM material -- ORDER BY material_no"
    block = "SELECT material_no FROM material /* ORDER BY material_no */"
    assert _has_top_level_order_by(line) is False
    assert _has_top_level_order_by(block) is False


def test_like_matches_text_with_a_prefix() -> None:
    checked = 0
    for exercise in _exercises():
        for sql in exercise.reference_sql:
            if _LIKE.search(_neutralize(sql)) is None:
                continue
            checked += 1
            with duckdb.connect() as connection:
                connection.execute(
                    "CREATE TABLE material (material_no VARCHAR, description VARCHAR)"
                )
                connection.execute("INSERT INTO material VALUES ('M-9', 'Spare RF generator kit')")
                rows = connection.execute(sql).fetchall()
            values = [value for row in rows for value in row]
            assert "Spare RF generator kit" in values, exercise.id
    assert checked > 0
