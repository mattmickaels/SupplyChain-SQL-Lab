"""Reference queries stay portable, ordered, and pinned to a snapshot."""

import hashlib
import json
import re
import tomllib
from copy import deepcopy
from datetime import date, timedelta
from functools import cache
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from sclab.data.schema import create_all
from sclab.engine.checker import compare, normalize_value
from sclab.engine.query import QueryError, run_query
from sclab.engine.types import QueryResult
from sclab.lessons.model import Exercise, check_answer, load_lessons
from sclab.web.app import create_app

_CONTENT = Path(__file__).parents[1] / "src" / "sclab" / "lessons" / "content"
_SNAPSHOT = Path(__file__).parent / "snapshots" / "exercises.json"
_MAX_ROWS = 200_000
_FORBIDDEN = re.compile(
    r"GROUP\s+BY\s+ALL|\bEXCLUDE\b|\bQUALIFY\b|\bCURRENT_DATE\b|\blist_|\bstrftime\b",
    re.IGNORECASE,
)
_DAY_UNITS = frozenset({"day"})
_TRUNC_UNITS = frozenset({"month", "week"})
_DAY_INTERVALS = frozenset({"to_days"})
_OTHER_INTERVALS = frozenset(
    {
        "to_hours",
        "to_minutes",
        "to_seconds",
        "to_milliseconds",
        "to_microseconds",
        "to_weeks",
        "to_months",
        "to_quarters",
        "to_years",
        "to_decades",
        "to_centuries",
        "to_millennia",
    }
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


def _walk(value: object):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _function_name(node: dict) -> str:
    name = node.get("function_name")
    return name.casefold() if isinstance(name, str) else ""


def _function_expressions(node: dict) -> list[dict]:
    expressions: list[dict] = []
    for argument in node.get("arguments") or []:
        if isinstance(argument, dict) and isinstance(argument.get("expression"), dict):
            expressions.append(argument["expression"])
    return expressions


def _string_constant(node: object) -> str | None:
    if not isinstance(node, dict) or node.get("class") != "CONSTANT":
        return None
    literal = node.get("literal")
    if not isinstance(literal, dict) or literal.get("kind") != "STRING":
        return None
    text = literal.get("text")
    if not isinstance(text, str):
        return None
    return text.casefold()


def _is_interval_literal(node: dict) -> bool:
    if node.get("class") not in {"CAST", "CONSTANT"}:
        return False
    return (
        node.get("type_expr", {}).get("type_name", "").casefold() == "interval"
        or node.get("literal", {}).get("kind", "").casefold() == "interval"
    )


def _interval_literal_is_whole_days(node: dict) -> bool:
    constant = node.get("child") if node.get("class") == "CAST" else node
    if not isinstance(constant, dict) or constant.get("class") != "CONSTANT":
        return False
    text = constant.get("literal", {}).get("text")
    if not isinstance(text, str):
        return False
    # Keep months separate: Python timedeltas would flatten months into days.
    with duckdb.connect(":memory:") as connection:
        try:
            return connection.execute(
                "SELECT datepart('year', i) = 0 AND datepart('month', i) = 0 "
                "AND (datepart('hour', i) * 3600000000 "
                "+ datepart('minute', i) * 60000000 "
                "+ datepart('microseconds', i)) % 86400000000 = 0 "
                "FROM (SELECT CAST(? AS INTERVAL) AS i)",
                [text],
            ).fetchone()[0]
        except duckdb.Error:
            return False


def _date_exception_forms(sql: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split controlled date forms into allowed units and rejected units."""
    allowed: list[str] = []
    rejected: list[str] = []
    for node in _walk(_parse_sql(sql)):
        if _is_interval_literal(node):
            if _interval_literal_is_whole_days(node):
                allowed.append("interval")
            else:
                rejected.append("interval")
            continue
        if node.get("class") != "FUNCTION":
            continue
        name = _function_name(node)
        expressions = _function_expressions(node)
        unit = _string_constant(expressions[0]) if expressions else None
        if name in {"datediff", "date_diff"}:
            if unit in _DAY_UNITS:
                allowed.append(name)
            else:
                rejected.append(name)
        elif name == "date_trunc":
            if unit in _TRUNC_UNITS:
                allowed.append(name)
            else:
                rejected.append(name)
        elif name in _DAY_INTERVALS:
            allowed.append("interval")
        elif name in _OTHER_INTERVALS:
            rejected.append(name)
    return tuple(allowed), tuple(rejected)


def _has_databricks_note(note: str | None) -> bool:
    return isinstance(note, str) and bool(note.strip())


def _date_exception_problem(sql: str, note: str | None) -> str | None:
    allowed, rejected = _date_exception_forms(sql)
    if rejected:
        return f"unsupported date form: {rejected[0]}"
    if allowed and not _has_databricks_note(note):
        return f"{allowed[0]} needs a databricks_note"
    return None


def _integer_constant(node: object) -> int | None:
    if not isinstance(node, dict) or node.get("class") != "CONSTANT":
        return None
    literal = node.get("literal")
    if not isinstance(literal, dict) or literal.get("kind") != "INTEGER":
        return None
    text = literal.get("text")
    if not isinstance(text, str):
        return None
    return int(text)


def _is_level_guard(node: dict) -> bool:
    if node.get("class") != "COMPARISON" or node.get("type") != "COMPARE_LESSTHAN":
        return False
    left = node.get("left")
    if not isinstance(left, dict) or left.get("class") != "COLUMN_REF":
        return False
    names = left.get("column_names")
    if not isinstance(names, list) or not names or str(names[-1]).casefold() != "level":
        return False
    limit = _integer_constant(node.get("right"))
    return limit is not None and limit <= 10


def _where_has_level_guard(where: object) -> bool:
    if not isinstance(where, dict):
        return False
    if _is_level_guard(where):
        return True
    if where.get("class") != "CONJUNCTION":
        return False
    children = where.get("children", [])
    if where.get("type") == "CONJUNCTION_AND":
        return any(_where_has_level_guard(child) for child in children)
    if where.get("type") == "CONJUNCTION_OR":
        return bool(children) and all(_where_has_level_guard(child) for child in children)
    return False


def _recursive_guard_problem(sql: str) -> str | None:
    for node in _walk(_parse_sql(sql)):
        if node.get("type") != "RECURSIVE_CTE_NODE":
            continue
        if _is_bounded_week_sequence(node):
            continue
        right = node.get("right")
        where = right.get("where_clause") if isinstance(right, dict) else None
        if not _where_has_level_guard(where):
            return "recursive term needs WHERE level < an integer of at most 10"
    return None


def _is_bounded_week_sequence(node: dict) -> bool:
    """Allow only the coordinator's zero-based, increment-by-one calendar, up to 26 weeks."""
    left, right = node.get("left", {}), node.get("right", {})
    anchor = left.get("select_list", [])
    step = right.get("select_list", [])
    source = right.get("from_table", {})
    if not (
        node.get("cte_name") == "weeks"
        and node.get("aliases") == ["level"]
        and node.get("union_all") is True
        and left.get("from_table", {}).get("type") == "EMPTY"
        and len(anchor) == 1
        and _integer_constant(anchor[0]) == 0
        and len(step) == 1
        and _function_name(step[0]) == "+"
        and source.get("type") == "BASE_TABLE"
        and source.get("table_name") == "weeks"
    ):
        return False
    arguments = _function_expressions(step[0])
    where = right.get("where_clause") or {}
    return (
        len(arguments) == 2
        and arguments[0].get("class") == "COLUMN_REF"
        and arguments[0].get("column_names") == ["level"]
        and _integer_constant(arguments[1]) == 1
        and where.get("type") == "COMPARE_LESSTHAN"
        and where.get("left", {}).get("class") == "COLUMN_REF"
        and where.get("left", {}).get("column_names") == ["level"]
        and _integer_constant(where.get("right")) in range(26)
    )


def test_reference_sql_is_portable() -> None:
    for exercise in _exercises():
        for sql in exercise.reference_sql:
            assert _FORBIDDEN.search(_portable_sql(sql)) is None, (
                f"{exercise.id} uses non-portable SQL"
            )
            problem = _date_exception_problem(sql, exercise.databricks_note)
            assert problem is None, f"{exercise.id}: {problem}"


def test_exception_forms_need_databricks_note() -> None:
    for exercise in _exercises():
        for sql in exercise.reference_sql:
            allowed, rejected = _date_exception_forms(sql)
            assert not rejected, f"{exercise.id} uses an unsupported date form"
            if allowed:
                assert _has_databricks_note(exercise.databricks_note), (
                    f"{exercise.id} uses {allowed[0]} and needs a databricks_note"
                )


def test_recursive_queries_have_depth_guard() -> None:
    for exercise in _exercises():
        for sql in exercise.reference_sql:
            problem = _recursive_guard_problem(sql)
            assert problem is None, f"{exercise.id}: {problem}"


_GUARDED_RECURSIVE = (
    "WITH RECURSIVE bom AS ("
    "SELECT 0 AS level "
    "UNION ALL "
    "SELECT level + 1 FROM bom WHERE level < 10"
    ") SELECT level FROM bom"
)
_OUTER_ONLY_RECURSIVE = (
    "WITH RECURSIVE t(level) AS ("
    "SELECT 1 UNION ALL SELECT level + 1 FROM t"
    ") SELECT * FROM t WHERE level < 10"
)
_UNGUARDED_RECURSIVE = (
    "WITH RECURSIVE t(level) AS (SELECT 1 UNION ALL SELECT level + 1 FROM t) SELECT * FROM t"
)


def test_recursive_guard_in_recursive_term() -> None:
    assert _recursive_guard_problem(_GUARDED_RECURSIVE) is None


@pytest.mark.parametrize(
    ("predicate", "guarded"),
    [
        ("level < 10 AND x > 0", True),
        ("level < 10 OR true", False),
        ("NOT (level >= 10)", False),
        ("NOT (level < 10)", False),
        ("(level < 10 AND x > 0) OR (level < 5 AND x < 0)", True),
        ("(level < 10 AND x > 0) OR x < 0", False),
        ("(level < 10 OR true) AND x > 0", False),
        ("(x > 0 OR x < 0) AND level < 10", True),
    ],
)
def test_recursive_guard_boolean_structure(predicate: str, guarded: bool) -> None:
    sql = _GUARDED_RECURSIVE.replace("level < 10", predicate)
    assert (_recursive_guard_problem(sql) is None) is guarded


def test_recursive_outer_filter_is_not_a_guard() -> None:
    assert _recursive_guard_problem(_OUTER_ONLY_RECURSIVE) is not None


def test_recursive_query_without_filter_fails() -> None:
    assert _recursive_guard_problem(_UNGUARDED_RECURSIVE) is not None


def test_recursive_guard_constant_is_at_most_10() -> None:
    too_deep = _GUARDED_RECURSIVE.replace("level < 10", "level < 11")
    not_less_than = _GUARDED_RECURSIVE.replace("level < 10", "level <= 10")
    assert _recursive_guard_problem(too_deep) is not None
    assert _recursive_guard_problem(not_less_than) is not None


_ALLOWED_DATE_SQL = (
    "SELECT datediff('day', a, b) FROM t",
    "SELECT date_diff('day', a, b) FROM t",
    "SELECT date_trunc('month', d) FROM t",
    "SELECT date_trunc('week', d) FROM t",
    "SELECT d + INTERVAL 7 DAY FROM t",
    "SELECT d + INTERVAL '1 day' FROM t",
    "SELECT CAST('7 days' AS INTERVAL)",
)


@pytest.mark.parametrize("sql", _ALLOWED_DATE_SQL)
def test_allowed_date_forms_pass_with_note(sql: str) -> None:
    assert _date_exception_problem(sql, "date_add or datediff equivalent.") is None


@pytest.mark.parametrize("sql", _ALLOWED_DATE_SQL)
def test_allowed_date_forms_fail_without_note(sql: str) -> None:
    assert _date_exception_problem(sql, None) is not None
    assert _date_exception_problem(sql, "  ") is not None


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT datediff('month', a, b) FROM t",
        "SELECT date_trunc('year', d) FROM t",
        "SELECT d + INTERVAL 2 YEAR FROM t",
        "SELECT d + INTERVAL '2 years' FROM t",
        "SELECT INTERVAL '36 hours'",
        "SELECT INTERVAL '1 day 1 microsecond'",
        "SELECT CAST('1 month 2 days' AS INTERVAL)",
    ],
)
def test_disallowed_date_units_fail_with_note(sql: str) -> None:
    assert _date_exception_problem(sql, "A note does not allow this unit.") is not None


def test_non_ascii_day_interval_needs_note() -> None:
    sql = "SELECT 'é', DATE '2026-01-01' + INTERVAL 1 DAY"
    assert _date_exception_problem(sql, None) is not None
    assert _date_exception_problem(sql, "date_add(DATE '2026-01-01', 1).") is None


def test_interval_column_is_not_a_date_exception() -> None:
    assert _date_exception_problem("SELECT interval_col FROM t", None) is None


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
    ],
)
def test_portability_round_trip_preserves_banned_constructs(sql: str) -> None:
    assert _FORBIDDEN.search(_portable_sql(sql)) is not None


def _lesson_folders() -> list[Path]:
    folders = [
        folder for folder in _CONTENT.iterdir() if folder.is_dir() and folder.name != "00-sample"
    ]
    return sorted(folders, key=lambda folder: folder.name)


def _cell(value: object) -> object:
    if value == "NULL":
        return None
    return value


def _normalize_ws(text: str) -> str:
    return " ".join(text.split())


def test_worked_examples_match_text(lab_home) -> None:
    assert lab_home is not None
    for folder in _lesson_folders():
        path = folder / "examples.toml"
        assert path.is_file(), folder.name
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        examples = data.get("example", [])
        assert examples, folder.name
        for example in examples:
            result = _run(example["sql"])
            expected = QueryResult(
                columns=list(result.columns),
                rows=[tuple(_cell(value) for value in row) for row in example["expected_rows"]],
                total_rows=len(example["expected_rows"]),
                truncated=False,
                elapsed_ms=0,
            )
            outcome = compare(
                result,
                expected,
                order_matters=example["order_matters"],
                names_matter=False,
                columns_in_order=True,
            )
            assert outcome.passed, f"{folder.name} {example['id']}: {outcome.summary}"
        for fact in data.get("fact", []):
            result = _run(fact["sql"])
            assert len(result.rows) == 1, fact["id"]
            assert len(result.rows[0]) == 1, fact["id"]
            assert normalize_value(result.rows[0][0]) == normalize_value(fact["expected"]), fact[
                "id"
            ]


@pytest.mark.parametrize("last_order", [None, "2026-06-23", "2026-06-24"])
def test_tool_recency_fact_includes_every_customer(last_order) -> None:
    data = tomllib.loads((_CONTENT / "05-dates-and-time/examples.toml").read_text())
    fact = next(item for item in data["fact"] if item["id"] == "customers-without-july-tool-order")
    with duckdb.connect(":memory:") as connection:
        connection.execute("CREATE TABLE customer (customer_no VARCHAR)")
        connection.execute(
            "CREATE TABLE sales_order (so_no VARCHAR, customer_no VARCHAR, order_date DATE)"
        )
        connection.execute("CREATE TABLE sales_order_item (so_no VARCHAR, material_no VARCHAR)")
        connection.execute("CREATE TABLE material (material_no VARCHAR, material_type VARCHAR)")
        connection.execute("INSERT INTO customer VALUES ('recent'), ('other')")
        connection.execute("INSERT INTO material VALUES ('tool', 'TOOL'), ('spare', 'SPARE')")
        connection.execute(
            "INSERT INTO sales_order VALUES ('one', 'recent', DATE '2026-06-24'), "
            "('two', 'other', DATE '2026-10-31')"
        )
        connection.execute("INSERT INTO sales_order_item VALUES ('one', 'tool'), ('two', 'spare')")
        if last_order is not None:
            connection.execute("INSERT INTO sales_order VALUES ('three', 'other', ?)", [last_order])
            connection.execute("INSERT INTO sales_order_item VALUES ('three', 'tool')")
        actual = connection.execute(fact["sql"]).fetchone()[0]
    matches = normalize_value(actual) == normalize_value(fact["expected"])
    assert matches == (last_order == "2026-06-24")


def test_hints_are_not_reference_queries() -> None:
    for exercise in _exercises():
        for hint in exercise.hints:
            folded = _normalize_ws(hint)
            for sql in exercise.reference_sql:
                reference = _normalize_ws(sql)
                assert folded != reference, exercise.id
                assert reference not in folded, exercise.id


