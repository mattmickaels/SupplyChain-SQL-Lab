from datetime import date, datetime
from decimal import Decimal

import pytest

from sclab.engine import checker
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
    assert compare(swapped, expected, names_matter=True).passed is True


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


def test_reordered_columns_pass_by_default() -> None:
    expected = result(["plant_code", "name"], [("P1", "North"), ("P2", "South")])
    actual = result(["name", "plant_code"], [("North", "P1"), ("South", "P2")])
    outcome = compare(actual, expected)
    assert outcome.passed is True
    assert outcome.summary == "Result matches."
    assert outcome.missing_rows == []
    assert outcome.extra_rows == []


def test_reordered_unnamed_columns_pass_by_values() -> None:
    expected = result(["plant_code", "name"], [("P1", "North"), ("P2", "South")])
    actual = result(["col_b", "col_a"], [("North", "P1"), ("South", "P2")])
    outcome = compare(actual, expected)
    assert outcome.passed is True
    assert outcome.summary == "Result matches."


def test_columns_in_order_required_fails_on_swap() -> None:
    expected = result(["plant_code", "name"], [("P1", "North")])
    actual = result(["name", "plant_code"], [("North", "P1")])
    outcome = compare(actual, expected, columns_in_order=True)
    assert outcome.passed is False
    assert outcome.missing_rows == [("P1", "North")]
    assert outcome.extra_rows == [("North", "P1")]
    assert outcome.summary == "1 missing row and 1 extra row."


def test_ambiguous_identical_columns_resolved() -> None:
    expected = result(
        ["left", "right", "tag"],
        [(1, 2, "X"), (2, 1, "Y")],
    )
    actual = result(
        ["c", "b", "a"],
        [("X", 2, 1), ("Y", 1, 2)],
    )
    outcome = compare(actual, expected)
    assert outcome.passed is True
    assert outcome.summary == "Result matches."
    positional = compare(actual, expected, columns_in_order=True)
    assert positional.passed is False


def test_swapped_aliases_with_values_in_place_pass() -> None:
    expected = result(["a", "b"], [(1, 2)])
    actual = result(["b", "a"], [(1, 2)])
    assert compare(actual, expected).passed is True


def test_names_matter_ignores_name_order_when_columns_not_in_order() -> None:
    expected = result(["a", "b"], [(1, 2)])
    actual = result(["B", "A"], [(2, 1)])
    assert compare(actual, expected, names_matter=True).passed is True
    assert compare(result(["B", "A"], [(1, 2)]), expected, names_matter=True).passed is False


def test_names_matter_reordered_columns_with_correct_values_pass() -> None:
    expected = result(["a", "b"], [(1, 2)])
    actual = result(["B", "A"], [(2, 1)])
    assert compare(actual, expected, names_matter=True).passed is True


def test_duplicate_names_counted() -> None:
    expected = result(["a", "a", "b"], [(1, 2, 3)])
    actual = result(["A", "B", "b"], [(1, 2, 3)])
    outcome = compare(actual, expected, names_matter=True)
    assert outcome.passed is False
    assert outcome.summary == "Expected columns a, a, b, got A, B, b."


def test_names_matter_value_search_preserves_name_associations() -> None:
    expected = result(["a", "b"], [(1, 2)])
    actual = result(["A", "B"], [(2, 1)])
    assert compare(actual, expected, names_matter=True).passed is False


def test_names_matter_duplicate_names_can_permute_within_name_group() -> None:
    expected = result(["a", "a", "b"], [(1, 2, "X"), (2, 1, "Y")])
    actual = result(["B", "A", "a"], [("X", 2, 1), ("Y", 1, 2)])
    assert compare(actual, expected, names_matter=True).passed is True


def test_names_matter_and_columns_in_order_fail_on_name_swap() -> None:
    expected = result(["a", "b"], [(1, 2)])
    actual = result(["B", "A"], [(1, 2)])
    outcome = compare(actual, expected, names_matter=True, columns_in_order=True)
    assert outcome.passed is False
    assert outcome.summary == "Expected columns a, b, got B, A."


@pytest.mark.parametrize("rows_match", [False, True])
def test_wide_ambiguous_result_respects_cap(monkeypatch, rows_match: bool) -> None:
    width = 12
    actual = result([f"a{i}" for i in range(width)], [(0,) * width, (1,) * width])
    expected_rows = actual.rows if rows_match else [(0, 1) * 6, (1, 0) * 6]
    expected = result([f"e{i}" for i in range(width)], expected_rows)
    original_permutations = checker.itertools.permutations
    original_rows_equal = checker._rows_equal
    generated = comparisons = 0

    def guarded_permutations(pool):
        nonlocal generated
        for permutation in original_permutations(pool):
            generated += 1
            # Stop an eager factorial allocation before it can exhaust memory.
            assert generated <= 5040
            yield permutation

    def counted_rows_equal(*args):
        nonlocal comparisons
        comparisons += 1
        assert comparisons <= 5040
        return original_rows_equal(*args)

    monkeypatch.setattr(checker.itertools, "permutations", guarded_permutations)
    monkeypatch.setattr(checker, "_rows_equal", counted_rows_equal)
    assert compare(actual, expected).passed is rows_match
    assert comparisons == (1 if rows_match else 5040)


def test_midnight_timestamp_equals_date() -> None:
    day = date(2026, 11, 1)
    midnight = datetime(2026, 11, 1, 0, 0, 0)
    assert normalize_value(midnight) == "2026-11-01"
    assert compare(result(["d"], [(midnight,)]), result(["d"], [(day,)])).passed is True
    assert compare(result(["d"], [(day,)]), result(["d"], [(midnight,)])).passed is True


def test_half_way_rounding_boundary() -> None:
    low = 1.0 + 4e-7
    high = 1.0 + 6e-7
    assert round(low, 6) != round(high, 6)
    assert compare(result(["n"], [(low,)]), result(["n"], [(high,)])).passed is True
    outcome = compare(result(["n"], [(low,), (low,)]), result(["n"], [(high,)]))
    assert outcome.passed is False
    assert outcome.extra_rows == [(low,)]
    assert outcome.missing_rows == []
    assert outcome.summary == "1 extra row."
