"""Reference queries stay portable, ordered, and pinned to a snapshot."""

import hashlib
import json
import re
from copy import deepcopy
from functools import cache
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


@cache
def _parse_sql(sql: str) -> dict:
    """Use DuckDB's parser without binding tables or executing the query."""
    with duckdb.connect(":memory:") as connection:
        parsed = json.loads(connection.execute("SELECT json_serialize_sql(?)", [sql]).fetchone()[0])
    if parsed.get("error"):
        pytest.fail(f"{parsed.get('error_message', parsed)}\n{sql}")
    assert len(parsed["statements"]) == 1, f"Expected one reference statement:\n{sql}"
    return parsed


def _root_modifiers(sql: str) -> list[dict]:
    return _parse_sql(sql)["statements"][0]["node"]["modifiers"]


def _top_level_order_clause(sql: str) -> list[dict] | None:
    for modifier in _root_modifiers(sql):
        if modifier["type"] == "ORDER_MODIFIER":
            return [order["expression"] for order in modifier["orders"]]
    return None


def _has_top_level_order_by(sql: str) -> bool:
    return _top_level_order_clause(sql) is not None


def _top_level_limit(sql: str) -> dict | None:
    for modifier in _root_modifiers(sql):
        if modifier["type"] == "LIMIT_MODIFIER":
            return modifier
    return None


def _deserialize_sql(parsed: dict) -> str:
    with duckdb.connect(":memory:") as connection:
        return connection.execute(
            "SELECT json_deserialize_sql(?)", [json.dumps(parsed)]
        ).fetchone()[0]


def _limit_probe(sql: str) -> tuple[int, str]:
    """Increase only the root LIMIT, retaining OFFSET and all nested modifiers."""
    parsed = deepcopy(_parse_sql(sql))
    for modifier in parsed["statements"][0]["node"]["modifiers"]:
        if modifier["type"] != "LIMIT_MODIFIER":
            continue
        expression = modifier["limit"]
        assert modifier["limit_type"] == "ROW_COUNT", f"Expected a row-count LIMIT:\n{sql}"
        assert expression and expression["class"] == "CONSTANT", (
            f"Expected a constant integer LIMIT:\n{sql}"
        )
        literal = expression["literal"]
        assert literal["kind"] == "INTEGER", f"Expected an integer LIMIT:\n{sql}"
        limit = int(literal["text"])
        assert limit > 0, f"Expected a positive LIMIT for the boundary tie check:\n{sql}"
        literal["text"] = str(limit + 1)
        return limit, _deserialize_sql(parsed)
    pytest.fail(f"Expected a top-level LIMIT:\n{sql}")


def _portable_sql(sql: str) -> str:
    """Round-trip the parse tree with string values blanked; comments are absent."""
    parsed = deepcopy(_parse_sql(sql))

    def blank_strings(value):
        if isinstance(value, dict):
            if value.get("kind") == "STRING":
                value["text"] = ""
            else:
                for child in value.values():
                    blank_strings(child)
        elif isinstance(value, list):
            for child in value:
                blank_strings(child)

    blank_strings(parsed)
    return _deserialize_sql(parsed)


def _order_column_indexes(sql: str, columns: list[str]) -> list[int]:
    expressions = _top_level_order_clause(sql)
    if expressions is None:
        pytest.fail(f"LIMIT query has no top-level ORDER BY:\n{sql}")
    names = {name.casefold(): index for index, name in enumerate(columns)}
    indexes: list[int] = []
    for expression in expressions:
        if expression["class"] == "CONSTANT" and expression["literal"]["kind"] == "INTEGER":
            index = int(expression["literal"]["text"]) - 1
        elif expression["class"] == "COLUMN_REF":
            index = names.get(expression["column_names"][-1].casefold())
        else:
            pytest.fail(f"ORDER BY expression is not a result column: {expression}\n{sql}")
        if index is None or not 0 <= index < len(columns):
            pytest.fail(f"ORDER BY expression is not a result column: {expression}\n{sql}")
        indexes.append(index)
    assert indexes, f"ORDER BY list is empty:\n{sql}"
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
            assert _FORBIDDEN.search(_portable_sql(sql)) is None, (
                f"{exercise.id} uses non-portable SQL"
            )


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
            if _top_level_limit(sql) is None:
                continue
            assert _has_top_level_order_by(sql), (
                f"{exercise.id} uses LIMIT without a top-level ORDER BY"
            )
            limit, probe = _limit_probe(sql)
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