_WRONG_ANSWERS = (
    (
        "join-open-rf-po-lines",
        """
        SELECT i.po_no, i.line_no, s.name AS supplier_name, i.confirmed_delivery_date
        FROM purchase_order_item AS i
        JOIN purchase_order AS h ON h.po_no = i.po_no
        JOIN supplier AS s ON s.supplier_no = h.supplier_no
        JOIN material AS m ON m.material_no = i.material_no
        LEFT JOIN (
            SELECT po_no, line_no, sum(qty) AS received_qty
            FROM goods_receipt
            GROUP BY po_no, line_no
        ) AS r ON r.po_no = i.po_no AND r.line_no = i.line_no
        WHERE m.description LIKE 'RF generator,%'
          AND h.order_date <= DATE '2026-11-01'
        ORDER BY i.confirmed_delivery_date, i.po_no, i.line_no
        """,
    ),
    (
        "join-open-rf-po-lines",
        """
        SELECT i.po_no, i.line_no, s.name AS supplier_name, i.confirmed_delivery_date
        FROM purchase_order_item AS i
        JOIN purchase_order AS h ON h.po_no = i.po_no
        JOIN supplier AS s ON s.supplier_no = h.supplier_no
        JOIN material AS m ON m.material_no = i.material_no
        JOIN goods_receipt AS g ON g.po_no = i.po_no AND g.line_no = i.line_no
        WHERE m.description LIKE 'RF generator,%'
          AND h.order_date <= DATE '2026-11-01'
          AND g.qty < i.order_qty
        ORDER BY i.confirmed_delivery_date, i.po_no, i.line_no
        """,
    ),
    (
        "join-corvane-late-lines",
        """
        SELECT i.po_no, i.line_no, m.description,
               i.requested_delivery_date, i.confirmed_delivery_date
        FROM purchase_order_item AS i
        JOIN purchase_order AS h ON h.po_no = i.po_no
        JOIN material AS m ON m.material_no = i.material_no
        LEFT JOIN (
            SELECT po_no, line_no, sum(qty) AS received_qty
            FROM goods_receipt
            GROUP BY po_no, line_no
        ) AS r ON r.po_no = i.po_no AND r.line_no = i.line_no
        WHERE h.supplier_no = 'V-10017'
          AND h.order_date <= DATE '2026-11-01'
          AND i.confirmed_delivery_date >= i.requested_delivery_date + INTERVAL 56 DAY
        ORDER BY i.po_no, i.line_no
        """,
    ),
    (
        "join-corvane-late-lines",
        """
        SELECT i.po_no, i.line_no, m.description,
               i.requested_delivery_date, i.confirmed_delivery_date
        FROM purchase_order_item AS i
        JOIN purchase_order AS h ON h.po_no = i.po_no
        JOIN material AS m ON m.material_no = i.material_no
        JOIN goods_receipt AS g ON g.po_no = i.po_no AND g.line_no = i.line_no
        WHERE h.supplier_no = 'V-10017'
          AND h.order_date <= DATE '2026-11-01'
          AND g.qty < i.order_qty
          AND i.confirmed_delivery_date >= i.requested_delivery_date + INTERVAL 56 DAY
        ORDER BY i.po_no, i.line_no
        """,
    ),
    (
        "join-long-lead-without-open-po",
        """
        SELECT m.material_no, m.description
        FROM material AS m
        LEFT JOIN purchase_order_item AS i ON i.material_no = m.material_no
        WHERE m.is_long_lead = true
          AND i.po_no IS NULL
          AND m.description NOT LIKE '% '
        ORDER BY m.material_no
        """,
    ),
    (
        "join-long-lead-without-open-po",
        """
        SELECT m.material_no, m.description
        FROM material AS m
        LEFT JOIN purchase_order_item AS i
          ON i.material_no = m.material_no
         AND i.confirmed_delivery_date >= DATE '2026-11-01'
        WHERE m.is_long_lead = true
          AND i.po_no = NULL
          AND m.description NOT LIKE '% '
        ORDER BY m.material_no
        """,
    ),
    (
        "join-long-lead-without-open-po",
        """
        SELECT m.material_no, m.description
        FROM material AS m
        LEFT JOIN purchase_order_item AS i ON i.material_no = m.material_no
        WHERE m.is_long_lead = true
          AND i.confirmed_delivery_date >= DATE '2026-11-01'
          AND i.po_no IS NULL
          AND m.description NOT LIKE '% '
        ORDER BY m.material_no
        """,
    ),
    (
        "join-single-source-long-lead",
        """
        SELECT m.material_no, m.description, s.supplier_no
        FROM material AS m
        JOIN material_supplier AS s ON s.material_no = m.material_no
        WHERE m.description LIKE 'RF generator,%'
        ORDER BY m.material_no
        """,
    ),
    (
        "confirmed-after-today",
        """
        SELECT po_no, line_no, material_no, confirmed_delivery_date
        FROM purchase_order_item
        WHERE confirmed_delivery_date >= DATE '2026-11-01'
          AND order_qty >= 10
        ORDER BY confirmed_delivery_date, po_no, line_no
        """,
    ),
    (
        "rf-generator-search",
        """
        SELECT material_no, description
        FROM material
        WHERE description = 'RF generator'
        ORDER BY material_no
        """,
    ),
    (
        "rf-generator-search",
        """
        SELECT material_no, description
        FROM material
        WHERE description LIKE 'RF generator%'
        ORDER BY material_no
        """,
    ),
    (
        "join-multi-receipt-qty",
        """
        SELECT i.po_no, i.line_no, SUM(i.order_qty) AS order_qty, SUM(g.qty) AS received_qty
        FROM purchase_order_item AS i
        JOIN goods_receipt AS g ON g.po_no = i.po_no AND g.line_no = i.line_no
        WHERE i.po_no IN (
            'PO-0000003', 'PO-0000006', 'PO-0000009', 'PO-0000012', 'PO-0000015'
        )
        GROUP BY i.po_no, i.line_no
        ORDER BY i.po_no, i.line_no
        """,
    ),
    (
        "dates-october-late-receipts",
        """
        SELECT g.po_no, g.line_no, g.receipt_date, i.confirmed_delivery_date,
               datediff('day', i.confirmed_delivery_date, g.receipt_date) AS days_late
        FROM goods_receipt AS g
        JOIN purchase_order_item AS i ON i.po_no = g.po_no AND i.line_no = g.line_no
        WHERE g.receipt_date >= DATE '2026-10-01'
          AND g.receipt_date <= DATE '2026-10-31'
        ORDER BY days_late DESC, g.po_no, g.line_no
        """,
    ),
    (
        "dates-late-rate-by-year",
        """
        SELECT EXTRACT(YEAR FROM g.receipt_date) AS receipt_year,
               COUNT(*) AS receipt_count,
               SUM(
                   CASE WHEN g.receipt_date >= i.confirmed_delivery_date THEN 1 ELSE 0 END
               ) AS late_count,
               ROUND(
                   100.0 * SUM(
                       CASE WHEN g.receipt_date >= i.confirmed_delivery_date THEN 1 ELSE 0 END
                   )
                   / COUNT(*),
                   1
               ) AS late_pct
        FROM goods_receipt AS g
        JOIN purchase_order_item AS i ON i.po_no = g.po_no AND i.line_no = g.line_no
        WHERE g.receipt_date >= DATE '2022-01-01'
          AND g.receipt_date < DATE '2027-01-01'
        GROUP BY EXTRACT(YEAR FROM g.receipt_date)
        ORDER BY receipt_year
        """,
    ),
    (
        "dates-actual-vs-quoted-lead-time",
        """
        SELECT line.supplier_no,
               ROUND(AVG(line.actual_days), 1) AS avg_actual_lead_time_days,
               ROUND(AVG(q.quoted_lead_time_days), 1) AS avg_quoted_lead_time_days,
               r.receipt_count
        FROM (
            SELECT i.po_no,
                   i.line_no,
                   i.material_no,
                   h.supplier_no,
                   MIN(g.receipt_date) AS first_receipt_date,
                   datediff('day', h.order_date, MAX(g.receipt_date)) AS actual_days
            FROM purchase_order_item AS i
            JOIN purchase_order AS h ON h.po_no = i.po_no
            JOIN goods_receipt AS g ON g.po_no = i.po_no AND g.line_no = i.line_no
            GROUP BY i.po_no, i.line_no, i.material_no, h.supplier_no, h.order_date
        ) AS line
        JOIN (
            SELECT h.supplier_no, COUNT(*) AS receipt_count
            FROM goods_receipt AS g
            JOIN purchase_order AS h ON h.po_no = g.po_no
            WHERE g.receipt_date >= DATE '2025-11-01'
              AND g.receipt_date < DATE '2026-11-01'
            GROUP BY h.supplier_no
            HAVING COUNT(*) >= 20
        ) AS r ON r.supplier_no = line.supplier_no
        LEFT JOIN material_supplier AS q
          ON q.material_no = line.material_no
         AND q.supplier_no = line.supplier_no
        WHERE line.first_receipt_date >= DATE '2025-11-01'
          AND line.first_receipt_date < DATE '2026-11-01'
        GROUP BY line.supplier_no, r.receipt_count
        ORDER BY line.supplier_no
        """,
    ),
    (
        "dates-actual-vs-quoted-lead-time",
        """
        SELECT line.supplier_no,
               ROUND(AVG(line.actual_days), 1) AS avg_actual_lead_time_days,
               ROUND(AVG(q.quoted_lead_time_days), 1) AS avg_quoted_lead_time_days,
               r.receipt_count
        FROM (
            SELECT i.po_no,
                   i.line_no,
                   i.material_no,
                   h.supplier_no,
                   MIN(g.receipt_date) AS first_receipt_date,
                   datediff('day', h.order_date, MIN(g.receipt_date)) AS actual_days
            FROM purchase_order_item AS i
            JOIN purchase_order AS h ON h.po_no = i.po_no
            JOIN goods_receipt AS g ON g.po_no = i.po_no AND g.line_no = i.line_no
            GROUP BY i.po_no, i.line_no, i.material_no, h.supplier_no, h.order_date
        ) AS line
        JOIN (
            SELECT h.supplier_no, COUNT(*) AS receipt_count
            FROM goods_receipt AS g
            JOIN purchase_order AS h ON h.po_no = g.po_no
            WHERE g.receipt_date >= DATE '2025-11-01'
              AND g.receipt_date < DATE '2026-11-01'
            GROUP BY h.supplier_no
        ) AS r ON r.supplier_no = line.supplier_no
        LEFT JOIN material_supplier AS q
          ON q.material_no = line.material_no
         AND q.supplier_no = line.supplier_no
        WHERE line.first_receipt_date >= DATE '2025-11-01'
          AND line.first_receipt_date < DATE '2026-11-01'
        GROUP BY line.supplier_no, r.receipt_count
        ORDER BY line.supplier_no
        """,
    ),
    (
        "dates-lines-booked-by-month",
        """
        SELECT date_trunc('month', i.requested_date) AS booked_month,
               COUNT(*) AS line_count
        FROM sales_order_item AS i
        WHERE i.requested_date >= DATE '2025-01-01'
          AND i.requested_date < DATE '2026-01-01'
        GROUP BY date_trunc('month', i.requested_date)
        ORDER BY booked_month
        """,
    ),
    (
        "dates-open-po-lines-by-week",
        """
        SELECT date_trunc('week', i.confirmed_delivery_date) AS week_start,
               COUNT(*) AS open_line_count
        FROM purchase_order_item AS i
        LEFT JOIN (
            SELECT po_no, line_no, SUM(qty) AS received_qty
            FROM goods_receipt
            GROUP BY po_no, line_no
        ) AS r ON r.po_no = i.po_no AND r.line_no = i.line_no
        WHERE i.confirmed_delivery_date >= DATE '2026-11-01'
          AND i.confirmed_delivery_date < DATE '2026-11-01' + INTERVAL 56 DAY
          AND COALESCE(r.received_qty, 0) = 0
        GROUP BY date_trunc('week', i.confirmed_delivery_date)
        ORDER BY week_start
        """,
    ),
    (
        "dates-open-po-lines-by-week",
        """
        SELECT date_trunc('week', i.confirmed_delivery_date) AS week_start,
               COUNT(*) AS open_line_count
        FROM purchase_order_item AS i
        LEFT JOIN (
            SELECT po_no, line_no, SUM(qty) AS received_qty
            FROM goods_receipt
            GROUP BY po_no, line_no
        ) AS r ON r.po_no = i.po_no AND r.line_no = i.line_no
        WHERE i.confirmed_delivery_date >= DATE '2026-11-01'
          AND i.confirmed_delivery_date < DATE '2026-11-01' + INTERVAL 8 DAY
          AND COALESCE(r.received_qty, 0) < i.order_qty
        GROUP BY date_trunc('week', i.confirmed_delivery_date)
        ORDER BY week_start
        """,
    ),
    (
        "dates-quiet-tool-customers",
        """
        SELECT c.customer_no,
               c.name,
               MAX(h.order_date) AS last_tool_order_date
        FROM customer AS c
        JOIN sales_order AS h ON h.customer_no = c.customer_no
        JOIN sales_order_item AS i ON i.so_no = h.so_no
        JOIN material AS m ON m.material_no = i.material_no
        WHERE m.material_type = 'TOOL'
        GROUP BY c.customer_no, c.name
        HAVING MAX(h.order_date) + INTERVAL 180 DAY < DATE '2026-11-01'
        ORDER BY last_tool_order_date, c.customer_no
        """,
    ),
    (
        "dates-quiet-tool-customers",
        """
        SELECT c.customer_no,
               c.name,
               MAX(h.order_date) AS last_tool_order_date
        FROM customer AS c
        JOIN sales_order AS h ON h.customer_no = c.customer_no
        GROUP BY c.customer_no, c.name
        HAVING MAX(h.order_date) + INTERVAL 90 DAY < DATE '2026-11-01'
        ORDER BY last_tool_order_date, c.customer_no
        """,
    ),
    (
        "dates-corvane-on-time-by-quarter",
        """
        SELECT EXTRACT(YEAR FROM g.receipt_date) AS receipt_year,
               EXTRACT(QUARTER FROM g.receipt_date) AS receipt_quarter,
               COUNT(*) AS receipt_count,
               ROUND(
                   100.0 * SUM(
                       CASE WHEN g.receipt_date <= i.confirmed_delivery_date THEN 1 ELSE 0 END
                   )
                   / COUNT(*),
                   1
               ) AS on_time_pct
        FROM goods_receipt AS g
        JOIN purchase_order_item AS i ON i.po_no = g.po_no AND i.line_no = g.line_no
        JOIN purchase_order AS h ON h.po_no = g.po_no
        WHERE g.receipt_date >= DATE '2025-01-01'
          AND g.receipt_date < DATE '2027-01-01'
        GROUP BY EXTRACT(YEAR FROM g.receipt_date), EXTRACT(QUARTER FROM g.receipt_date)
        ORDER BY receipt_year, receipt_quarter
        """,
    ),
    (
        "clean-duplicate-materials",
        """
        SELECT material_no, description, TRIM(description) AS trimmed_description
        FROM material
        WHERE description LIKE '% '
        ORDER BY trimmed_description, material_no
        """,
    ),
    (
        "clean-lead-time-counts",
        """
        SELECT plant_code,
               SUM(CASE WHEN planned_lead_time_days IS NULL THEN 1 ELSE 0 END) AS null_count,
               SUM(CASE WHEN planned_lead_time_days = 0 THEN 1 ELSE 0 END) AS zero_count,
               SUM(CASE WHEN planned_lead_time_days >= 0 THEN 1 ELSE 0 END) AS valid_count
        FROM material_plant
        GROUP BY plant_code
        ORDER BY plant_code
        """,
    ),
    (
        "clean-lead-time-counts",
        """
        SELECT plant_code,
               SUM(CASE WHEN planned_lead_time_days IS NULL THEN 1 ELSE 0 END) AS null_count,
               SUM(
                   CASE WHEN COALESCE(planned_lead_time_days, 0) = 0 THEN 1 ELSE 0 END
               ) AS zero_count,
               SUM(CASE WHEN planned_lead_time_days > 0 THEN 1 ELSE 0 END) AS valid_count
        FROM material_plant
        GROUP BY plant_code
        ORDER BY plant_code
        """,
    ),
    (
        "clean-lead-time-averages",
        """
        SELECT ROUND(AVG(planned_lead_time_days), 1) AS avg_ignoring_null,
               ROUND(AVG(planned_lead_time_days), 1) AS avg_null_as_zero,
               ROUND(AVG(CASE WHEN planned_lead_time_days > 0
                              THEN planned_lead_time_days END), 1) AS avg_excluding_null_and_zero
        FROM material_plant
        """,
    ),
    (
        "clean-lead-time-averages",
        """
        SELECT ROUND(AVG(planned_lead_time_days), 1) AS avg_ignoring_null,
               ROUND(AVG(COALESCE(planned_lead_time_days, 0)), 1) AS avg_null_as_zero,
               ROUND(AVG(planned_lead_time_days), 1) AS avg_excluding_null_and_zero
        FROM material_plant
        """,
    ),
    (
        "clean-uom",
        """
        SELECT uom, COUNT(*) AS line_count
        FROM purchase_order_item
        GROUP BY uom
        ORDER BY uom
        """,
    ),
    (
        "clean-country-trim",
        """
        SELECT country AS country_raw,
               country,
               COUNT(*) AS raw_count,
               COUNT(*) AS trimmed_count
        FROM customer
        GROUP BY country
        ORDER BY country, country_raw
        """,
    ),
    (
        "clean-fill-lead-time",
        """
        SELECT p.material_no,
               p.plant_code,
               p.planned_lead_time_days,
               s.quoted_lead_time_days,
               COALESCE(p.planned_lead_time_days, 0) AS filled_lead_time_days
        FROM material_plant AS p
        LEFT JOIN material_supplier AS s
          ON s.material_no = p.material_no
         AND s.is_primary = true
        WHERE p.planned_lead_time_days IS NULL
        ORDER BY p.material_no, p.plant_code
        """,
    ),
    (
        "clean-removed-material-rows",
        """
        SELECT COUNT(*) - COUNT(DISTINCT TRIM(description)) AS removed_rows
        FROM material
        """,
    ),
    (
        "clean-removed-material-rows",
        """
        SELECT COUNT(*) - COUNT(DISTINCT description) AS removed_rows
        FROM material
        """,
    ),
    (
        "plan-open-demand",
        """
        SELECT m.family_code, CAST(date_trunc('month', i.promised_date) AS DATE) AS month,
               SUM(i.qty) AS open_qty
        FROM sales_order_item i JOIN material m ON m.material_no = i.material_no
        WHERE m.material_type = 'TOOL'
          AND i.promised_date >= DATE '2026-11-01' AND i.promised_date < DATE '2027-05-01'
        GROUP BY m.family_code, CAST(date_trunc('month', i.promised_date) AS DATE)
        ORDER BY m.family_code, month
        """,
    ),
    (
        "plan-open-demand",
        """
        SELECT m.family_code, CAST(date_trunc('month', i.promised_date) AS DATE) AS month,
               SUM(i.qty) AS open_qty
        FROM sales_order_item i JOIN material m ON m.material_no = i.material_no
        WHERE i.status = 'Open' AND m.material_type = 'TOOL'
          AND i.promised_date >= DATE '2026-11-01' AND i.promised_date < DATE '2027-04-01'
        GROUP BY m.family_code, CAST(date_trunc('month', i.promised_date) AS DATE)
        ORDER BY m.family_code, month
        """,
    ),
    (
        "plan-forecast-waterfall",
        """
        WITH totals AS (
            SELECT m.family_code, f.forecast_month,
                   SUM(CASE WHEN f.forecast_version = '2026-09' THEN f.qty ELSE 0 END) AS qty_09,
                   SUM(CASE WHEN f.forecast_version = '2026-10' THEN f.qty ELSE 0 END) AS qty_10
            FROM demand_forecast f JOIN material m ON m.material_no = f.material_no
            WHERE m.material_type = 'TOOL' AND f.forecast_version IN ('2026-09', '2026-10')
              AND f.forecast_month >= DATE '2026-11-01' AND f.forecast_month < DATE '2027-05-01'
            GROUP BY m.family_code, f.forecast_month
        )
        SELECT family_code, forecast_month, qty_09, qty_10, qty_09 - qty_10 AS change_qty
        FROM totals ORDER BY family_code, forecast_month
        """,
    ),
    (
        "plan-forecast-waterfall",
        """
        WITH totals AS (
            SELECT m.family_code, f.forecast_month,
                   SUM(CASE WHEN f.forecast_version = '2026-09' THEN f.qty ELSE 0 END) AS qty_09,
                   SUM(CASE WHEN f.forecast_version = '2026-09' THEN f.qty ELSE 0 END) AS qty_10
            FROM demand_forecast f JOIN material m ON m.material_no = f.material_no
            WHERE m.material_type = 'TOOL' AND f.forecast_version IN ('2026-09', '2026-10')
              AND f.forecast_month >= DATE '2026-11-01' AND f.forecast_month < DATE '2027-05-01'
            GROUP BY m.family_code, f.forecast_month
        )
        SELECT family_code, forecast_month, qty_09, qty_10, qty_10 - qty_09 AS change_qty
        FROM totals ORDER BY family_code, forecast_month
        """,
    ),
    (
        "plan-forecast-coverage",
        """
        WITH booked AS (
            SELECT m.family_code, CAST(date_trunc('month', i.promised_date) AS DATE) AS month,
                   SUM(i.qty) AS qty
            FROM sales_order_item i JOIN material m ON m.material_no = i.material_no
            WHERE m.material_type = 'TOOL' AND i.status = 'Open'
              AND i.promised_date >= DATE '2026-11-01' AND i.promised_date < DATE '2027-02-01'
            GROUP BY m.family_code, CAST(date_trunc('month', i.promised_date) AS DATE)
        ), forecast AS (
            SELECT m.family_code, f.forecast_month AS month, SUM(f.qty) AS qty
            FROM demand_forecast f JOIN material m ON m.material_no = f.material_no
            WHERE m.material_type = 'TOOL' AND f.forecast_version IN ('2026-09', '2026-10')
              AND f.forecast_month >= DATE '2026-11-01' AND f.forecast_month < DATE '2027-02-01'
            GROUP BY m.family_code, f.forecast_month
        )
        SELECT COALESCE(b.family_code, f.family_code) AS family_code,
               COALESCE(b.month, f.month) AS month, COALESCE(b.qty, 0) AS booked_qty,
               COALESCE(f.qty, 0) AS forecast_qty,
               ROUND(100.0 * COALESCE(b.qty, 0) / NULLIF(f.qty, 0), 2) AS coverage_pct
        FROM booked b FULL OUTER JOIN forecast f
          ON f.family_code = b.family_code AND f.month = b.month
        ORDER BY family_code, month
        """,
    ),
    (
        "plan-forecast-coverage",
        """
        WITH booked AS (
            SELECT m.family_code, CAST(date_trunc('month', i.promised_date) AS DATE) AS month,
                   SUM(i.qty) AS qty
            FROM sales_order_item i JOIN material m ON m.material_no = i.material_no
            WHERE m.material_type = 'TOOL' AND i.status = 'Open'
              AND i.promised_date >= DATE '2026-11-01' AND i.promised_date < DATE '2027-02-01'
            GROUP BY m.family_code, CAST(date_trunc('month', i.promised_date) AS DATE)
        ), forecast AS (
            SELECT m.family_code, f.forecast_month AS month, SUM(f.qty) AS qty
            FROM demand_forecast f JOIN material m ON m.material_no = f.material_no
            WHERE m.material_type = 'TOOL' AND f.forecast_version = '2026-10'
              AND f.forecast_month >= DATE '2026-11-01' AND f.forecast_month < DATE '2027-02-01'
            GROUP BY m.family_code, f.forecast_month
        )
        SELECT COALESCE(b.family_code, f.family_code) AS family_code,
               COALESCE(b.month, f.month) AS month, COALESCE(b.qty, 0) AS booked_qty,
               COALESCE(f.qty, 0) AS forecast_qty,
               ROUND(1.0 * COALESCE(b.qty, 0) / NULLIF(f.qty, 0), 2) AS coverage_pct
        FROM booked b FULL OUTER JOIN forecast f
          ON f.family_code = b.family_code AND f.month = b.month
        ORDER BY family_code, month
        """,
    ),
    (
        "plan-weekly-balance",
        """
        WITH RECURSIVE tree AS (
            SELECT m.material_no AS root, m.family_code, b.component_material_no AS material_no,
                   CAST(b.qty_per AS DECIMAL(20,6)) AS qty, 1 AS level
            FROM material m JOIN bom_item b ON b.parent_material_no = m.material_no
            WHERE m.material_type = 'TOOL'
            UNION ALL
            SELECT t.root, t.family_code, b.component_material_no,
                   CAST(t.qty * b.qty_per AS DECIMAL(20,6)), t.level + 1
            FROM tree t JOIN bom_item b ON b.parent_material_no = t.material_no
            WHERE t.level < 10
        ), leaves AS (
            SELECT t.root, t.family_code, t.material_no, SUM(t.qty) AS qty
            FROM tree t JOIN material m ON m.material_no = t.material_no
            WHERE m.material_type = 'COMPONENT'
            GROUP BY t.root, t.family_code, t.material_no
        ), outstanding AS (
            SELECT i.po_no, i.line_no, i.material_no, h.plant_code, h.supplier_no,
                   i.confirmed_delivery_date, i.order_qty - COALESCE(SUM(g.qty), 0) AS qty
            FROM purchase_order_item i JOIN purchase_order h ON h.po_no = i.po_no
            LEFT JOIN goods_receipt g ON g.po_no = i.po_no AND g.line_no = i.line_no
            WHERE h.order_date <= DATE '2026-11-01'
            GROUP BY i.po_no, i.line_no, i.material_no, h.plant_code, h.supplier_no,
                     i.confirmed_delivery_date, i.order_qty
            HAVING i.order_qty > COALESCE(SUM(g.qty), 0)
        ), stock AS (
            SELECT material_no, plant_code, SUM(qty_on_hand) AS qty FROM inventory
            WHERE storage_location = 'RM01'
            GROUP BY material_no, plant_code
        ), periods AS (
            SELECT CAST(DATE '2026-11-01' + n * INTERVAL 7 DAY AS DATE) AS week_start
            FROM (VALUES (0), (1), (2), (3), (4), (5), (6), (7), (8), (9), (10), (11), (12),
                         (13), (14), (15), (16), (17), (18), (19),
                         (20), (21), (22), (23), (24), (25)) AS offsets(n)
        ), targets AS (
            SELECT mp.material_no, mp.plant_code, mp.safety_stock
            FROM material_plant mp JOIN material m ON m.material_no = mp.material_no
            WHERE (m.is_long_lead = true OR m.description LIKE 'RF generator,%')
              AND EXISTS (
                  SELECT 1 FROM production_order p JOIN leaves l ON l.root = p.material_no
                  WHERE l.material_no = mp.material_no AND p.plant_code = mp.plant_code
                    AND p.status IN ('Planned', 'Released')
                    AND p.planned_start > DATE '2026-11-01' AND p.planned_start < DATE '2027-05-02'
              )
        ), demand AS (
            SELECT l.material_no, p.plant_code, p.planned_start AS day, SUM(p.qty * l.qty) AS qty
            FROM production_order p JOIN leaves l ON l.root = p.material_no
            WHERE p.status IN ('Planned', 'Released')
              AND p.planned_start > DATE '2026-11-01' AND p.planned_start < DATE '2027-05-02'
            GROUP BY l.material_no, p.plant_code, p.planned_start
        ), weekly AS (
            SELECT t.material_no, t.plant_code, t.safety_stock, w.week_start,
                   COALESCE((SELECT SUM(o.qty) FROM outstanding o
                             WHERE o.material_no = t.material_no AND o.plant_code = t.plant_code
                               AND o.confirmed_delivery_date IS NOT NULL
                               AND GREATEST(o.confirmed_delivery_date, DATE '2026-11-01')
                            >= w.week_start
                               AND GREATEST(o.confirmed_delivery_date, DATE '2026-11-01')
                                   < w.week_start + INTERVAL 7 DAY), 0) AS receipts,
                   COALESCE((SELECT SUM(d.qty) FROM demand d
                             WHERE d.material_no = t.material_no AND d.plant_code = t.plant_code
                               AND d.day >= w.week_start
                            AND d.day < w.week_start + INTERVAL 7 DAY), 0)
                       AS demand_qty,
                   COALESCE(s.qty, 0) AS opening_qty
            FROM targets t CROSS JOIN periods w
            LEFT JOIN stock s ON s.material_no = t.material_no AND s.plant_code = t.plant_code
        ), balances AS (
            SELECT material_no, plant_code, safety_stock, week_start, receipts, demand_qty,
                   0 + SUM(receipts - demand_qty) OVER (
                       PARTITION BY material_no, plant_code ORDER BY week_start
                       ROWS UNBOUNDED PRECEDING) AS projected_balance
            FROM weekly
        )
        SELECT week_start, receipts, demand_qty, projected_balance FROM balances
        WHERE material_no = 'M-001324' AND plant_code = 'SC01'
        ORDER BY week_start
        """,
    ),
    (
        "plan-weekly-balance",
        """
        WITH RECURSIVE tree AS (
            SELECT m.material_no AS root, m.family_code, b.component_material_no AS material_no,
                   CAST(b.qty_per AS DECIMAL(20,6)) AS qty, 1 AS level
            FROM material m JOIN bom_item b ON b.parent_material_no = m.material_no
            WHERE m.material_type = 'TOOL'
            UNION ALL
            SELECT t.root, t.family_code, b.component_material_no,
                   CAST(t.qty * b.qty_per AS DECIMAL(20,6)), t.level + 1
            FROM tree t JOIN bom_item b ON b.parent_material_no = t.material_no
            WHERE t.level < 10
        ), leaves AS (
            SELECT t.root, t.family_code, t.material_no, SUM(t.qty) AS qty
            FROM tree t JOIN material m ON m.material_no = t.material_no
            WHERE m.material_type = 'COMPONENT'
            GROUP BY t.root, t.family_code, t.material_no
        ), outstanding AS (
            SELECT i.po_no, i.line_no, i.material_no, h.plant_code, h.supplier_no,
                   i.confirmed_delivery_date, i.order_qty - SUM(g.qty) AS qty
            FROM purchase_order_item i JOIN purchase_order h ON h.po_no = i.po_no
            LEFT JOIN goods_receipt g ON g.po_no = i.po_no AND g.line_no = i.line_no
            WHERE h.order_date <= DATE '2026-11-01'
            GROUP BY i.po_no, i.line_no, i.material_no, h.plant_code, h.supplier_no,
                     i.confirmed_delivery_date, i.order_qty
            HAVING i.order_qty > COALESCE(SUM(g.qty), 0)
        ), stock AS (
            SELECT material_no, plant_code, SUM(qty_on_hand) AS qty FROM inventory
            WHERE storage_location = 'RM01'
            GROUP BY material_no, plant_code
        ), periods AS (
            SELECT CAST(DATE '2026-11-01' + n * INTERVAL 7 DAY AS DATE) AS week_start
            FROM (VALUES (0), (1), (2), (3), (4), (5), (6), (7), (8), (9), (10), (11), (12),
                         (13), (14), (15), (16), (17), (18), (19),
                         (20), (21), (22), (23), (24), (25)) AS offsets(n)
        ), targets AS (
            SELECT mp.material_no, mp.plant_code, mp.safety_stock
            FROM material_plant mp JOIN material m ON m.material_no = mp.material_no
            WHERE (m.is_long_lead = true OR m.description LIKE 'RF generator,%')
              AND EXISTS (
                  SELECT 1 FROM production_order p JOIN leaves l ON l.root = p.material_no
                  WHERE l.material_no = mp.material_no AND p.plant_code = mp.plant_code
                    AND p.status IN ('Planned', 'Released')
                    AND p.planned_start > DATE '2026-11-01' AND p.planned_start < DATE '2027-05-02'
              )
        ), demand AS (
            SELECT l.material_no, p.plant_code, p.planned_start AS day, SUM(p.qty * l.qty) AS qty
            FROM production_order p JOIN leaves l ON l.root = p.material_no
            WHERE p.status IN ('Planned', 'Released')
              AND p.planned_start > DATE '2026-11-01' AND p.planned_start < DATE '2027-05-02'
            GROUP BY l.material_no, p.plant_code, p.planned_start
        ), weekly AS (
            SELECT t.material_no, t.plant_code, t.safety_stock, w.week_start,
                   COALESCE((SELECT SUM(o.qty) FROM outstanding o
                             WHERE o.material_no = t.material_no AND o.plant_code = t.plant_code
                               AND o.confirmed_delivery_date IS NOT NULL
                               AND GREATEST(o.confirmed_delivery_date, DATE '2026-11-01')
                            >= w.week_start
                               AND GREATEST(o.confirmed_delivery_date, DATE '2026-11-01')
                                   < w.week_start + INTERVAL 7 DAY), 0) AS receipts,
                   COALESCE((SELECT SUM(d.qty) FROM demand d
                             WHERE d.material_no = t.material_no AND d.plant_code = t.plant_code
                               AND d.day >= w.week_start
                            AND d.day < w.week_start + INTERVAL 7 DAY), 0)
                       AS demand_qty,
                   COALESCE(s.qty, 0) AS opening_qty
            FROM targets t CROSS JOIN periods w
            LEFT JOIN stock s ON s.material_no = t.material_no AND s.plant_code = t.plant_code
        ), balances AS (
            SELECT material_no, plant_code, safety_stock, week_start, receipts, demand_qty,
                   opening_qty + SUM(receipts - demand_qty) OVER (
                       PARTITION BY material_no, plant_code ORDER BY week_start
                       ROWS UNBOUNDED PRECEDING) AS projected_balance
            FROM weekly
        )
        SELECT week_start, receipts, demand_qty, projected_balance FROM balances
        WHERE material_no = 'M-001324' AND plant_code = 'SC01'
        ORDER BY week_start
        """,
    ),
    (
        "plan-shortages",
        """
        WITH RECURSIVE tree AS (
            SELECT m.material_no AS root, m.family_code, b.component_material_no AS material_no,
                   CAST(b.qty_per AS DECIMAL(20,6)) AS qty, 1 AS level
            FROM material m JOIN bom_item b ON b.parent_material_no = m.material_no
            WHERE m.material_type = 'TOOL'
            UNION ALL
            SELECT t.root, t.family_code, b.component_material_no,
                   CAST(t.qty * b.qty_per AS DECIMAL(20,6)), t.level + 1
            FROM tree t JOIN bom_item b ON b.parent_material_no = t.material_no
            WHERE t.level < 10
        ), leaves AS (
            SELECT t.root, t.family_code, t.material_no, SUM(t.qty) AS qty
            FROM tree t JOIN material m ON m.material_no = t.material_no
            WHERE m.material_type = 'COMPONENT'
            GROUP BY t.root, t.family_code, t.material_no
        ), outstanding AS (
            SELECT i.po_no, i.line_no, i.material_no, h.plant_code, h.supplier_no,
                   i.confirmed_delivery_date, i.order_qty - COALESCE(SUM(g.qty), 0) AS qty
            FROM purchase_order_item i JOIN purchase_order h ON h.po_no = i.po_no
            LEFT JOIN goods_receipt g ON g.po_no = i.po_no AND g.line_no = i.line_no
            WHERE h.order_date <= DATE '2026-11-01'
            GROUP BY i.po_no, i.line_no, i.material_no, h.plant_code, h.supplier_no,
                     i.confirmed_delivery_date, i.order_qty
            HAVING i.order_qty > COALESCE(SUM(g.qty), 0)
        ), stock AS (
            SELECT material_no, plant_code, SUM(qty_on_hand) AS qty FROM inventory
            WHERE storage_location = 'RM01'
            GROUP BY material_no, plant_code
        ), periods AS (
            SELECT CAST(DATE '2026-11-01' + n * INTERVAL 7 DAY AS DATE) AS week_start
            FROM (VALUES (0), (1), (2), (3), (4), (5), (6), (7), (8), (9), (10), (11), (12),
                         (13), (14), (15), (16), (17), (18), (19),
                         (20), (21), (22), (23), (24), (25)) AS offsets(n)
        ), targets AS (
            SELECT mp.material_no, mp.plant_code, mp.safety_stock
            FROM material_plant mp JOIN material m ON m.material_no = mp.material_no
            WHERE (m.is_long_lead = true OR m.description LIKE 'RF generator,%')
              AND EXISTS (
                  SELECT 1 FROM production_order p JOIN leaves l ON l.root = p.material_no
                  WHERE l.material_no = mp.material_no AND p.plant_code = mp.plant_code
                    AND p.status IN ('Planned', 'Released')
                    AND p.planned_start > DATE '2026-11-01' AND p.planned_start < DATE '2027-05-02'
              )
        ), demand AS (
            SELECT l.material_no, p.plant_code, p.planned_start AS day, SUM(p.qty * l.qty) AS qty
            FROM production_order p JOIN leaves l ON l.root = p.material_no
            WHERE p.status IN ('Planned', 'Released')
              AND p.planned_start > DATE '2026-11-01' AND p.planned_start < DATE '2027-05-02'
            GROUP BY l.material_no, p.plant_code, p.planned_start
        ), weekly AS (
            SELECT t.material_no, t.plant_code, t.safety_stock, w.week_start,
                   COALESCE((SELECT SUM(o.qty) FROM outstanding o
                             WHERE o.material_no = t.material_no AND o.plant_code = t.plant_code
                               AND o.confirmed_delivery_date IS NOT NULL
                               AND GREATEST(o.confirmed_delivery_date, DATE '2026-11-01')
                            >= w.week_start
                               AND GREATEST(o.confirmed_delivery_date, DATE '2026-11-01')
                                   < w.week_start + INTERVAL 7 DAY), 0) AS receipts,
                   COALESCE((SELECT SUM(d.qty) FROM demand d
                             WHERE d.material_no = t.material_no AND d.plant_code = t.plant_code
                               AND d.day >= w.week_start
                            AND d.day < w.week_start + INTERVAL 7 DAY), 0)
                       AS demand_qty,
                   COALESCE(s.qty, 0) AS opening_qty
            FROM targets t CROSS JOIN periods w
            LEFT JOIN stock s ON s.material_no = t.material_no AND s.plant_code = t.plant_code
        ), balances AS (
            SELECT material_no, plant_code, safety_stock, week_start, receipts, demand_qty,
                   opening_qty + SUM(receipts - demand_qty) OVER (
                       PARTITION BY material_no, plant_code ORDER BY week_start
                       ROWS UNBOUNDED PRECEDING) AS projected_balance
            FROM weekly
        )
        SELECT material_no, plant_code, MIN(week_start) AS first_shortage_week
        FROM balances WHERE projected_balance < 0
        GROUP BY material_no, plant_code ORDER BY material_no, plant_code
        """,
    ),
    (
        "plan-shortages",
        """
        WITH RECURSIVE tree AS (
            SELECT m.material_no AS root, m.family_code, b.component_material_no AS material_no,
                   CAST(b.qty_per AS DECIMAL(20,6)) AS qty, 1 AS level
            FROM material m JOIN bom_item b ON b.parent_material_no = m.material_no
            WHERE m.material_type = 'TOOL'
            UNION ALL
            SELECT t.root, t.family_code, b.component_material_no,
                   CAST(t.qty * b.qty_per AS DECIMAL(20,6)), t.level + 1
            FROM tree t JOIN bom_item b ON b.parent_material_no = t.material_no
            WHERE t.level < 10
        ), leaves AS (
            SELECT t.root, t.family_code, t.material_no, SUM(t.qty) AS qty
            FROM tree t JOIN material m ON m.material_no = t.material_no
            WHERE m.material_type = 'COMPONENT'
            GROUP BY t.root, t.family_code, t.material_no
        ), outstanding AS (
            SELECT i.po_no, i.line_no, i.material_no, h.plant_code, h.supplier_no,
                   i.confirmed_delivery_date, i.order_qty - COALESCE(SUM(g.qty), 0) AS qty
            FROM purchase_order_item i JOIN purchase_order h ON h.po_no = i.po_no
            LEFT JOIN goods_receipt g ON g.po_no = i.po_no AND g.line_no = i.line_no
            WHERE h.order_date <= DATE '2026-11-01'
            GROUP BY i.po_no, i.line_no, i.material_no, h.plant_code, h.supplier_no,
                     i.confirmed_delivery_date, i.order_qty
            HAVING i.order_qty > COALESCE(SUM(g.qty), 0)
        ), stock AS (
            SELECT material_no, plant_code, SUM(qty_on_hand) AS qty FROM inventory
            WHERE storage_location = 'RM01'
            GROUP BY material_no, plant_code
        ), periods AS (
            SELECT CAST(DATE '2026-11-01' + n * INTERVAL 7 DAY AS DATE) AS week_start
            FROM (VALUES (0), (1), (2), (3), (4), (5), (6), (7), (8), (9), (10), (11), (12),
                         (13), (14), (15), (16), (17), (18), (19),
                         (20), (21), (22), (23), (24), (25)) AS offsets(n)
        ), targets AS (
            SELECT mp.material_no, mp.plant_code, mp.safety_stock
            FROM material_plant mp JOIN material m ON m.material_no = mp.material_no
            WHERE (m.is_long_lead = true OR m.description LIKE 'RF generator,%')
              AND EXISTS (
                  SELECT 1 FROM production_order p JOIN leaves l ON l.root = p.material_no
                  WHERE l.material_no = mp.material_no AND p.plant_code = mp.plant_code
                    AND p.status IN ('Planned', 'Released')
                    AND p.planned_start > DATE '2026-11-01' AND p.planned_start < DATE '2027-05-02'
              )
        ), demand AS (
            SELECT l.material_no, p.plant_code, p.planned_start AS day, SUM(p.qty * l.qty) AS qty
            FROM production_order p JOIN leaves l ON l.root = p.material_no
            WHERE p.status IN ('Planned', 'Released')
              AND p.planned_start > DATE '2026-11-01' AND p.planned_start < DATE '2027-05-02'
            GROUP BY l.material_no, p.plant_code, p.planned_start
        ), weekly AS (
            SELECT t.material_no, t.plant_code, t.safety_stock, w.week_start,
                   COALESCE((SELECT SUM(o.qty) FROM outstanding o
                             WHERE o.material_no = t.material_no AND o.plant_code = t.plant_code
                               AND o.confirmed_delivery_date IS NOT NULL
                               AND GREATEST(o.confirmed_delivery_date, DATE '2026-11-01')
                            >= w.week_start
                               AND GREATEST(o.confirmed_delivery_date, DATE '2026-11-01')
                                   < w.week_start + INTERVAL 7 DAY), 0) AS receipts,
                   COALESCE((SELECT SUM(d.qty) FROM demand d
                             WHERE d.material_no = t.material_no AND d.plant_code = t.plant_code
                               AND d.day >= w.week_start
                            AND d.day < w.week_start + INTERVAL 7 DAY), 0)
                       AS demand_qty,
                   COALESCE(s.qty, 0) AS opening_qty
            FROM targets t CROSS JOIN periods w
            LEFT JOIN stock s ON s.material_no = t.material_no AND s.plant_code = t.plant_code
        ), balances AS (
            SELECT material_no, plant_code, safety_stock, week_start, receipts, demand_qty,
                   opening_qty + SUM(receipts - demand_qty) OVER (
                       PARTITION BY material_no, plant_code ORDER BY week_start
                       ROWS UNBOUNDED PRECEDING) AS projected_balance
            FROM weekly
        )
        SELECT material_no, plant_code, MIN(week_start) AS first_shortage_week
        FROM balances WHERE projected_balance <= safety_stock
        GROUP BY material_no, plant_code ORDER BY material_no, plant_code
        """,
    ),
    (
        "plan-at-risk-orders",
        """
        WITH RECURSIVE tree AS (
            SELECT m.material_no AS root, m.family_code, b.component_material_no AS material_no,
                   CAST(b.qty_per AS DECIMAL(20,6)) AS qty, 1 AS level
            FROM material m JOIN bom_item b ON b.parent_material_no = m.material_no
            WHERE m.material_type = 'TOOL'
            UNION ALL
            SELECT t.root, t.family_code, b.component_material_no,
                   CAST(t.qty * b.qty_per AS DECIMAL(20,6)), t.level + 1
            FROM tree t JOIN bom_item b ON b.parent_material_no = t.material_no
            WHERE t.level < 10
        ), leaves AS (
            SELECT t.root, t.family_code, t.material_no, SUM(t.qty) AS qty
            FROM tree t JOIN material m ON m.material_no = t.material_no
            WHERE m.material_type = 'COMPONENT'
            GROUP BY t.root, t.family_code, t.material_no
        ), outstanding AS (
            SELECT i.po_no, i.line_no, i.material_no, h.plant_code, h.supplier_no,
                   i.confirmed_delivery_date, i.order_qty - COALESCE(SUM(g.qty), 0) AS qty
            FROM purchase_order_item i JOIN purchase_order h ON h.po_no = i.po_no
            LEFT JOIN goods_receipt g ON g.po_no = i.po_no AND g.line_no = i.line_no
            WHERE h.order_date <= DATE '2026-11-01'
            GROUP BY i.po_no, i.line_no, i.material_no, h.plant_code, h.supplier_no,
                     i.confirmed_delivery_date, i.order_qty
            HAVING i.order_qty > COALESCE(SUM(g.qty), 0)
        ), stock AS (
            SELECT material_no, plant_code, SUM(qty_on_hand) AS qty FROM inventory
            WHERE storage_location = 'RM01'
            GROUP BY material_no, plant_code
        ), spare_plant AS (
            SELECT material_no, MIN(CASE family_code WHEN 'ETC' THEN 'SC01'
                                   WHEN 'CMP' THEN 'SG01' ELSE 'AU01' END) AS plant_code
            FROM leaves GROUP BY material_no
        ), requirements AS (
            SELECT p.prod_order_no, l.material_no, p.plant_code, p.planned_start AS need_date,
                   p.qty * l.qty AS qty
            FROM production_order p JOIN leaves l ON l.root = p.material_no
            JOIN material m ON m.material_no = l.material_no
            WHERE p.planned_start > DATE '2026-11-01' AND m.description LIKE 'RF generator,%'
            UNION ALL
            SELECT NULL, i.material_no, sp.plant_code, i.promised_date, i.qty
            FROM sales_order_item i JOIN spare_plant sp ON sp.material_no = i.material_no
            JOIN material m ON m.material_no = i.material_no
            WHERE i.status = 'Open' AND i.promised_date > DATE '2026-11-01'
              AND m.description LIKE 'RF generator,%'
        ), daily_demand AS (
            SELECT material_no, plant_code, need_date, SUM(qty) AS qty FROM requirements
            GROUP BY material_no, plant_code, need_date
        ), cumulative_demand AS (
            SELECT material_no, plant_code, need_date,
                   SUM(qty) OVER (PARTITION BY material_no, plant_code ORDER BY need_date
                                  ROWS UNBOUNDED PRECEDING) AS required_qty
            FROM daily_demand
        ), parameters AS (
            SELECT 28 AS slip_days
        ), scenarios AS (
            SELECT 0 AS slip_days UNION ALL SELECT slip_days FROM parameters
        ), supply_events AS (
            SELECT s.material_no, s.plant_code, sc.slip_days, DATE '2026-11-01' AS day, s.qty
            FROM stock s CROSS JOIN scenarios sc
            UNION ALL
            SELECT o.material_no, o.plant_code, sc.slip_days,
                   CAST(GREATEST(o.confirmed_delivery_date
                        + CASE WHEN o.supplier_no = 'V-10017' THEN sc.slip_days ELSE 0 END
                          * INTERVAL 1 DAY, DATE '2026-11-01') AS DATE), o.qty
            FROM outstanding o CROSS JOIN scenarios sc
            WHERE o.confirmed_delivery_date IS NOT NULL
        ), daily_supply AS (
            SELECT material_no, plant_code, slip_days, day, SUM(qty) AS qty FROM supply_events
            GROUP BY material_no, plant_code, slip_days, day
        ), supply_balances AS (
            SELECT material_no, plant_code, slip_days, day,
                   SUM(qty) OVER (PARTITION BY material_no, plant_code, slip_days ORDER BY day
                                  ROWS UNBOUNDED PRECEDING) AS available_qty
            FROM daily_supply
        ), availability AS (
            SELECT r.prod_order_no, r.need_date, sc.slip_days,
                   (SELECT MIN(s.day) FROM supply_balances s
                    WHERE s.material_no = r.material_no AND s.plant_code = r.plant_code
                      AND s.slip_days = sc.slip_days
                      AND s.available_qty >= d.required_qty) AS available_date
            FROM requirements r JOIN cumulative_demand d
              ON d.material_no = r.material_no AND d.plant_code = r.plant_code
                  AND d.need_date = r.need_date
            CROSS JOIN scenarios sc
            WHERE r.prod_order_no IS NOT NULL
        ), line_risk AS (
            SELECT i.so_no, i.line_no, c.name AS customer_name, i.promised_date,
                   p.planned_start, a.slip_days,
                   MAX(datediff('day', p.planned_start, a.available_date)) AS days_at_risk
            FROM availability a JOIN production_order p ON p.prod_order_no = a.prod_order_no
            JOIN sales_order_item i ON i.so_no = p.so_no AND i.line_no = p.so_line_no
            JOIN sales_order h ON h.so_no = i.so_no
            JOIN customer c ON c.customer_no = h.customer_no
            JOIN material m ON m.material_no = p.material_no
            WHERE i.status = 'Open' AND m.material_type = 'TOOL' AND m.family_code = 'ETC'
              AND h.order_date <= DATE '2026-11-01'
            GROUP BY i.so_no, i.line_no, c.name, i.promised_date, p.planned_start, a.slip_days
        )
        SELECT so_no, line_no, customer_name, promised_date, planned_start, days_at_risk
        FROM line_risk WHERE slip_days = 28 AND days_at_risk > 0
        ORDER BY so_no, line_no
        """,
    ),
    (
        "plan-at-risk-orders",
        """
        WITH RECURSIVE tree AS (
            SELECT m.material_no AS root, m.family_code, b.component_material_no AS material_no,
                   CAST(b.qty_per AS DECIMAL(20,6)) AS qty, 1 AS level
            FROM material m JOIN bom_item b ON b.parent_material_no = m.material_no
            WHERE m.material_type = 'TOOL'
            UNION ALL
            SELECT t.root, t.family_code, b.component_material_no,
                   CAST(t.qty * b.qty_per AS DECIMAL(20,6)), t.level + 1
            FROM tree t JOIN bom_item b ON b.parent_material_no = t.material_no
            WHERE t.level < 10
        ), leaves AS (
            SELECT t.root, t.family_code, t.material_no, SUM(t.qty) AS qty
            FROM tree t JOIN material m ON m.material_no = t.material_no
            WHERE m.material_type = 'COMPONENT'
            GROUP BY t.root, t.family_code, t.material_no
        ), outstanding AS (
            SELECT i.po_no, i.line_no, i.material_no, h.plant_code, h.supplier_no,
                   i.confirmed_delivery_date, i.order_qty - COALESCE(SUM(g.qty), 0) AS qty
            FROM purchase_order_item i JOIN purchase_order h ON h.po_no = i.po_no
            LEFT JOIN goods_receipt g ON g.po_no = i.po_no AND g.line_no = i.line_no
            WHERE h.order_date <= DATE '2026-11-01'
            GROUP BY i.po_no, i.line_no, i.material_no, h.plant_code, h.supplier_no,
                     i.confirmed_delivery_date, i.order_qty
            HAVING i.order_qty > COALESCE(SUM(g.qty), 0)
        ), stock AS (
            SELECT material_no, plant_code, SUM(qty_on_hand) AS qty FROM inventory
            WHERE storage_location = 'FG01'
            GROUP BY material_no, plant_code
        ), spare_plant AS (
            SELECT material_no, MIN(CASE family_code WHEN 'ETC' THEN 'SC01'
                                   WHEN 'CMP' THEN 'SG01' ELSE 'AU01' END) AS plant_code
            FROM leaves GROUP BY material_no
        ), requirements AS (
            SELECT p.prod_order_no, l.material_no, p.plant_code, p.planned_start AS need_date,
                   p.qty * l.qty AS qty
            FROM production_order p JOIN leaves l ON l.root = p.material_no
            JOIN material m ON m.material_no = l.material_no
            WHERE p.planned_start > DATE '2026-11-01' AND m.description LIKE 'RF generator,%'
            UNION ALL
            SELECT NULL, i.material_no, sp.plant_code, i.promised_date, i.qty
            FROM sales_order_item i JOIN spare_plant sp ON sp.material_no = i.material_no
            JOIN material m ON m.material_no = i.material_no
            WHERE i.status = 'Open' AND i.promised_date > DATE '2026-11-01'
              AND m.description LIKE 'RF generator,%'
        ), daily_demand AS (
            SELECT material_no, plant_code, need_date, SUM(qty) AS qty FROM requirements
            GROUP BY material_no, plant_code, need_date
        ), cumulative_demand AS (
            SELECT material_no, plant_code, need_date,
                   SUM(qty) OVER (PARTITION BY material_no, plant_code ORDER BY need_date
                                  ROWS UNBOUNDED PRECEDING) AS required_qty
            FROM daily_demand
        ), parameters AS (
            SELECT 28 AS slip_days
        ), scenarios AS (
            SELECT 0 AS slip_days UNION ALL SELECT slip_days FROM parameters
        ), supply_events AS (
            SELECT s.material_no, s.plant_code, sc.slip_days, DATE '2026-11-01' AS day, s.qty
            FROM stock s CROSS JOIN scenarios sc
            UNION ALL
            SELECT o.material_no, o.plant_code, sc.slip_days,
                   CAST(GREATEST(o.confirmed_delivery_date
                        + CASE WHEN o.supplier_no = 'V-10017' THEN sc.slip_days ELSE 0 END
                          * INTERVAL 1 DAY, DATE '2026-11-01') AS DATE), o.qty
            FROM outstanding o CROSS JOIN scenarios sc
            WHERE o.confirmed_delivery_date IS NOT NULL
        ), daily_supply AS (
            SELECT material_no, plant_code, slip_days, day, SUM(qty) AS qty FROM supply_events
            GROUP BY material_no, plant_code, slip_days, day
        ), supply_balances AS (
            SELECT material_no, plant_code, slip_days, day,
                   SUM(qty) OVER (PARTITION BY material_no, plant_code, slip_days ORDER BY day
                                  ROWS UNBOUNDED PRECEDING) AS available_qty
            FROM daily_supply
        ), availability AS (
            SELECT r.prod_order_no, r.need_date, sc.slip_days,
                   (SELECT MIN(s.day) FROM supply_balances s
                    WHERE s.material_no = r.material_no AND s.plant_code = r.plant_code
                      AND s.slip_days = sc.slip_days
                      AND s.available_qty >= d.required_qty) AS available_date
            FROM requirements r JOIN cumulative_demand d
              ON d.material_no = r.material_no AND d.plant_code = r.plant_code
                  AND d.need_date = r.need_date
            CROSS JOIN scenarios sc
            WHERE r.prod_order_no IS NOT NULL
        ), line_risk AS (
            SELECT i.so_no, i.line_no, c.name AS customer_name, i.promised_date,
                   p.planned_start, a.slip_days,
                   MAX(datediff('day', p.planned_start, a.available_date)) AS days_at_risk
            FROM availability a JOIN production_order p ON p.prod_order_no = a.prod_order_no
            JOIN sales_order_item i ON i.so_no = p.so_no AND i.line_no = p.so_line_no
            JOIN sales_order h ON h.so_no = i.so_no
            JOIN customer c ON c.customer_no = h.customer_no
            JOIN material m ON m.material_no = p.material_no
            WHERE i.status = 'Open' AND m.material_type = 'TOOL' AND m.family_code = 'ETC'
              AND h.order_date <= DATE '2026-11-01'
            GROUP BY i.so_no, i.line_no, c.name, i.promised_date, p.planned_start, a.slip_days
        )
        SELECT so_no, line_no, customer_name, promised_date, planned_start, days_at_risk
        FROM line_risk WHERE slip_days = 0 AND days_at_risk > 0
        ORDER BY so_no, line_no
        """,
    ),
    (
        "plan-bay-load",
        """
        WITH weeks AS (
            SELECT DISTINCT CAST(date_trunc('week', planned_start) AS DATE) AS week_start
            FROM production_order
            WHERE planned_start >= DATE '2026-12-01' AND planned_start < DATE '2027-03-01'
        ), loads AS (
            SELECT bay_id, CAST(date_trunc('week', planned_start) AS DATE) AS week_start,
                   COUNT(*) AS starts
            FROM production_order
            WHERE planned_start >= DATE '2026-12-01' AND planned_start < DATE '2027-03-01'
            GROUP BY bay_id, CAST(date_trunc('week', planned_start) AS DATE)
        ), grid AS (
            SELECT b.bay_id, w.week_start, COALESCE(l.starts, 0) AS starts, b.weekly_slots
            FROM build_bay b CROSS JOIN weeks w
            LEFT JOIN loads l ON l.bay_id = b.bay_id AND l.week_start = w.week_start
        )
        SELECT bay_id, week_start, starts, weekly_slots,
               ROUND(100.0 * starts / weekly_slots, 2) AS load_pct,
               starts >= weekly_slots AS overloaded
        FROM grid ORDER BY bay_id, week_start
        """,
    ),
    (
        "plan-bay-load",
        """
        WITH weeks AS (
            SELECT DISTINCT CAST(date_trunc('week', planned_start) AS DATE) AS week_start
            FROM production_order
            WHERE planned_start >= DATE '2026-12-01' AND planned_start < DATE '2027-03-01'
        ), loads AS (
            SELECT bay_id, CAST(date_trunc('week', planned_start) AS DATE) AS week_start,
                   COUNT(*) AS starts
            FROM production_order
            WHERE planned_start >= DATE '2026-12-01' AND planned_start < DATE '2027-03-01'
            GROUP BY bay_id, CAST(date_trunc('week', planned_start) AS DATE)
        ), grid AS (
            SELECT b.bay_id, w.week_start, COALESCE(l.starts, 0) AS starts, b.weekly_slots
            FROM build_bay b CROSS JOIN weeks w
            LEFT JOIN loads l ON l.bay_id = b.bay_id AND l.week_start = w.week_start
        )
        SELECT bay_id, week_start, starts, weekly_slots,
               ROUND(100.0 * starts / weekly_slots, 2) AS load_pct,
               starts > weekly_slots AS overloaded
        FROM grid WHERE starts > 0 ORDER BY bay_id, week_start
        """,
    ),
    (
        "plan-corvane-what-if",
        """
        WITH RECURSIVE tree AS (
            SELECT m.material_no AS root, m.family_code, b.component_material_no AS material_no,
                   CAST(b.qty_per AS DECIMAL(20,6)) AS qty, 1 AS level
            FROM material m JOIN bom_item b ON b.parent_material_no = m.material_no
            WHERE m.material_type = 'TOOL'
            UNION ALL
            SELECT t.root, t.family_code, b.component_material_no,
                   CAST(t.qty * b.qty_per AS DECIMAL(20,6)), t.level + 1
            FROM tree t JOIN bom_item b ON b.parent_material_no = t.material_no
            WHERE t.level < 10
        ), leaves AS (
            SELECT t.root, t.family_code, t.material_no, SUM(t.qty) AS qty
            FROM tree t JOIN material m ON m.material_no = t.material_no
            WHERE m.material_type = 'COMPONENT'
            GROUP BY t.root, t.family_code, t.material_no
        ), outstanding AS (
            SELECT i.po_no, i.line_no, i.material_no, h.plant_code, h.supplier_no,
                   i.confirmed_delivery_date, i.order_qty - COALESCE(SUM(g.qty), 0) AS qty
            FROM purchase_order_item i JOIN purchase_order h ON h.po_no = i.po_no
            LEFT JOIN goods_receipt g ON g.po_no = i.po_no AND g.line_no = i.line_no
            WHERE h.order_date <= DATE '2026-11-01'
            GROUP BY i.po_no, i.line_no, i.material_no, h.plant_code, h.supplier_no,
                     i.confirmed_delivery_date, i.order_qty
            HAVING i.order_qty > COALESCE(SUM(g.qty), 0)
        ), stock AS (
            SELECT material_no, plant_code, SUM(qty_on_hand) AS qty FROM inventory
            WHERE storage_location = 'RM01'
            GROUP BY material_no, plant_code
        ), spare_plant AS (
            SELECT material_no, MIN(CASE family_code WHEN 'ETC' THEN 'SC01'
                                   WHEN 'CMP' THEN 'SG01' ELSE 'AU01' END) AS plant_code
            FROM leaves GROUP BY material_no
        ), requirements AS (
            SELECT p.prod_order_no, l.material_no, p.plant_code, p.planned_start AS need_date,
                   p.qty * l.qty AS qty
            FROM production_order p JOIN leaves l ON l.root = p.material_no
            JOIN material m ON m.material_no = l.material_no
            WHERE p.planned_start > DATE '2026-11-01' AND m.description LIKE 'RF generator,%'
            UNION ALL
            SELECT NULL, i.material_no, sp.plant_code, i.promised_date, i.qty
            FROM sales_order_item i JOIN spare_plant sp ON sp.material_no = i.material_no
            JOIN material m ON m.material_no = i.material_no
            WHERE i.status = 'Open' AND i.promised_date > DATE '2026-11-01'
              AND m.description LIKE 'RF generator,%'
        ), daily_demand AS (
            SELECT material_no, plant_code, need_date, SUM(qty) AS qty FROM requirements
            GROUP BY material_no, plant_code, need_date
        ), cumulative_demand AS (
            SELECT material_no, plant_code, need_date,
                   SUM(qty) OVER (PARTITION BY material_no, plant_code ORDER BY need_date
                                  ROWS UNBOUNDED PRECEDING) AS required_qty
            FROM daily_demand
        ), parameters AS (
            SELECT 0 AS slip_days
        ), scenarios AS (
            SELECT 0 AS slip_days UNION ALL SELECT slip_days FROM parameters
        ), supply_events AS (
            SELECT s.material_no, s.plant_code, sc.slip_days, DATE '2026-11-01' AS day, s.qty
            FROM stock s CROSS JOIN scenarios sc
            UNION ALL
            SELECT o.material_no, o.plant_code, sc.slip_days,
                   CAST(GREATEST(o.confirmed_delivery_date
                        + CASE WHEN o.supplier_no = 'V-10017' THEN sc.slip_days ELSE 0 END
                          * INTERVAL 1 DAY, DATE '2026-11-01') AS DATE), o.qty
            FROM outstanding o CROSS JOIN scenarios sc
            WHERE o.confirmed_delivery_date IS NOT NULL
        ), daily_supply AS (
            SELECT material_no, plant_code, slip_days, day, SUM(qty) AS qty FROM supply_events
            GROUP BY material_no, plant_code, slip_days, day
        ), supply_balances AS (
            SELECT material_no, plant_code, slip_days, day,
                   SUM(qty) OVER (PARTITION BY material_no, plant_code, slip_days ORDER BY day
                                  ROWS UNBOUNDED PRECEDING) AS available_qty
            FROM daily_supply
        ), availability AS (
            SELECT r.prod_order_no, r.need_date, sc.slip_days,
                   (SELECT MIN(s.day) FROM supply_balances s
                    WHERE s.material_no = r.material_no AND s.plant_code = r.plant_code
                      AND s.slip_days = sc.slip_days
                      AND s.available_qty >= d.required_qty) AS available_date
            FROM requirements r JOIN cumulative_demand d
              ON d.material_no = r.material_no AND d.plant_code = r.plant_code
                  AND d.need_date = r.need_date
            CROSS JOIN scenarios sc
            WHERE r.prod_order_no IS NOT NULL
        ), line_risk AS (
            SELECT i.so_no, i.line_no, c.name AS customer_name, i.promised_date,
                   p.planned_start, a.slip_days,
                   MAX(datediff('day', p.planned_start, a.available_date)) AS days_at_risk
            FROM availability a JOIN production_order p ON p.prod_order_no = a.prod_order_no
            JOIN sales_order_item i ON i.so_no = p.so_no AND i.line_no = p.so_line_no
            JOIN sales_order h ON h.so_no = i.so_no
            JOIN customer c ON c.customer_no = h.customer_no
            JOIN material m ON m.material_no = p.material_no
            WHERE i.status = 'Open' AND m.material_type = 'TOOL' AND m.family_code = 'ETC'
              AND h.order_date <= DATE '2026-11-01'
            GROUP BY i.so_no, i.line_no, c.name, i.promised_date, p.planned_start, a.slip_days
        ), compared AS (
            SELECT so_no, line_no,
                   MAX(CASE WHEN slip_days = 0 AND days_at_risk > 0 THEN 1 ELSE 0 END) AS baseline,
                   MAX(CASE WHEN slip_days = (SELECT slip_days FROM parameters)
                             AND days_at_risk > 0 THEN 1 ELSE 0 END) AS slipped
            FROM line_risk GROUP BY so_no, line_no
        )
        SELECT SUM(baseline) AS baseline_at_risk, SUM(slipped) AS slipped_at_risk,
               SUM(CASE WHEN slipped = 1 AND baseline = 0 THEN 1 ELSE 0 END) AS additional_at_risk
        FROM compared
        """,
    ),
    (
        "plan-corvane-what-if",
        """
        WITH RECURSIVE tree AS (
            SELECT m.material_no AS root, m.family_code, b.component_material_no AS material_no,
                   CAST(b.qty_per AS DECIMAL(20,6)) AS qty, 1 AS level
            FROM material m JOIN bom_item b ON b.parent_material_no = m.material_no
            WHERE m.material_type = 'TOOL'
            UNION ALL
            SELECT t.root, t.family_code, b.component_material_no,
                   CAST(t.qty * b.qty_per AS DECIMAL(20,6)), t.level + 1
            FROM tree t JOIN bom_item b ON b.parent_material_no = t.material_no
            WHERE t.level < 10
        ), leaves AS (
            SELECT t.root, t.family_code, t.material_no, SUM(t.qty) AS qty
            FROM tree t JOIN material m ON m.material_no = t.material_no
            WHERE m.material_type = 'COMPONENT'
            GROUP BY t.root, t.family_code, t.material_no
        ), outstanding AS (
            SELECT i.po_no, i.line_no, i.material_no, h.plant_code, h.supplier_no,
                   i.confirmed_delivery_date, i.order_qty - COALESCE(SUM(g.qty), 0) AS qty
            FROM purchase_order_item i JOIN purchase_order h ON h.po_no = i.po_no
            LEFT JOIN goods_receipt g ON g.po_no = i.po_no AND g.line_no = i.line_no
            WHERE h.order_date <= DATE '2026-11-01'
            GROUP BY i.po_no, i.line_no, i.material_no, h.plant_code, h.supplier_no,
                     i.confirmed_delivery_date, i.order_qty
            HAVING i.order_qty > COALESCE(SUM(g.qty), 0)
        ), stock AS (
            SELECT material_no, plant_code, SUM(qty_on_hand) AS qty FROM inventory
            WHERE storage_location = 'RM01'
            GROUP BY material_no, plant_code
        ), spare_plant AS (
            SELECT material_no, MIN(CASE family_code WHEN 'ETC' THEN 'SC01'
                                   WHEN 'CMP' THEN 'SG01' ELSE 'AU01' END) AS plant_code
            FROM leaves GROUP BY material_no
        ), requirements AS (
            SELECT p.prod_order_no, l.material_no, p.plant_code, p.planned_start AS need_date,
                   p.qty * l.qty AS qty
            FROM production_order p JOIN leaves l ON l.root = p.material_no
            JOIN material m ON m.material_no = l.material_no
            WHERE p.planned_start > DATE '2026-11-01' AND m.description LIKE 'RF generator,%'
            UNION ALL
            SELECT NULL, i.material_no, sp.plant_code, i.promised_date, i.qty
            FROM sales_order_item i JOIN spare_plant sp ON sp.material_no = i.material_no
            JOIN material m ON m.material_no = i.material_no
            WHERE i.status = 'Open' AND i.promised_date > DATE '2026-11-01'
              AND m.description LIKE 'RF generator,%'
        ), daily_demand AS (
            SELECT material_no, plant_code, need_date, SUM(qty) AS qty FROM requirements
            GROUP BY material_no, plant_code, need_date
        ), cumulative_demand AS (
            SELECT material_no, plant_code, need_date,
                   SUM(qty) OVER (PARTITION BY material_no, plant_code ORDER BY need_date
                                  ROWS UNBOUNDED PRECEDING) AS required_qty
            FROM daily_demand
        ), parameters AS (
            SELECT 28 AS slip_days
        ), scenarios AS (
            SELECT 0 AS slip_days UNION ALL SELECT slip_days FROM parameters
        ), supply_events AS (
            SELECT s.material_no, s.plant_code, sc.slip_days, DATE '2026-11-01' AS day, s.qty
            FROM stock s CROSS JOIN scenarios sc
            UNION ALL
            SELECT o.material_no, o.plant_code, sc.slip_days,
                   CAST(GREATEST(o.confirmed_delivery_date
                        + CASE WHEN o.supplier_no = 'V-10017' THEN sc.slip_days ELSE 0 END
                          * INTERVAL 1 DAY, DATE '2026-11-01') AS DATE), o.qty
            FROM outstanding o CROSS JOIN scenarios sc
            WHERE o.confirmed_delivery_date IS NOT NULL
        ), daily_supply AS (
            SELECT material_no, plant_code, slip_days, day, SUM(qty) AS qty FROM supply_events
            GROUP BY material_no, plant_code, slip_days, day
        ), supply_balances AS (
            SELECT material_no, plant_code, slip_days, day,
                   SUM(qty) OVER (PARTITION BY material_no, plant_code, slip_days ORDER BY day
                                  ROWS UNBOUNDED PRECEDING) AS available_qty
            FROM daily_supply
        ), availability AS (
            SELECT r.prod_order_no, r.need_date, sc.slip_days,
                   (SELECT MIN(s.day) FROM supply_balances s
                    WHERE s.material_no = r.material_no AND s.plant_code = r.plant_code
                      AND s.slip_days = sc.slip_days
                      AND s.available_qty >= d.required_qty) AS available_date
            FROM requirements r JOIN cumulative_demand d
              ON d.material_no = r.material_no AND d.plant_code = r.plant_code
                  AND d.need_date = r.need_date
            CROSS JOIN scenarios sc
            WHERE r.prod_order_no IS NOT NULL
        ), line_risk AS (
            SELECT i.so_no, i.line_no, c.name AS customer_name, i.promised_date,
                   p.planned_start, a.slip_days,
                   MAX(datediff('day', p.planned_start, a.available_date)) AS days_at_risk
            FROM availability a JOIN production_order p ON p.prod_order_no = a.prod_order_no
            JOIN sales_order_item i ON i.so_no = p.so_no AND i.line_no = p.so_line_no
            JOIN sales_order h ON h.so_no = i.so_no
            JOIN customer c ON c.customer_no = h.customer_no
            JOIN material m ON m.material_no = p.material_no
            WHERE i.status = 'Open' AND m.material_type = 'TOOL' AND m.family_code = 'ETC'
              AND h.order_date <= DATE '2026-11-01'
            GROUP BY i.so_no, i.line_no, c.name, i.promised_date, p.planned_start, a.slip_days
        ), compared AS (
            SELECT so_no, line_no,
                   MAX(CASE WHEN slip_days = 0 AND days_at_risk > 0 THEN 1 ELSE 0 END) AS baseline,
                   MAX(CASE WHEN slip_days = (SELECT slip_days FROM parameters)
                             AND days_at_risk > 0 THEN 1 ELSE 0 END) AS slipped
            FROM line_risk GROUP BY so_no, line_no
        )
        SELECT SUM(baseline) AS baseline_at_risk, SUM(slipped) AS slipped_at_risk,
               SUM(slipped) AS additional_at_risk
        FROM compared
        """,
    ),
    (
        "drill-late-suppliers",
        """
        WITH late AS (
            SELECT h.supplier_no, s.name AS supplier_name, COUNT(*) AS late_receipts
            FROM goods_receipt g JOIN purchase_order_item i
          ON i.po_no = g.po_no AND i.line_no = g.line_no
            JOIN purchase_order h ON h.po_no = i.po_no
        JOIN supplier s ON s.supplier_no = h.supplier_no
            WHERE g.receipt_date >= DATE '2025-11-01' AND g.receipt_date < DATE '2026-11-01'
              AND g.receipt_date > i.requested_delivery_date
            GROUP BY h.supplier_no, s.name
        )
        SELECT supplier_no, supplier_name, late_receipts,
               ROUND(100.0 * late_receipts / SUM(late_receipts) OVER (), 2) AS share_pct,
               ROUND(100.0 * SUM(late_receipts) OVER (ORDER BY late_receipts DESC, supplier_no
                     ROWS UNBOUNDED PRECEDING) / SUM(late_receipts) OVER (), 2) AS cumulative_pct
        FROM late ORDER BY late_receipts DESC, supplier_no
        """,
    ),
    (
        "drill-late-suppliers",
        """
        WITH late AS (
            SELECT h.supplier_no, s.name AS supplier_name, COUNT(DISTINCT i.po_no) AS late_receipts
            FROM goods_receipt g JOIN purchase_order_item i
          ON i.po_no = g.po_no AND i.line_no = g.line_no
            JOIN purchase_order h ON h.po_no = i.po_no
        JOIN supplier s ON s.supplier_no = h.supplier_no
            WHERE g.receipt_date >= DATE '2025-11-01' AND g.receipt_date < DATE '2026-11-01'
              AND g.receipt_date > i.confirmed_delivery_date
            GROUP BY h.supplier_no, s.name
        )
        SELECT supplier_no, supplier_name, late_receipts,
               ROUND(100.0 * late_receipts / SUM(late_receipts) OVER (), 2) AS share_pct,
               ROUND(100.0 * SUM(late_receipts) OVER (ORDER BY late_receipts DESC, supplier_no
                     ROWS UNBOUNDED PRECEDING) / SUM(late_receipts) OVER (), 2) AS cumulative_pct
        FROM late ORDER BY late_receipts DESC, supplier_no
        """,
    ),
    (
        "drill-halcyon-demand",
        """
        WITH bookings AS (
            SELECT SUM(CASE WHEN h.order_date >= DATE '2025-07-01'
                            AND h.order_date < DATE '2025-11-01'
                            THEN i.qty ELSE 0 END) AS booked_2025,
                   SUM(CASE WHEN h.order_date >= DATE '2026-07-01'
                            AND h.order_date < DATE '2026-11-01'
                            THEN i.qty ELSE 0 END) AS booked_2026
            FROM sales_order_item i JOIN sales_order h ON h.so_no = i.so_no
            JOIN material m ON m.material_no = i.material_no
            WHERE h.customer_no IS NOT NULL AND m.material_type = 'TOOL' AND m.family_code = 'ETC'
              AND i.status <> 'Cancelled'
        ), forecast AS (
            SELECT SUM(CASE WHEN f.forecast_version = '2026-09' THEN f.qty ELSE 0 END)
                   AS forecast_09,
                   SUM(CASE WHEN f.forecast_version = '2026-10' THEN f.qty ELSE 0 END)
                   AS forecast_10
            FROM demand_forecast f JOIN material m ON m.material_no = f.material_no
            WHERE m.material_type = 'TOOL' AND m.family_code = 'ETC'
              AND f.forecast_month >= DATE '2026-11-01' AND f.forecast_month < DATE '2027-02-01'
              AND f.forecast_version IN ('2026-09', '2026-10')
        )
        SELECT b.booked_2025, b.booked_2026,
               ROUND(100.0 * (b.booked_2026 - b.booked_2025) / NULLIF(b.booked_2025, 0), 2)
                   AS booking_growth_pct,
               f.forecast_09, f.forecast_10,
               ROUND(100.0 * (f.forecast_10 - f.forecast_09) / NULLIF(f.forecast_09, 0), 2)
                   AS forecast_growth_pct
        FROM bookings b CROSS JOIN forecast f
        """,
    ),
    (
        "drill-halcyon-demand",
        """
        WITH bookings AS (
            SELECT SUM(CASE WHEN h.order_date >= DATE '2025-01-01'
                            AND h.order_date < DATE '2025-11-01'
                            THEN i.qty ELSE 0 END) AS booked_2025,
                   SUM(CASE WHEN h.order_date >= DATE '2026-07-01'
                            AND h.order_date < DATE '2026-11-01'
                            THEN i.qty ELSE 0 END) AS booked_2026
            FROM sales_order_item i JOIN sales_order h ON h.so_no = i.so_no
            JOIN material m ON m.material_no = i.material_no
            WHERE h.customer_no = 'C-2003' AND m.material_type = 'TOOL' AND m.family_code = 'ETC'
              AND i.status <> 'Cancelled'
        ), forecast AS (
            SELECT SUM(CASE WHEN f.forecast_version = '2026-09' THEN f.qty ELSE 0 END)
                   AS forecast_09,
                   SUM(CASE WHEN f.forecast_version = '2026-10' THEN f.qty ELSE 0 END)
                   AS forecast_10
            FROM demand_forecast f JOIN material m ON m.material_no = f.material_no
            WHERE m.material_type = 'TOOL' AND m.family_code = 'ETC'
              AND f.forecast_month >= DATE '2026-11-01' AND f.forecast_month < DATE '2027-02-01'
              AND f.forecast_version IN ('2026-09', '2026-10')
        )
        SELECT b.booked_2025, b.booked_2026,
               ROUND(100.0 * (b.booked_2026 - b.booked_2025) / NULLIF(b.booked_2025, 0), 2)
                   AS booking_growth_pct,
               f.forecast_09, f.forecast_10,
               ROUND(100.0 * (f.forecast_10 - f.forecast_09) / NULLIF(f.forecast_09, 0), 2)
                   AS forecast_growth_pct
        FROM bookings b CROSS JOIN forecast f
        """,
    ),
    (
        "drill-bay-bottleneck",
        """
        WITH weeks AS (
            SELECT DISTINCT CAST(date_trunc('week', planned_start) AS DATE) AS week_start
            FROM production_order
            WHERE planned_start >= DATE '2026-12-01' AND planned_start < DATE '2027-03-01'
        ), loads AS (
            SELECT bay_id, CAST(date_trunc('week', planned_start) AS DATE) AS week_start,
                   COUNT(*) AS starts
            FROM production_order
            WHERE planned_start >= DATE '2026-12-01' AND planned_start < DATE '2027-03-01'
            GROUP BY bay_id, CAST(date_trunc('week', planned_start) AS DATE)
        ), grid AS (
            SELECT b.bay_id, w.week_start, COALESCE(l.starts, 0) AS starts, b.weekly_slots
            FROM build_bay b CROSS JOIN weeks w
            LEFT JOIN loads l ON l.bay_id = b.bay_id AND l.week_start = w.week_start
        )
        SELECT bay_id, SUM(starts) AS total_starts, SUM(weekly_slots) AS total_slots,
               SUM(CASE WHEN starts > weekly_slots THEN starts - weekly_slots ELSE 0 END)
                   AS excess_starts,
               ROUND(100.0 * SUM(starts) / SUM(weekly_slots), 2) AS load_pct
        FROM grid WHERE starts > 0 GROUP BY bay_id ORDER BY load_pct DESC, bay_id
        """,
    ),
    (
        "drill-bay-bottleneck",
        """
        WITH weeks AS (
            SELECT DISTINCT CAST(date_trunc('week', planned_start) AS DATE) AS week_start
            FROM production_order
            WHERE planned_start >= DATE '2026-12-01' AND planned_start < DATE '2027-03-01'
        ), loads AS (
            SELECT bay_id, CAST(date_trunc('week', planned_start) AS DATE) AS week_start,
                   COUNT(*) AS starts
            FROM production_order
            WHERE planned_start >= DATE '2026-12-01' AND planned_start < DATE '2027-03-01'
            GROUP BY bay_id, CAST(date_trunc('week', planned_start) AS DATE)
        ), grid AS (
            SELECT b.bay_id, w.week_start, COALESCE(l.starts, 0) AS starts, b.weekly_slots
            FROM build_bay b CROSS JOIN weeks w
            LEFT JOIN loads l ON l.bay_id = b.bay_id AND l.week_start = w.week_start
        )
        SELECT bay_id, SUM(starts) AS total_starts, SUM(weekly_slots) AS total_slots,
               SUM(starts) - SUM(weekly_slots) AS excess_starts,
               ROUND(100.0 * SUM(starts) / SUM(weekly_slots), 2) AS load_pct
        FROM grid GROUP BY bay_id ORDER BY load_pct DESC, bay_id
        """,
    ),
    (
        "drill-spare-otd",
        """
        SELECT c.segment, COUNT(*) AS shipped_lines,
               SUM(CASE WHEN i.shipped_date <= i.promised_date THEN 1 ELSE 0 END) AS on_time_lines,
               ROUND(100.0 * SUM(CASE WHEN i.shipped_date <= i.promised_date THEN 1 ELSE 0 END)
                     / COUNT(*), 2) AS on_time_pct
        FROM sales_order_item i JOIN sales_order h ON h.so_no = i.so_no
        JOIN customer c ON c.customer_no = h.customer_no
        JOIN material m ON m.material_no = i.material_no
        WHERE m.material_type = 'TOOL' AND i.status = 'Shipped' AND i.promised_date IS NOT NULL
          AND i.shipped_date >= DATE '2025-11-01' AND i.shipped_date < DATE '2026-11-01'
        GROUP BY c.segment ORDER BY c.segment
        """,
    ),
    (
        "drill-spare-otd",
        """
        SELECT c.segment, COUNT(*) AS shipped_lines,
               SUM(CASE WHEN i.shipped_date <= i.promised_date THEN 1 ELSE 0 END) AS on_time_lines,
               ROUND(100.0 * SUM(CASE WHEN i.shipped_date <= i.promised_date THEN 1 ELSE 0 END)
                     / COUNT(*), 2) AS on_time_pct
        FROM sales_order_item i JOIN sales_order h ON h.so_no = i.so_no
        JOIN customer c ON c.customer_no = h.customer_no
        JOIN material m ON m.material_no = i.material_no
        WHERE m.material_type = 'COMPONENT' AND i.status = 'Shipped' AND i.promised_date IS NOT NULL
          AND h.order_date >= DATE '2025-11-01' AND i.shipped_date < DATE '2026-11-01'
        GROUP BY c.segment ORDER BY c.segment
        """,
    ),
    (
        "drill-excess-inventory",
        """
        WITH stock AS (
            SELECT material_no, SUM(qty_on_hand) AS on_hand_qty FROM inventory
            GROUP BY material_no HAVING SUM(qty_on_hand) > 0
        )
        SELECT m.material_no, m.description, s.on_hand_qty,
               ROUND(s.on_hand_qty * q.unit_price, 2) AS on_hand_value
        FROM stock s JOIN material m ON m.material_no = s.material_no
        JOIN material_supplier q ON q.material_no = s.material_no AND q.is_primary = true
        WHERE EXISTS (
            SELECT 1 FROM stock_movement u WHERE u.material_no = s.material_no
              AND u.movement_type IN ('261', '601')
              AND u.posting_date >= DATE '2025-11-01' AND u.posting_date < DATE '2026-11-01'
        )
        ORDER BY on_hand_value DESC, m.material_no LIMIT 10
        """,
    ),
    (
        "drill-single-source",
        """
        WITH RECURSIVE tree AS (
            SELECT m.material_no AS root, m.family_code, b.component_material_no AS material_no,
                   CAST(b.qty_per AS DECIMAL(20,6)) AS qty, 1 AS level
            FROM material m JOIN bom_item b ON b.parent_material_no = m.material_no
            WHERE m.material_type = 'TOOL'
            UNION ALL
            SELECT t.root, t.family_code, b.component_material_no,
                   CAST(t.qty * b.qty_per AS DECIMAL(20,6)), t.level + 1
            FROM tree t JOIN bom_item b ON b.parent_material_no = t.material_no
            WHERE t.level < 10
        ), leaves AS (
            SELECT t.root, t.family_code, t.material_no, SUM(t.qty) AS qty
            FROM tree t JOIN material m ON m.material_no = t.material_no
            WHERE m.material_type = 'COMPONENT'
            GROUP BY t.root, t.family_code, t.material_no
        ), single_source AS (
            SELECT material_no, MIN(supplier_no) AS supplier_no FROM material_supplier
            GROUP BY material_no HAVING COUNT(*) >= 1
        ), dependencies AS (
            SELECT l.material_no, i.so_no, i.line_no, i.qty * l.qty AS component_qty,
                   i.qty * i.unit_price AS revenue
            FROM leaves l JOIN sales_order_item i ON i.material_no = l.root
            JOIN sales_order h ON h.so_no = i.so_no
            WHERE i.status = 'Open' AND h.order_date <= DATE '2026-11-01'
        )
        SELECT d.material_no, s.supplier_no, SUM(d.component_qty) AS open_component_qty,
               COUNT(*) AS dependent_lines, ROUND(SUM(d.revenue), 2) AS exposed_revenue
        FROM dependencies d JOIN single_source s ON s.material_no = d.material_no
        JOIN material m ON m.material_no = d.material_no
        WHERE m.is_long_lead = true AND m.material_type = 'COMPONENT'
        GROUP BY d.material_no, s.supplier_no ORDER BY exposed_revenue DESC, d.material_no
        """,
    ),
    (
        "drill-single-source",
        """
        WITH RECURSIVE tree AS (
            SELECT m.material_no AS root, m.family_code, b.component_material_no AS material_no,
                   CAST(b.qty_per AS DECIMAL(20,6)) AS qty, 1 AS level
            FROM material m JOIN bom_item b ON b.parent_material_no = m.material_no
            WHERE m.material_type = 'TOOL'
            UNION ALL
            SELECT t.root, t.family_code, b.component_material_no,
                   CAST(t.qty * b.qty_per AS DECIMAL(20,6)), t.level + 1
            FROM tree t JOIN bom_item b ON b.parent_material_no = t.material_no
            WHERE t.level < 10
        ), leaves AS (
            SELECT t.root, t.family_code, t.material_no, SUM(t.qty) AS qty
            FROM tree t JOIN material m ON m.material_no = t.material_no
            WHERE m.material_type = 'COMPONENT'
            GROUP BY t.root, t.family_code, t.material_no
        ), single_source AS (
            SELECT material_no, MIN(supplier_no) AS supplier_no FROM material_supplier
            GROUP BY material_no HAVING COUNT(*) = 1
        ), dependencies AS (
            SELECT l.material_no, i.so_no, i.line_no, i.qty * l.qty AS component_qty,
                   i.qty * i.unit_price * l.qty AS revenue
            FROM leaves l JOIN sales_order_item i ON i.material_no = l.root
            JOIN sales_order h ON h.so_no = i.so_no
            WHERE i.status = 'Open' AND h.order_date <= DATE '2026-11-01'
        )
        SELECT d.material_no, s.supplier_no, SUM(d.component_qty) AS open_component_qty,
               COUNT(*) AS dependent_lines, ROUND(SUM(d.revenue), 2) AS exposed_revenue
        FROM dependencies d JOIN single_source s ON s.material_no = d.material_no
        JOIN material m ON m.material_no = d.material_no
        WHERE m.is_long_lead = true AND m.material_type = 'COMPONENT'
        GROUP BY d.material_no, s.supplier_no ORDER BY exposed_revenue DESC, d.material_no
        """,
    ),
    (
        "plan-shortages",
        """
WITH RECURSIVE tree AS (
    SELECT m.material_no AS root, m.family_code, b.component_material_no AS material_no,
           CAST(b.qty_per AS DECIMAL(20,6)) AS qty, 1 AS level
    FROM material m JOIN bom_item b ON b.parent_material_no = m.material_no
    WHERE m.material_type = 'TOOL'
    UNION ALL
    SELECT t.root, t.family_code, b.component_material_no,
           CAST(t.qty * b.qty_per AS DECIMAL(20,6)), t.level + 1
    FROM tree t JOIN bom_item b ON b.parent_material_no = t.material_no
    WHERE t.level < 10
), leaves AS (
    SELECT t.root, t.family_code, t.material_no, SUM(t.qty) AS qty
    FROM tree t JOIN material m ON m.material_no = t.material_no
    WHERE m.material_type = 'COMPONENT'
    GROUP BY t.root, t.family_code, t.material_no
), outstanding AS (
    SELECT i.po_no, i.line_no, i.material_no, h.plant_code, h.supplier_no,
           i.confirmed_delivery_date, i.order_qty - COALESCE(SUM(g.qty), 0) AS qty
    FROM purchase_order_item i JOIN purchase_order h ON h.po_no = i.po_no
    LEFT JOIN goods_receipt g ON g.po_no = i.po_no AND g.line_no = i.line_no
    WHERE h.order_date <= DATE '2026-11-01'
    GROUP BY i.po_no, i.line_no, i.material_no, h.plant_code, h.supplier_no,
             i.confirmed_delivery_date, i.order_qty
    HAVING i.order_qty > COALESCE(SUM(g.qty), 0)
), stock AS (
    SELECT material_no, plant_code, SUM(qty_on_hand) AS qty FROM inventory
    WHERE storage_location = 'RM01'
    GROUP BY material_no, plant_code
), periods AS (
    SELECT CAST(DATE '2026-11-01' + n * INTERVAL 7 DAY AS DATE) AS week_start
    FROM (VALUES (0), (1), (2), (3), (4), (5), (6), (7), (8), (9), (10), (11), (12),
                 (13), (14), (15), (16), (17), (18), (19), (20), (21), (22), (23), (24), (25))
                 AS offsets(n)
), targets AS (
    SELECT mp.material_no, mp.plant_code, mp.safety_stock
    FROM material_plant mp JOIN material m ON m.material_no = mp.material_no
    WHERE (m.is_long_lead = true OR m.description LIKE 'RF generator,%')
      AND EXISTS (
          SELECT 1 FROM production_order p JOIN leaves l ON l.root = p.material_no
          WHERE l.material_no = mp.material_no AND p.plant_code = mp.plant_code
            AND p.status IN ('Planned', 'Released')
            AND p.planned_start > DATE '2026-11-01' AND p.planned_start < DATE '2027-05-02'
      )
), demand AS (
    SELECT l.material_no, p.plant_code, p.planned_start AS day, SUM(p.qty * l.qty) AS qty
    FROM production_order p JOIN leaves l ON l.root = p.material_no
    WHERE p.status IN ('Planned', 'Released')
      AND p.planned_start > DATE '2026-11-01' AND p.planned_start < DATE '2027-05-02'
    GROUP BY l.material_no, p.plant_code, p.planned_start
), weekly AS (
    SELECT t.material_no, t.plant_code, t.safety_stock, w.week_start,
           COALESCE((SELECT SUM(o.qty) FROM outstanding o
                     WHERE o.material_no = t.material_no AND o.plant_code = t.plant_code
                       AND o.confirmed_delivery_date IS NOT NULL
                       AND GREATEST(o.confirmed_delivery_date, DATE '2026-11-01') >= w.week_start
                       AND GREATEST(o.confirmed_delivery_date, DATE '2026-11-01')
                           < w.week_start + INTERVAL 7 DAY), 0) AS receipts,
           COALESCE((SELECT SUM(d.qty) FROM demand d
                     WHERE d.material_no = t.material_no AND d.plant_code = t.plant_code
                       AND d.day >= w.week_start AND d.day < w.week_start + INTERVAL 7 DAY), 0)
               AS demand_qty,
           COALESCE(s.qty, 0) AS opening_qty
    FROM targets t CROSS JOIN periods w
    LEFT JOIN stock s ON s.material_no = t.material_no AND s.plant_code = t.plant_code
), balances AS (
    SELECT material_no, plant_code, safety_stock, week_start, receipts, demand_qty,
           opening_qty + SUM(receipts - demand_qty) OVER (
               PARTITION BY material_no, plant_code ORDER BY week_start
               ROWS UNBOUNDED PRECEDING) AS projected_balance
    FROM weekly
)
SELECT material_no, plant_code, MIN(week_start) AS first_shortage_week
FROM balances WHERE projected_balance < safety_stock
GROUP BY material_no, plant_code ORDER BY material_no, plant_code

        """,
    ),
    (
        "cte-open-qty-above-avg",
        """
        WITH received AS (
            SELECT po_no, line_no, SUM(qty) AS received_qty
            FROM goods_receipt
            GROUP BY po_no, line_no
        ),
        open_by_material AS (
            SELECT i.material_no, SUM(i.order_qty) AS open_qty
            FROM purchase_order_item AS i
            JOIN purchase_order AS h ON h.po_no = i.po_no
            LEFT JOIN received AS r ON r.po_no = i.po_no AND r.line_no = i.line_no
            WHERE h.order_date <= DATE '2026-11-01'
              AND i.order_qty > COALESCE(r.received_qty, 0)
            GROUP BY i.material_no
        )
        SELECT t.material_no, m.description, t.open_qty
        FROM open_by_material AS t
        JOIN material AS m ON m.material_no = t.material_no
        WHERE t.open_qty > (SELECT AVG(open_qty) FROM open_by_material)
        ORDER BY t.open_qty DESC, t.material_no
        """,
    ),
    (
        "cte-open-qty-above-avg",
        """
        WITH line AS (
            SELECT i.material_no,
                   i.order_qty - COALESCE(SUM(g.qty), 0) AS open_qty
            FROM purchase_order_item AS i
            JOIN purchase_order AS h ON h.po_no = i.po_no
            LEFT JOIN goods_receipt AS g ON g.po_no = i.po_no AND g.line_no = i.line_no
            WHERE h.order_date <= DATE '2026-11-01'
            GROUP BY i.po_no, i.line_no, i.material_no, i.order_qty
            HAVING i.order_qty > COALESCE(SUM(g.qty), 0)
        ),
        open_by_material AS (
            SELECT material_no, SUM(open_qty) AS open_qty
            FROM line
            GROUP BY material_no
        )
        SELECT t.material_no, m.description, t.open_qty
        FROM open_by_material AS t
        JOIN material AS m ON m.material_no = t.material_no
        WHERE t.open_qty > (SELECT AVG(open_qty) FROM line)
        ORDER BY t.open_qty DESC, t.material_no
        """,
    ),
    (
        "cte-open-qty-with-cte",
        """
        WITH open_by_material AS (
            SELECT i.material_no,
                   i.order_qty - COALESCE(SUM(g.qty), 0) AS open_qty
            FROM purchase_order_item AS i
            JOIN purchase_order AS h ON h.po_no = i.po_no
            LEFT JOIN goods_receipt AS g ON g.po_no = i.po_no AND g.line_no = i.line_no
            WHERE h.order_date <= DATE '2026-11-01'
            GROUP BY i.po_no, i.line_no, i.material_no, i.order_qty
            HAVING i.order_qty > COALESCE(SUM(g.qty), 0)
        )
        SELECT t.material_no, m.description, SUM(t.open_qty) AS open_qty
        FROM open_by_material AS t
        JOIN material AS m ON m.material_no = t.material_no
        GROUP BY t.material_no, m.description
        HAVING SUM(t.open_qty) > (SELECT AVG(open_qty) FROM open_by_material)
        ORDER BY open_qty DESC, t.material_no
        """,
    ),
    (
        "cte-supplier-on-time-rank",
        """
        WITH rates AS (
            SELECT s.supplier_no, s.name AS supplier_name, COUNT(*) AS receipts,
                   ROUND(100.0 * AVG(CASE
                       WHEN g.receipt_date <= i.confirmed_delivery_date THEN 1.0
                       ELSE 0.0 END), 1) AS on_time_pct
            FROM goods_receipt AS g
            JOIN purchase_order_item AS i ON i.po_no = g.po_no AND i.line_no = g.line_no
            JOIN purchase_order AS h ON h.po_no = g.po_no
            JOIN supplier AS s ON s.supplier_no = h.supplier_no
            WHERE g.receipt_date >= DATE '2025-11-01'
              AND g.receipt_date < DATE '2026-11-01'
            GROUP BY s.supplier_no, s.name
            HAVING COUNT(*) >= 20
        )
        SELECT supplier_no, supplier_name, receipts, on_time_pct,
               ROW_NUMBER() OVER (ORDER BY on_time_pct DESC) AS rank_no
        FROM rates
        ORDER BY rank_no, supplier_no
        """,
    ),
    (
        "cte-supplier-on-time-rank",
        """
        WITH rates AS (
            SELECT s.supplier_no, s.name AS supplier_name, COUNT(*) AS receipts,
                   ROUND(100.0 * AVG(CASE
                       WHEN g.receipt_date <= i.confirmed_delivery_date THEN 1.0
                       ELSE 0.0 END), 1) AS on_time_pct
            FROM goods_receipt AS g
            JOIN purchase_order_item AS i ON i.po_no = g.po_no AND i.line_no = g.line_no
            JOIN purchase_order AS h ON h.po_no = g.po_no
            JOIN supplier AS s ON s.supplier_no = h.supplier_no
            WHERE g.receipt_date >= DATE '2025-11-01'
              AND g.receipt_date < DATE '2026-11-01'
            GROUP BY s.supplier_no, s.name
            HAVING COUNT(*) >= 20
        )
        SELECT supplier_no, supplier_name, receipts, on_time_pct,
               RANK() OVER (ORDER BY on_time_pct DESC, supplier_no) AS rank_no
        FROM rates
        ORDER BY rank_no, supplier_no
        """,
    ),
    (
        "cte-latest-receipt",
        """
        WITH numbered AS (
            SELECT i.po_no, i.line_no, g.gr_no, g.receipt_date, g.qty,
                   ROW_NUMBER() OVER (
                       PARTITION BY i.po_no, i.line_no
                       ORDER BY g.receipt_date, g.gr_no DESC
                   ) AS rn
            FROM purchase_order_item AS i
            JOIN purchase_order AS h ON h.po_no = i.po_no
            JOIN goods_receipt AS g ON g.po_no = i.po_no AND g.line_no = i.line_no
            WHERE h.order_date >= DATE '2026-10-01'
              AND h.order_date <= DATE '2026-10-31'
        )
        SELECT po_no, line_no, gr_no, receipt_date, qty
        FROM numbered WHERE rn = 1
        ORDER BY po_no, line_no
        """,
    ),
    (
        "cte-latest-receipt",
        """
        WITH numbered AS (
            SELECT i.po_no, i.line_no, g.gr_no, g.receipt_date, g.qty,
                   ROW_NUMBER() OVER (
                       PARTITION BY i.po_no, i.line_no
                       ORDER BY g.receipt_date DESC, g.gr_no
                   ) AS rn
            FROM purchase_order_item AS i
            JOIN purchase_order AS h ON h.po_no = i.po_no
            JOIN goods_receipt AS g ON g.po_no = i.po_no AND g.line_no = i.line_no
            WHERE h.order_date >= DATE '2026-10-01'
              AND h.order_date <= DATE '2026-10-31'
        )
        SELECT po_no, line_no, gr_no, receipt_date, qty
        FROM numbered WHERE rn = 1
        ORDER BY po_no, line_no
        """,
    ),
    (
        "cte-latest-receipt",
        """
        WITH numbered AS (
            SELECT i.po_no, i.line_no, g.gr_no, g.receipt_date, g.qty,
                   ROW_NUMBER() OVER (
                       PARTITION BY i.po_no, i.line_no
                       ORDER BY g.receipt_date DESC, g.gr_no DESC
                   ) AS rn
            FROM purchase_order_item AS i
            JOIN purchase_order AS h ON h.po_no = i.po_no
            JOIN goods_receipt AS g ON g.po_no = i.po_no AND g.line_no = i.line_no
            WHERE g.receipt_date >= DATE '2026-10-01'
              AND g.receipt_date <= DATE '2026-10-31'
        )
        SELECT po_no, line_no, gr_no, receipt_date, qty
        FROM numbered WHERE rn = 1
        ORDER BY po_no, line_no
        """,
    ),
    (
        "cte-rf-running-total",
        """
        WITH monthly AS (
            SELECT CAST(date_trunc('month', g.receipt_date) AS DATE) AS month,
                   SUM(g.qty) AS month_qty
            FROM goods_receipt AS g
            JOIN purchase_order_item AS i ON i.po_no = g.po_no AND i.line_no = g.line_no
            JOIN material AS m ON m.material_no = i.material_no
            WHERE m.description LIKE 'RF generator,%'
              AND g.receipt_date >= DATE '2026-01-01'
              AND g.receipt_date < DATE '2026-11-01'
            GROUP BY CAST(date_trunc('month', g.receipt_date) AS DATE)
        )
        SELECT month, month_qty, month_qty AS running_qty
        FROM monthly
        ORDER BY month
        """,
    ),
    (
        "cte-rf-running-total",
        """
        WITH monthly AS (
            SELECT CAST(date_trunc('month', g.receipt_date) AS DATE) AS month,
                   SUM(g.qty) AS month_qty
            FROM goods_receipt AS g
            JOIN purchase_order_item AS i ON i.po_no = g.po_no AND i.line_no = g.line_no
            JOIN material AS m ON m.material_no = i.material_no
            WHERE (m.description LIKE 'RF generator,%' OR m.description = 'Spare RF generator kit')
              AND g.receipt_date >= DATE '2026-01-01'
              AND g.receipt_date < DATE '2026-11-01'
            GROUP BY CAST(date_trunc('month', g.receipt_date) AS DATE)
        )
        SELECT month,
               month_qty,
               SUM(month_qty) OVER (
                   ORDER BY month ROWS UNBOUNDED PRECEDING
               ) AS running_qty
        FROM monthly
        ORDER BY month
        """,
    ),
    (
        "cte-tool-order-change",
        """
        WITH monthly AS (
            SELECT CAST(date_trunc('month', h.order_date) AS DATE) AS month,
                   SUM(i.qty) AS order_qty
            FROM sales_order_item AS i
            JOIN sales_order AS h ON h.so_no = i.so_no
            JOIN material AS m ON m.material_no = i.material_no
            WHERE m.material_type = 'TOOL'
              AND h.order_date >= DATE '2025-01-01'
              AND h.order_date < DATE '2026-11-01'
            GROUP BY CAST(date_trunc('month', h.order_date) AS DATE)
        )
        SELECT month, order_qty,
               order_qty - COALESCE(LAG(order_qty) OVER (ORDER BY month), 0) AS qty_change
        FROM monthly
        ORDER BY month
        """,
    ),
    (
        "cte-customer-share",
        """
        WITH open_lines AS (
            SELECT h.customer_no, c.name AS customer_name, SUM(i.qty) AS open_qty
            FROM sales_order_item AS i
            JOIN sales_order AS h ON h.so_no = i.so_no
            JOIN customer AS c ON c.customer_no = h.customer_no
            JOIN material AS m ON m.material_no = i.material_no
            WHERE m.material_type = 'TOOL'
            GROUP BY h.customer_no, c.name
        )
        SELECT customer_no, customer_name, open_qty,
               ROUND(100.0 * open_qty / SUM(open_qty) OVER (), 1) AS share_pct
        FROM open_lines
        ORDER BY share_pct DESC, customer_no
        """,
    ),
    (
        "cte-customer-share",
        """
        WITH open_lines AS (
            SELECT h.customer_no, c.name AS customer_name, SUM(i.qty) AS open_qty
            FROM sales_order_item AS i
            JOIN sales_order AS h ON h.so_no = i.so_no
            JOIN customer AS c ON c.customer_no = h.customer_no
            JOIN material AS m ON m.material_no = i.material_no
            WHERE i.status = 'Open'
              AND m.material_type = 'TOOL'
            GROUP BY h.customer_no, c.name
        )
        SELECT customer_no, customer_name, open_qty,
               ROUND(open_qty / SUM(open_qty) OVER (), 1) AS share_pct
        FROM open_lines
        ORDER BY share_pct DESC, customer_no
        """,
    ),
    (
        "cte-top-suppliers-by-country",
        """
        WITH spend AS (
            SELECT s.country, s.supplier_no, s.name AS supplier_name,
                   ROUND(SUM(i.order_qty * i.net_price), 2) AS spend
            FROM purchase_order_item AS i
            JOIN purchase_order AS h ON h.po_no = i.po_no
            JOIN supplier AS s ON s.supplier_no = h.supplier_no
            WHERE h.order_date >= DATE '2025-11-01'
              AND h.order_date < DATE '2026-11-01'
            GROUP BY s.country, s.supplier_no, s.name
        ),
        ranked AS (
            SELECT country, supplier_no, supplier_name, spend,
                   ROW_NUMBER() OVER (ORDER BY spend DESC, supplier_no) AS rank_no
            FROM spend
        )
        SELECT country, supplier_no, supplier_name, spend, rank_no
        FROM ranked WHERE rank_no <= 3
        ORDER BY country, rank_no
        """,
    ),
    (
        "bom-direct-children",
        """
        SELECT b.component_material_no, m.description, b.qty_per
        FROM bom_item AS b
        JOIN material AS m ON m.material_no = b.component_material_no
        WHERE b.parent_material_no = 'M-000001'
        ORDER BY b.component_material_no
        """,
    ),
    (
        "bom-full-explosion",
        """
        SELECT 1 AS level, b.parent_material_no, b.component_material_no, m.description
        FROM bom_item AS b
        JOIN material AS m ON m.material_no = b.component_material_no
        WHERE b.parent_material_no = 'M-000011'
        ORDER BY level, b.component_material_no, b.parent_material_no
        """,
    ),
    (
        "bom-extended-qty",
        """
        WITH RECURSIVE tree AS (
            SELECT b.parent_material_no, b.component_material_no,
                   CAST(b.qty_per AS DECIMAL(20,6)) AS extended_qty, 1 AS level
            FROM bom_item AS b
            WHERE b.parent_material_no = 'M-000011'
            UNION ALL
            SELECT b.parent_material_no, b.component_material_no,
                   CAST(b.qty_per AS DECIMAL(20,6)), t.level + 1
            FROM tree AS t
            JOIN bom_item AS b ON b.parent_material_no = t.component_material_no
            WHERE t.level < 10
        )
        SELECT t.level, t.parent_material_no, t.component_material_no,
               m.description, t.extended_qty
        FROM tree AS t
        JOIN material AS m ON m.material_no = t.component_material_no
        ORDER BY t.level, t.component_material_no, t.parent_material_no
        """,
    ),
    (
        "bom-long-lead-count",
        """
        SELECT tool.material_no AS tool_material_no,
               tool.description,
               COUNT(DISTINCT c.material_no) AS long_lead_count
        FROM material AS tool
        LEFT JOIN bom_item AS b ON b.parent_material_no = tool.material_no
        LEFT JOIN material AS c
          ON c.material_no = b.component_material_no
         AND c.material_type = 'COMPONENT'
         AND c.is_long_lead = true
        WHERE tool.material_type = 'TOOL'
        GROUP BY tool.material_no, tool.description
        ORDER BY long_lead_count DESC, tool.material_no
        """,
    ),
    (
        "bom-long-lead-count",
        """
        WITH RECURSIVE tree AS (
            SELECT m.material_no AS tool_no,
                   b.component_material_no AS material_no,
                   1 AS level
            FROM material AS m
            JOIN bom_item AS b ON b.parent_material_no = m.material_no
            WHERE m.material_type = 'TOOL'
            UNION ALL
            SELECT t.tool_no, b.component_material_no, t.level + 1
            FROM tree AS t
            JOIN bom_item AS b ON b.parent_material_no = t.material_no
            WHERE t.level < 10
        ),
        parts AS (
            SELECT DISTINCT t.tool_no, t.material_no
            FROM tree AS t
            JOIN material AS c ON c.material_no = t.material_no
            WHERE c.material_type = 'COMPONENT'
        )
        SELECT tool.material_no AS tool_material_no,
               tool.description,
               COUNT(parts.material_no) AS long_lead_count
        FROM material AS tool
        LEFT JOIN parts ON parts.tool_no = tool.material_no
        WHERE tool.material_type = 'TOOL'
        GROUP BY tool.material_no, tool.description
        ORDER BY long_lead_count DESC, tool.material_no
        """,
    ),
    (
        "bom-where-used",
        """
        SELECT m.material_no, m.description
        FROM bom_item AS b
        JOIN material AS m ON m.material_no = b.parent_material_no
        WHERE b.component_material_no = 'M-001324'
          AND m.material_type = 'TOOL'
        ORDER BY m.material_no
        """,
    ),
    (
        "bom-rf-for-open-orders",
        """
        WITH RECURSIVE tree AS (
            SELECT m.material_no AS tool_no, b.component_material_no AS material_no,
                   CAST(b.qty_per AS DECIMAL(20,6)) AS extended_qty, 1 AS level
            FROM material AS m
            JOIN bom_item AS b ON b.parent_material_no = m.material_no
            WHERE m.material_type = 'TOOL'
            UNION ALL
            SELECT t.tool_no, b.component_material_no,
                   CAST(t.extended_qty * b.qty_per AS DECIMAL(20,6)), t.level + 1
            FROM tree AS t
            JOIN bom_item AS b ON b.parent_material_no = t.material_no
            WHERE t.level < 10
        ),
        per_tool AS (
            SELECT t.tool_no, t.material_no, SUM(t.extended_qty) AS qty_per_tool
            FROM tree AS t
            JOIN material AS c ON c.material_no = t.material_no
            WHERE c.material_type = 'COMPONENT' AND c.description LIKE 'RF generator,%'
            GROUP BY t.tool_no, t.material_no
        )
        SELECT p.material_no, c.description, SUM(p.qty_per_tool) AS required_qty
        FROM per_tool AS p
        JOIN sales_order_item AS i ON i.material_no = p.tool_no
        JOIN material AS c ON c.material_no = p.material_no
        WHERE i.status = 'Open'
        GROUP BY p.material_no, c.description
        ORDER BY p.material_no
        """,
    ),
    (
        "bom-rf-for-open-orders",
        """
        WITH RECURSIVE tree AS (
            SELECT m.material_no AS tool_no, b.component_material_no AS material_no,
                   CAST(b.qty_per AS DECIMAL(20,6)) AS extended_qty, 1 AS level
            FROM material AS m
            JOIN bom_item AS b ON b.parent_material_no = m.material_no
            WHERE m.material_type = 'TOOL'
            UNION ALL
            SELECT t.tool_no, b.component_material_no,
                   CAST(t.extended_qty * b.qty_per AS DECIMAL(20,6)), t.level + 1
            FROM tree AS t
            JOIN bom_item AS b ON b.parent_material_no = t.material_no
            WHERE t.level < 10
        ),
        per_tool AS (
            SELECT t.tool_no, t.material_no, SUM(t.extended_qty) AS qty_per_tool
            FROM tree AS t
            JOIN material AS c ON c.material_no = t.material_no
            WHERE c.material_type = 'COMPONENT' AND c.description LIKE 'RF generator,%'
            GROUP BY t.tool_no, t.material_no
        )
        SELECT p.material_no, c.description, SUM(p.qty_per_tool * i.qty) AS required_qty
        FROM per_tool AS p
        JOIN sales_order_item AS i ON i.material_no = p.tool_no
        JOIN material AS c ON c.material_no = p.material_no
        GROUP BY p.material_no, c.description
        ORDER BY p.material_no
        """,
    ),
)


