from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sclab.engine.types import QueryResult

_EXAMPLE_LIMIT = 5


@dataclass
class CheckOutcome:
    passed: bool
    summary: str
    missing_rows: list[tuple]
    extra_rows: list[tuple]
    expected_count: int
    actual_count: int


def normalize_value(v: object) -> object:
    if v is None or isinstance(v, bool | str):
        return v
    if isinstance(v, int | float | Decimal):
        return round(float(v), 6)
    if isinstance(v, date):
        return v.isoformat()
    return v


def _row_key(row: tuple) -> tuple:
    return tuple(normalize_value(value) for value in row)


def _unmatched(rows: list[tuple], other: list[tuple]) -> tuple[list[tuple], int]:
    remaining: Counter[tuple] = Counter(_row_key(row) for row in other)
    examples: list[tuple] = []
    total = 0
    for row in rows:
        key = _row_key(row)
        if remaining[key]:
            remaining[key] -= 1
            continue
        total += 1
        if len(examples) < _EXAMPLE_LIMIT:
            examples.append(row)
    return examples, total


def _difference_summary(missing_total: int, extra_total: int) -> str:
    parts: list[str] = []
    if missing_total:
        noun = "row" if missing_total == 1 else "rows"
        parts.append(f"{missing_total} missing {noun}")
    if extra_total:
        noun = "row" if extra_total == 1 else "rows"
        parts.append(f"{extra_total} extra {noun}")
    if len(parts) == 1:
        text = parts[0]
    else:
        text = f"{parts[0]} and {parts[1]}"
    return text[0].upper() + text[1:] + "."


def compare(
    actual: QueryResult,
    expected: QueryResult,
    order_matters: bool = False,
    names_matter: bool = False,
) -> CheckOutcome:
    expected_count = len(expected.rows)
    actual_count = len(actual.rows)
    if len(actual.columns) != len(expected.columns):
        return CheckOutcome(
            passed=False,
            summary=f"Expected {len(expected.columns)} columns, got {len(actual.columns)}.",
            missing_rows=[],
            extra_rows=[],
            expected_count=expected_count,
            actual_count=actual_count,
        )
    if names_matter and [name.casefold() for name in actual.columns] != [
        name.casefold() for name in expected.columns
    ]:
        expected_names = ", ".join(expected.columns)
        actual_names = ", ".join(actual.columns)
        return CheckOutcome(
            passed=False,
            summary=f"Expected columns {expected_names}, got {actual_names}.",
            missing_rows=[],
            extra_rows=[],
            expected_count=expected_count,
            actual_count=actual_count,
        )

    missing_rows, missing_total = _unmatched(expected.rows, actual.rows)
    extra_rows, extra_total = _unmatched(actual.rows, expected.rows)
    order_wrong = order_matters and [_row_key(row) for row in actual.rows] != [
        _row_key(row) for row in expected.rows
    ]
    passed = missing_total == 0 and extra_total == 0 and not order_wrong
    if passed:
        summary = "Result matches."
    elif order_wrong and missing_total == 0 and extra_total == 0:
        summary = "Rows are in the wrong order."
    else:
        summary = _difference_summary(missing_total, extra_total)
    return CheckOutcome(
        passed=passed,
        summary=summary,
        missing_rows=missing_rows,
        extra_rows=extra_rows,
        expected_count=expected_count,
        actual_count=actual_count,
    )
