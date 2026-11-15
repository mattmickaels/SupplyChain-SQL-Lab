import itertools
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time
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
    columns: list[str] = field(default_factory=list)
    column_types: list[str] = field(default_factory=list)


def normalize_value(v: object) -> object:
    if v is None or isinstance(v, bool | str):
        return v
    if isinstance(v, int | float | Decimal):
        return round(float(v), 6)
    if isinstance(v, datetime):
        if v.time() == time.min:
            return v.date().isoformat()
        return v.isoformat()
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
    if Counter(actual_keys) != Counter(expected_keys):
        return None
    indexes_by_name = defaultdict(list)
    for index, name in reversed(list(enumerate(actual_keys))):
        indexes_by_name[name].append(index)
    return [indexes_by_name[name].pop() for name in expected_keys]


def _value_mappings(actual: QueryResult, expected: QueryResult, names_matter: bool):
    width = len(expected.columns)
    actual_sigs = [
        (_column_signature(actual.rows, index), name.casefold() if names_matter else None)
        for index, name in enumerate(actual.columns)
    ]
    expected_sigs = [
        (_column_signature(expected.rows, index), name.casefold() if names_matter else None)
        for index, name in enumerate(expected.columns)
    ]
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
    mapping = [-1] * width

    def visit(group: int):
        if group == len(pools):
            yield mapping.copy()
            return
        expected_indexes = targets[group]
        for actual_indexes in itertools.permutations(pools[group]):
            for expected_index, actual_index in zip(expected_indexes, actual_indexes, strict=True):
                mapping[expected_index] = actual_index
            yield from visit(group + 1)

    yield from visit(0)


def _project(rows: list[tuple], mapping: list[int]) -> list[tuple]:
    return [tuple(row[index] for index in mapping) for row in rows]


def _is_number(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int | float | Decimal)


def _values_match(left: object, right: object) -> bool:
    if _is_number(left) and _is_number(right):
        return math.isclose(float(left), float(right), abs_tol=1e-6)
    return normalize_value(left) == normalize_value(right)


def _rows_match(left: tuple, right: tuple) -> bool:
    return len(left) == len(right) and all(
        _values_match(left_value, right_value)
        for left_value, right_value in zip(left, right, strict=True)
    )


def _sort_key(row: tuple) -> tuple:
    parts: list[tuple] = []
    for value in row:
        if _is_number(value):
            parts.append((0, float(value)))
            continue
        normalized = normalize_value(value)
        parts.append((1, "" if normalized is None else str(normalized)))
    return tuple(parts)


def _cmp_row(left: tuple, right: tuple) -> int:
    width = min(len(left), len(right))
    for index in range(width):
        if _values_match(left[index], right[index]):
            continue
        return -1 if _sort_key((left[index],)) < _sort_key((right[index],)) else 1
    if len(left) == len(right):
        return 0
    return -1 if len(left) < len(right) else 1


def _matched_flags(rows: list[tuple], other: list[tuple]) -> list[bool]:
    buckets: dict[tuple, list[int]] = defaultdict(list)
    for index, row in enumerate(other):
        buckets[_row_key(row)].append(index)
    pending: list[int] = []
    flags = [False] * len(rows)
    for index, row in enumerate(rows):
        found = buckets.get(_row_key(row))
        if found:
            found.pop()
            flags[index] = True
        else:
            pending.append(index)
    if not pending:
        return flags
    remaining = [other[index] for indexes in buckets.values() for index in indexes]
    if not remaining:
        return flags
    if len(pending) * len(remaining) > 20_000:
        left = sorted(pending, key=lambda index: _sort_key(rows[index]))
        right = sorted(range(len(remaining)), key=lambda index: _sort_key(remaining[index]))
        left_index = 0
        right_index = 0
        while left_index < len(left) and right_index < len(right):
            compared = _cmp_row(rows[left[left_index]], remaining[right[right_index]])
            if compared == 0:
                flags[left[left_index]] = True
                left_index += 1
                right_index += 1
            elif compared < 0:
                left_index += 1
            else:
                right_index += 1
        return flags
    used = [False] * len(remaining)
    for index in pending:
        for candidate_index, candidate in enumerate(remaining):
            if used[candidate_index]:
                continue
            if _rows_match(rows[index], candidate):
                used[candidate_index] = True
                flags[index] = True
                break
    return flags


def _rows_equal(actual: list[tuple], expected: list[tuple], order_matters: bool) -> bool:
    if len(actual) != len(expected):
        return False
    if order_matters:
        return all(_rows_match(left, right) for left, right in zip(actual, expected, strict=True))
    if Counter(_row_key(row) for row in actual) == Counter(_row_key(row) for row in expected):
        return True
    return all(_matched_flags(actual, expected))


def _aligned_rows(
    actual: QueryResult,
    expected: QueryResult,
    order_matters: bool,
    columns_in_order: bool,
    names_matter: bool,
) -> list[tuple]:
    if columns_in_order:
        return list(actual.rows)

    def candidates():
        if not names_matter or [name.casefold() for name in actual.columns] == [
            name.casefold() for name in expected.columns
        ]:
            yield list(range(len(expected.columns)))
        mapping = _align_by_name(actual.columns, expected.columns)
        if mapping is not None:
            yield mapping
        yield from _value_mappings(actual, expected, names_matter)

    # One budget includes identity, name alignment, and lazy value alignments.
    first = None
    for candidate in itertools.islice(candidates(), _MAX_COLUMN_PERMUTATIONS):
        projected = _project(actual.rows, candidate)
        if first is None:
            first = projected
        if _rows_equal(projected, expected.rows, order_matters):
            return projected
    return first if names_matter and first is not None else list(actual.rows)


def _unmatched(rows: list[tuple], other: list[tuple]) -> tuple[list[tuple], int]:
    flags = _matched_flags(rows, other)
    examples: list[tuple] = []
    total = 0
    for row, matched in zip(rows, flags, strict=True):
        if matched:
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
    actual_names = [name.casefold() for name in actual.columns]
    expected_names = [name.casefold() for name in expected.columns]
    names_match = (
        actual_names == expected_names
        if columns_in_order
        else Counter(actual_names) == Counter(expected_names)
    )
    if names_matter and not names_match:
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

    aligned_rows = _aligned_rows(actual, expected, order_matters, columns_in_order, names_matter)
    missing_rows, missing_total = _unmatched(expected.rows, aligned_rows)
    extra_rows, extra_total = _unmatched(aligned_rows, expected.rows)
    order_wrong = order_matters and (
        len(aligned_rows) != len(expected.rows)
        or any(
            not _rows_match(actual_row, expected_row)
            for actual_row, expected_row in zip(aligned_rows, expected.rows, strict=False)
        )
    )
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
