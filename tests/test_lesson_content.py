"""Reference queries stay portable, ordered, and pinned to a snapshot."""

import hashlib
import json
import re
import tomllib
from copy import deepcopy
from functools import cache
from pathlib import Path

import duckdb
import pytest

from sclab.engine.checker import compare, normalize_value
from sclab.engine.query import QueryError, run_query
from sclab.engine.types import QueryResult
from sclab.lessons.model import Exercise, check_answer, load_lessons

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
        right = node.get("right")
        where = right.get("where_clause") if isinstance(right, dict) else None
        if not _where_has_level_guard(where):
            return "recursive term needs WHERE level < an integer of at most 10"
    return None


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
        WHERE m.description = 'RF generator'
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
        WHERE m.description = 'RF generator'
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
        WHERE m.description = 'RF generator'
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
)


def test_common_wrong_answers_fail(lab_home, reference_results) -> None:
    assert lab_home is not None
    exercises = {item.id: item for item in _exercises()}
    for exercise_id, sql in _WRONG_ANSWERS:
        exercise = exercises[exercise_id]
        outcome = check_answer(exercise, sql, runner=_run)
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
