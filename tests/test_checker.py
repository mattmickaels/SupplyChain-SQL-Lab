from datetime import date, datetime
from decimal import Decimal

import pytest

from sclab.engine.checker import CheckOutcome, compare, normalize_value
from sclab.engine.types import QueryError, QueryResult


def result(columns: list[str], rows: list[tuple]) -> QueryResult:
    return QueryResult(
        columns=columns,
        rows=rows,
        total_rows=len(rows),
        truncated=False,
        elapsed_ms=1,
    )


def test_query_error_message() -> None:
    error = QueryError("Type a query first.")
    assert error.message == "Type a query first."
    assert str(error) == "Type a query first."


def test_same_rows_different_order_pass_when_order_not_required() -> None:
    expected = result(["plant_code", "qty"], [("P2", 2), ("P1", 1)])
    actual = result(["plant_code", "qty"], [("P1", 1), ("P2", 2)])
    outcome = compare(actual, expected)
    assert outcome == CheckOutcome(
        passed=True,
        summary="Result matches.",
        missing_rows=[],
        extra_rows=[],
        expected_count=2,
        actual_count=2,
    )


def test_order_required_fails_on_different_order() -> None:
    expected = result(["plant_code"], [("P1",), ("P2",)])
    actual = result(["plant_code"], [("P2",), ("P1",)])
    outcome = compare(actual, expected, order_matters=True)
    assert outcome.passed is False
    assert outcome.summary == "Rows are in the wrong order."
    assert outcome.missing_rows == []
    assert outcome.extra_rows == []
    assert outcome.expected_count == 2
    assert outcome.actual_count == 2
    assert compare(actual, actual, order_matters=True).passed is True


def test_column_names_ignored_by_default() -> None:
    expected = result(["plant_code"], [("P1",)])
    actual = result(["code"], [("P1",)])
    assert compare(actual, expected).passed is True


def test_column_names_checked_when_required() -> None:
    expected = result(["plant_code", "name"], [("P1", "North")])
    wrong = result(["code", "title"], [("P1", "North")])
    outcome = compare(wrong, expected, names_matter=True)
    assert outcome.passed is False
    assert outcome.summary == "Expected columns plant_code, name, got code, title."
    assert outcome.missing_rows == []
    assert outcome.extra_rows == []
    same_case = result(["Plant_Code", "NAME"], [("P1", "North")])
    assert compare(same_case, expected, names_matter=True).passed is True
    swapped = result(["name", "plant_code"], [("North", "P1")])
    assert compare(swapped, expected, names_matter=True).passed is False


def test_column_count_mismatch_message() -> None:
    expected = result(["a", "b", "c"], [(1, 2, 3)])
    actual = result(["a", "b"], [(1, 2)])
    outcome = compare(actual, expected)
    assert outcome.passed is False
    assert outcome.summary == "Expected 3 columns, got 2."
    assert outcome.missing_rows == []
    assert outcome.extra_rows == []
    assert outcome.expected_count == 1
    assert outcome.actual_count == 1


def test_decimal_float_int_equal() -> None:
    close_values = [Decimal("2.50"), 2.5, 2.5000001]
    for left in close_values:
        for right in close_values:
            outcome = compare(result(["n"], [(left,)]), result(["n"], [(right,)]))
            assert outcome.passed is True
    assert compare(result(["n"], [(2,)]), result(["n"], [(Decimal("2"),)])).passed is True
    assert compare(result(["n"], [(2,)]), result(["n"], [(2.0,)])).passed is True
    assert normalize_value(Decimal("2.50")) == 2.5
    assert normalize_value(2) == 2.0
    assert normalize_value(2.5000001) == 2.5


def test_null_matches_null_but_not_zero() -> None:
    assert compare(result(["n"], [(None,)]), result(["n"], [(None,)])).passed is True
    outcome = compare(result(["n"], [(0,)]), result(["n"], [(None,)]))
    assert outcome.passed is False
    assert outcome.missing_rows == [(None,)]
    assert outcome.extra_rows == [(0,)]
    assert compare(result(["n"], [(None,)]), result(["n"], [(0,)])).passed is False


def test_trailing_space_is_different() -> None:
    outcome = compare(result(["name"], [("abc ",)]), result(["name"], [("abc",)]))
    assert outcome.passed is False
    assert normalize_value("abc ") == "abc "


def test_duplicate_rows_counted() -> None:
    expected = result(["qty"], [(1,)])
    actual = result(["qty"], [(1,), (1,)])
    outcome = compare(actual, expected)
    assert outcome.passed is False
    assert outcome.extra_rows == [(1,)]
    assert outcome.missing_rows == []
    assert outcome.summary == "1 extra row."
    assert outcome.expected_count == 1
    assert outcome.actual_count == 2


def test_missing_and_extra_examples_capped_at_5() -> None:
    expected = result(["n"], [(i,) for i in range(8)])
    actual = result(["n"], [(i,) for i in range(100, 108)])
    outcome = compare(actual, expected)
    assert outcome.passed is False
    assert outcome.missing_rows == [(0,), (1,), (2,), (3,), (4,)]
    assert outcome.extra_rows == [(100,), (101,), (102,), (103,), (104,)]
    assert outcome.summary == "8 missing rows and 8 extra rows."
    assert outcome.expected_count == 8
    assert outcome.actual_count == 8


def test_dates_become_iso_strings() -> None:
    day = date(2026, 11, 1)
    moment = datetime(2026, 11, 1, 8, 30, 0)
    assert normalize_value(day) == "2026-11-01"
    assert normalize_value(moment) == "2026-11-01T08:30:00"
    assert compare(result(["d"], [("2026-11-01",)]), result(["d"], [(day,)])).passed is True
    assert (
        compare(result(["d"], [("2026-11-01T08:30:00",)]), result(["d"], [(moment,)])).passed
        is True
    )


def test_order_and_value_mismatch_reports_rows() -> None:
    expected = result(["n"], [(1,), (2,), (3,)])
    actual = result(["n"], [(3,), (9,)])
    outcome = compare(actual, expected, order_matters=True)
    assert outcome.passed is False
    assert outcome.missing_rows == [(1,), (2,)]
    assert outcome.extra_rows == [(9,)]
    assert outcome.summary == "2 missing rows and 1 extra row."


@pytest.mark.parametrize(
    ("value", "normalized"),
    [
        (None, None),
        (True, True),
        ("P1", "P1"),
    ],
)
def test_normalize_value_leaves_null_bool_and_text(value: object, normalized: object) -> None:
    got = normalize_value(value)
    assert type(got) is type(normalized)
    assert got == normalized