@pytest.fixture
def latest_receipt_scenario():
    """Separate latest-date ties from the October order-date requirement."""
    with duckdb.connect(":memory:") as connection:
        connection.execute("""
            CREATE TABLE purchase_order (po_no VARCHAR, order_date DATE);
            CREATE TABLE purchase_order_item (po_no VARCHAR, line_no INTEGER);
            CREATE TABLE goods_receipt (
                po_no VARCHAR, line_no INTEGER, gr_no VARCHAR, receipt_date DATE, qty INTEGER
            );
            INSERT INTO purchase_order VALUES
                ('PO-OCT', DATE '2026-10-01'), ('PO-SEP', DATE '2026-09-30');
            INSERT INTO purchase_order_item VALUES ('PO-OCT', 1), ('PO-SEP', 1);
            INSERT INTO goods_receipt VALUES
                ('PO-OCT', 1, 'GR-1', DATE '2026-10-02', 1),
                ('PO-OCT', 1, 'GR-2', DATE '2026-11-01', 2),
                ('PO-OCT', 1, 'GR-3', DATE '2026-11-01', 3),
                ('PO-SEP', 1, 'GR-4', DATE '2026-10-03', 4);
        """)
        yield connection


def test_latest_receipt_reference_keeps_latest_tie_on_october_order(latest_receipt_scenario):
    exercise = next(item for item in _exercises() if item.id == "cte-latest-receipt")
    run = _scenario_runner(latest_receipt_scenario)
    for sql in exercise.reference_sql:
        assert run(sql).rows == [("PO-OCT", 1, "GR-3", date(2026, 11, 1), 3)]


