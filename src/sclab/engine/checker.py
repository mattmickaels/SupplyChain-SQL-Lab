import itertools
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sclab.engine.types import QueryResult

_EXAMPLE_LIMIT = 5
_MAX_COLUMN_PERMUTATIONS = 5040


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


def _value_sort_key(value: object) -> tuple[str, str]:
    return (type(value).__name__, repr(value))


def _column_signature(rows: list[tuple], index: int) -> tuple:
    counts = Counter(normalize_value(row[index]) for row in rows)
    return tuple(sorted(counts.items(), key=lambda item: _value_sort_key(item[0])))


def _align_by_name(actual: list[str], expected: list[str]) -> list[int] | None:
    actual_keys = [name.casefold() for name in actual]
    expected_keys = [name.casefold() for name in expected]
    if set(actual_keys) != set(expected_keys) or len(set(actual_keys)) != len(actual_keys):
        return None
    index_by_name = {name: index for index, name in enumerate(actual_keys)}
    return [index_by_name[name] for name in expected_keys]


def _value_mappings(actual_rows: list[tuple], expected_rows: list[tuple], width: int):
    actual_sigs = [_column_signature(actual_rows, index) for index in range(width)]
    expected_sigs = [_column_signature(expected_rows, index) for index in range(width)]
    if Counter(actual_sigs) != Counter(expected_sigs):
        return
    actual_groups: dict[tuple, list[int]] = defaultdict(list)
    expected_groups: dict[tuple, list[int]] = defaultdict(list)
    for index, signature in enumerate(actual_sigs):
        actual_groups[signature].append(index)
    ordered_sigs: list[tuple] = []
    seen: set[tuple] = set()
    for signature in expected_sigs:
        if signature not in seen:
            seen.add(signature)
            ordered_sigs.append(signature)
            for index, candidate in enumerate(expected_sigs):
                if candidate == signature:
                    expected_groups[signature].append(index)
    pools = [actual_groups[signature] for signature in ordered_sigs]
    targets = [expected_groups[signature] for signature in ordered_sigs]
    tries = 0
    permutations = (itertools.permutations(pool) for pool in pools)
    for choice in itertools.product(*permutations):
        if tries >= _MAX_COLUMN_PERMUTATIONS:
            return
        tries += 1
        mapping = [-1] * width
        for expected_indexes, actual_indexes in zip(targets, choice, strict=True):
            for expected_index, actual_index in zip(expected_indexes, actual_indexes, strict=True):
                mapping[expected_index] = actual_index
        yield mapping


def _project(rows: list[tuple], mapping: list[int]) -> list[tuple]:
    return [tuple(row[index] for index in mapping) for row in rows]


def _rows_equal(actual: list[tuple], expected: list[tuple], order_matters: bool) -> bool:
    actual_keys = [_row_key(row) for row in actual]
    expected_keys = [_row_key(row) for row in expected]
    if order_matters:
        return actual_keys == expected_keys
    return Counter(actual_keys) == Counter(expected_keys)


def _aligned_rows(
    actual: QueryResult,
    expected: QueryResult,
    order_matters: bool,
    columns_in_order: bool,
) -> list[tuple]:
    if columns_in_order:
        return list(actual.rows)
    mapping = _align_by_name(actual.columns, expected.columns)
    if mapping is not None:
        return _project(actual.rows, mapping)
    matched: list[tuple] | None = None
    first: list[tuple] | None = None
    for candidate in _value_mappings(actual.rows, expected.rows, len(expected.columns)):
        projected = _project(actual.rows, candidate)
        if first is None:
            first = projected
        if _rows_equal(projected, expected.rows, order_matters):
            matched = projected
            break
    if matched is not None:
        return matched
    if first is not None:
        return first
    return list(actual.rows)


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
    columns_in_order: bool = False,
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

    aligned_rows = _aligned_rows(actual, expected, order_matters, columns_in_order)
    missing_rows, missing_total = _unmatched(expected.rows, aligned_rows)
    extra_rows, extra_total = _unmatched(aligned_rows, expected.rows)
    order_wrong = order_matters and [_row_key(row) for row in aligned_rows] != [
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