def test_order_by_inside_nested_comment_does_not_count() -> None:
    assert not _has_top_level_order_by("SELECT 1 /* outer /* inner */ ORDER BY x */")


def test_top_level_order_by_with_trailing_comment_counts() -> None:
    assert _has_top_level_order_by("SELECT 1 AS x ORDER BY x -- trailing comment")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM (SELECT 1 LIMIT 1) AS nested",
        "SELECT 1 -- LIMIT 1",
        "SELECT 1 /* LIMIT 1 */",
        "SELECT 1 /* outer /* inner */ LIMIT 1 */",
    ],
)
def test_limit_outside_root_modifiers_does_not_count(sql: str) -> None:
    assert _top_level_limit(sql) is None


def test_top_level_limit_with_trailing_comment_counts() -> None:
    assert _top_level_limit("SELECT 1 LIMIT 1 -- trailing comment") is not None


@pytest.mark.parametrize(
    "sql",
    [
        "WITH nested AS (SELECT 1 AS x ORDER BY x LIMIT 1) SELECT * FROM nested",
        "SELECT 'ORDER BY x LIMIT 1' AS text",
    ],
)
def test_cte_and_string_modifiers_do_not_count(sql: str) -> None:
    assert not _has_top_level_order_by(sql)
    assert _top_level_limit(sql) is None


def test_parse_error_reports_duckdb_message() -> None:
    with pytest.raises(pytest.fail.Exception, match="syntax error"):
        _has_top_level_order_by("SELECT FROM")


def test_order_columns_come_from_root_expressions() -> None:
    sql = "SELECT x, y FROM t ORDER BY t.x DESC NULLS LAST, 2 -- trailing comment"
    assert _order_column_indexes(sql, ["x", "y"]) == [0, 1]


def test_limit_probe_keeps_nested_limit_and_offset() -> None:
    sql = (
        "SELECT x FROM (SELECT x FROM (VALUES (1), (2), (3), (4)) AS t(x) "
        "ORDER BY x LIMIT 3) AS nested ORDER BY x LIMIT 1 OFFSET 1 -- trailing comment"
    )
    limit, probe = _limit_probe(sql)
    assert limit == 1
    with duckdb.connect(":memory:") as connection:
        assert connection.execute(probe).fetchall() == [(2,), (3,)]
    assert _limit_probe(sql) == (limit, probe), "The cached parse tree must stay unchanged"


def test_portability_ignores_comments_and_strings() -> None:
    sql = "SELECT 'CURRENT_DATE QUALIFY' /* outer /* inner */ EXCLUDE */ -- date_diff"
    assert _FORBIDDEN.search(_portable_sql(sql)) is None


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT x FROM t GROUP BY ALL",
        "SELECT * EXCLUDE (x) FROM t",
        "SELECT x FROM t QUALIFY row_number() OVER () = 1",
        "SELECT CURRENT_DATE",
        "SELECT list_sum(x) FROM t",
        "SELECT strftime(x, '%Y') FROM t",
        "SELECT date_diff('day', x, y) FROM t",
    ],
)
def test_portability_round_trip_preserves_banned_constructs(sql: str) -> None:
    assert _FORBIDDEN.search(_portable_sql(sql)) is not None


def test_like_matches_text_with_a_prefix() -> None:
    exercise = next(exercise for exercise in _exercises() if exercise.id == "rf-generator-search")
    assert exercise.reference_sql
    for sql in exercise.reference_sql:
        with duckdb.connect(":memory:") as connection:
            connection.execute("CREATE TABLE material (material_no VARCHAR, description VARCHAR)")
            connection.execute("INSERT INTO material VALUES ('M-9', 'Spare RF generator kit')")
            rows = connection.execute(sql).fetchall()
        values = [value for row in rows for value in row]
        assert "Spare RF generator kit" in values, exercise.id