@pytest.fixture
def rf_open_order_scenario():
    """Open quantities above one and closed lines expose separate demand mistakes."""
    with duckdb.connect(":memory:") as connection:
        connection.execute("""
            CREATE TABLE material (
                material_no VARCHAR, description VARCHAR, material_type VARCHAR
            );
            CREATE TABLE bom_item (
                parent_material_no VARCHAR, component_material_no VARCHAR, qty_per INTEGER
            );
            CREATE TABLE sales_order_item (material_no VARCHAR, qty INTEGER, status VARCHAR);
            INSERT INTO material VALUES
                ('TOOL', 'Test tool', 'TOOL'),
                ('ASSEMBLY', 'Test assembly', 'ASSEMBLY'),
                ('RF', 'RF generator, 13.56 MHz, 3 kW', 'COMPONENT');
            INSERT INTO bom_item VALUES
                ('TOOL', 'ASSEMBLY', 2), ('ASSEMBLY', 'RF', 3), ('TOOL', 'RF', 1);
            INSERT INTO sales_order_item VALUES ('TOOL', 3, 'Open'), ('TOOL', 5, 'Closed');
        """)
        yield connection


def test_rf_reference_multiplies_open_quantity_only(rf_open_order_scenario):
    exercise = next(item for item in _exercises() if item.id == "bom-rf-for-open-orders")
    run = _scenario_runner(rf_open_order_scenario)
    for sql in exercise.reference_sql:
        assert run(sql).rows == [("RF", "RF generator, 13.56 MHz, 3 kW", 21)]


def test_common_wrong_answers_fail(
    lab_home, reference_results, latest_receipt_scenario, rf_open_order_scenario
) -> None:
    assert lab_home is not None
    exercises = {item.id: item for item in _exercises()}
    scenarios = {
        "cte-latest-receipt": latest_receipt_scenario,
        "bom-rf-for-open-orders": rf_open_order_scenario,
    }
    for exercise_id, sql in _WRONG_ANSWERS:
        exercise = exercises[exercise_id]
        runner = _scenario_runner(scenarios[exercise_id]) if exercise_id in scenarios else _run
        outcome = check_answer(exercise, sql, runner=runner)
        assert outcome.passed is False, exercise_id
        if exercise_id == "join-open-rf-po-lines" and "JOIN goods_receipt AS g" not in sql:
            wrong_rows = _run(sql).total_rows
            right_rows = reference_results[exercise.reference_sql[0]].total_rows
            assert wrong_rows > right_rows, exercise_id
        if exercise_id != "join-multi-receipt-qty":
            continue
        unsafe_result = _run(sql)
        reference = reference_results[exercise.reference_sql[0]]
        unsafe_qty = {(row[0], row[1]): row[2] for row in unsafe_result.rows}
        reference_qty = {(row[0], row[1]): row[2] for row in reference.rows}
        assert unsafe_qty[("PO-0000003", 1)] == 75
        assert reference_qty[("PO-0000003", 1)] == 25


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


@pytest.fixture
def planning_scenario():
    """A safety-stock breach without demand and twelve completely idle capacity weeks."""
    with duckdb.connect(":memory:") as connection:
        create_all(connection)
        connection.execute("""
            INSERT INTO plant VALUES ('SC01', 'Test plant', 'Santa Clara', 'US');
            INSERT INTO product_family VALUES ('ETC', 'Etch');
            INSERT INTO material VALUES
                ('LL-NO-DEMAND', 'Long lead spare', 'COMPONENT', NULL, 'EA', true,
                 DATE '2021-11-01'),
                ('LL-COVERED', 'Covered long lead', 'COMPONENT', NULL, 'EA', true,
                 DATE '2021-11-01'),
                ('SHORT-LEAD', 'Short lead spare', 'COMPONENT', NULL, 'EA', false,
                 DATE '2021-11-01'),
                ('TOOL1', 'Test tool', 'TOOL', 'ETC', 'EA', false,
                 DATE '2021-11-01');
            INSERT INTO material_plant VALUES
                ('LL-NO-DEMAND', 'SC01', 'PD', 140, 10),
                ('LL-COVERED', 'SC01', 'PD', 140, 10),
                ('SHORT-LEAD', 'SC01', 'PD', 7, 10);
            INSERT INTO inventory VALUES
                ('LL-NO-DEMAND', 'SC01', 'RM01', 3),
                ('LL-COVERED', 'SC01', 'RM01', 12),
                ('SHORT-LEAD', 'SC01', 'RM01', 0);
            INSERT INTO build_bay VALUES ('TEST-B1', 'SC01', 'ETC', 2);
            INSERT INTO production_order
                (prod_order_no, material_no, plant_code, bay_id, qty,
                 planned_start, planned_finish, status)
            VALUES ('P1', 'TOOL1', 'SC01', 'TEST-B1', 1,
                    DATE '2026-12-01', DATE '2027-01-01', 'Planned');
        """)
        yield connection


def _scenario_runner(connection):
    def run(sql, **_kwargs):
        cursor = connection.execute(sql)
        rows = cursor.fetchall()
        return QueryResult(
            columns=[item[0] for item in cursor.description],
            column_types=[str(item[1]) for item in cursor.description],
            rows=rows,
            total_rows=len(rows),
            truncated=False,
            elapsed_ms=0,
        )

    return run


@pytest.mark.parametrize(
    ("exercise_id", "answer"),
    [
        (
            "plan-shortages",
            "SELECT 'LL-NO-DEMAND' AS material_no, 'SC01' AS plant_code, "
            "DATE '2026-11-01' AS first_shortage_week",
        ),
        (
            "drill-bay-bottleneck",
            "SELECT 'TEST-B1' AS bay_id, 1 AS total_starts, 26 AS total_slots, "
            "0 AS excess_starts, 3.85 AS load_pct",
        ),
    ],
)
def test_planning_answers_through_check_route(
    planning_scenario, tmp_path, monkeypatch, exercise_id, answer
) -> None:
    monkeypatch.setenv("SCLAB_HOME", str(tmp_path))
    monkeypatch.setattr("sclab.engine.query.run_query", _scenario_runner(planning_scenario))
    with TestClient(create_app(), base_url="http://127.0.0.1") as client:
        response = client.post(
            f"/api/check/{exercise_id}", data={"sql": answer}, headers={"HX-Request": "true"}
        )
    assert response.status_code == 200
    assert "Result matches." in response.text


@pytest.mark.parametrize("has_starts", [True, False])
def test_capacity_keeps_completely_idle_weeks(planning_scenario, has_starts) -> None:
    if not has_starts:
        planning_scenario.execute("DELETE FROM production_order")
    exercises = {item.id: item for item in _exercises()}
    run = _scenario_runner(planning_scenario)
    weekly = run(exercises["plan-bay-load"].reference_sql[0]).rows
    assert [row[1] for row in weekly] == [
        date(2026, 11, 30) + timedelta(days=7 * n) for n in range(13)
    ]
    assert sum(row[2] for row in weekly) == int(has_starts)
    assert all(row[3] == 2 for row in weekly)
    assert sum(row[4] == 0 for row in weekly) == 13 - int(has_starts)
    drill = run(exercises["drill-bay-bottleneck"].reference_sql[0]).rows
    assert drill == [("TEST-B1", int(has_starts), 26, 0, 3.85 if has_starts else 0.0)]


def test_calendar_guard_allows_only_bounded_week_sequence() -> None:
    calendar = (
        "WITH RECURSIVE weeks(level) AS (SELECT 0 UNION ALL "
        "SELECT level + 1 FROM weeks WHERE level < 12) SELECT level FROM weeks"
    )
    assert _recursive_guard_problem(calendar) is None
    assert _recursive_guard_problem(calendar.replace("< 12", "< 25")) is None
    assert _recursive_guard_problem(calendar.replace("< 12", "< 26")) is not None
    assert _recursive_guard_problem(calendar.replace("+ 1", "- 1")) is not None
    assert _recursive_guard_problem(calendar.replace("SELECT 0", "SELECT -100")) is not None
    assert _recursive_guard_problem(calendar.replace("< 12", "< 12 OR true")) is not None
    assert _recursive_guard_problem(calendar.replace("SELECT 0", "SELECT 0, 1")) is not None
    assert _recursive_guard_problem(calendar.replace(" WHERE level < 12", "")) is not None
    joined = calendar.replace("FROM weeks WHERE", "FROM weeks JOIN bom_item ON true WHERE")
    assert _recursive_guard_problem(joined) is not None


_IDLE_WEEK_WRONG_ANSWERS = (
    (
        "plan-bay-load",
        """
WITH weeks AS (
    SELECT DISTINCT CAST(date_trunc('week', planned_start) AS DATE) AS week_start
    FROM production_order
    WHERE planned_start >= DATE '2026-12-01' AND planned_start < DATE '2027-03-01'
), loads AS (
    SELECT bay_id, CAST(date_trunc('week', planned_start) AS DATE) AS week_start,
           COUNT(*) AS starts
    FROM production_order
    WHERE planned_start >= DATE '2026-12-01' AND planned_start < DATE '2027-03-01'
    GROUP BY bay_id, CAST(date_trunc('week', planned_start) AS DATE)
), grid AS (
    SELECT b.bay_id, w.week_start, COALESCE(l.starts, 0) AS starts, b.weekly_slots
    FROM build_bay b CROSS JOIN weeks w
    LEFT JOIN loads l ON l.bay_id = b.bay_id AND l.week_start = w.week_start
)
SELECT bay_id, week_start, starts, weekly_slots,
       ROUND(100.0 * starts / weekly_slots, 2) AS load_pct,
       starts > weekly_slots AS overloaded
FROM grid ORDER BY bay_id, week_start

        """,
    ),
    (
        "drill-bay-bottleneck",
        """
WITH weeks AS (
    SELECT DISTINCT CAST(date_trunc('week', planned_start) AS DATE) AS week_start
    FROM production_order
    WHERE planned_start >= DATE '2026-12-01' AND planned_start < DATE '2027-03-01'
), loads AS (
    SELECT bay_id, CAST(date_trunc('week', planned_start) AS DATE) AS week_start,
           COUNT(*) AS starts
    FROM production_order
    WHERE planned_start >= DATE '2026-12-01' AND planned_start < DATE '2027-03-01'
    GROUP BY bay_id, CAST(date_trunc('week', planned_start) AS DATE)
), grid AS (
    SELECT b.bay_id, w.week_start, COALESCE(l.starts, 0) AS starts, b.weekly_slots
    FROM build_bay b CROSS JOIN weeks w
    LEFT JOIN loads l ON l.bay_id = b.bay_id AND l.week_start = w.week_start
)
SELECT bay_id, SUM(starts) AS total_starts, SUM(weekly_slots) AS total_slots,
       SUM(CASE WHEN starts > weekly_slots THEN starts - weekly_slots ELSE 0 END) AS excess_starts,
       ROUND(100.0 * SUM(starts) / SUM(weekly_slots), 2) AS load_pct
FROM grid GROUP BY bay_id ORDER BY load_pct DESC, bay_id

        """,
    ),
)


@pytest.mark.parametrize(("exercise_id", "sql"), _IDLE_WEEK_WRONG_ANSWERS)
def test_production_derived_calendar_wrong_answers_fail(planning_scenario, exercise_id, sql):
    exercise = next(item for item in _exercises() if item.id == exercise_id)
    outcome = check_answer(exercise, sql, runner=_scenario_runner(planning_scenario))
    assert outcome.passed is False
