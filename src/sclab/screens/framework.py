"""Screen definitions, parameter handling and the display SQL for "Show the SQL".

Parameter convention: `$1` is the key typed on the screen.
`$2`, `$3` and so on bind the names a screen declares in `Screen.params`, in declared order.
Query-string values that a screen does not declare are ignored.
Every query is bound by DuckDB; `inline_params` only builds text for the learner to read.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from math import isinf, isnan

import duckdb

from sclab.engine.query import QueryError, run_query
from sclab.engine.types import QueryResult

_DIGITS = re.compile(r"\d+")


@dataclass(frozen=True)
class ScreenQuery:
    name: str
    title: str
    sql: str


@dataclass(frozen=True)
class Screen:
    code: str
    title: str
    key_label: str
    queries: tuple[ScreenQuery, ...]
    exists_sql: str
    not_found: str
    params: tuple[str, ...] = ()


@dataclass
class QueryRun:
    query: ScreenQuery
    result: QueryResult | None
    error: str | None
    display_sql: str


@dataclass
class ScreenData:
    screen: Screen
    key: str
    found: bool
    message: str = ""
    runs: dict[str, QueryRun] = field(default_factory=dict)


_SCREENS: dict[str, Screen] = {}


def register(screen: Screen) -> None:
    _SCREENS[screen.code.upper()] = screen


def get_screen(code: str) -> Screen:
    try:
        return _SCREENS[code.strip().upper()]
    except KeyError:
        raise KeyError(f"No screen with transaction code {code}") from None


def all_screens() -> list[Screen]:
    return sorted(_SCREENS.values(), key=lambda screen: screen.code)


def _literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, datetime):
        return f"TIMESTAMP '{value.isoformat(sep=' ')}'"
    if isinstance(value, date):
        return f"DATE '{value.isoformat()}'"
    if isinstance(value, float) and (isnan(value) or isinf(value)):
        text = "NaN" if isnan(value) else ("Infinity" if value > 0 else "-Infinity")
        return f"CAST('{text}' AS DOUBLE)"
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _placeholders(sql: str) -> list[tuple[int, int, int]]:
    """Find `$n` parameter tokens as (start, end, n), skipping strings, comments and identifiers."""
    tokens = duckdb.tokenize(sql)
    found: list[tuple[int, int, int]] = []
    for position, (offset, kind) in enumerate(tokens[:-1]):
        if kind != duckdb.token_type.operator or sql[offset : offset + 1] != "$":
            continue
        following, following_kind = tokens[position + 1]
        digits = _DIGITS.match(sql, following)
        if (
            following != offset + 1
            or following_kind != duckdb.token_type.numeric_const
            or not digits
        ):
            continue
        found.append((offset, digits.end(), int(digits.group())))
    return found


def inline_params(sql: str, params: Sequence[object]) -> str:
    """Replace `$n` with SQL literals. For display only; never run the result with user text."""
    pieces: list[str] = []
    cursor = 0
    for start, end, index in _placeholders(sql):
        if index < 1 or index > len(params):
            raise ValueError(f"No value for parameter ${index}")
        pieces.append(sql[cursor:start])
        pieces.append(_literal(params[index - 1]))
        cursor = end
    pieces.append(sql[cursor:])
    return "".join(pieces)


def _params_for(sql: str, values: Sequence[object]) -> list[object]:
    needed = max((index for _, _, index in _placeholders(sql)), default=0)
    padded = list(values) + [None] * max(0, needed - len(values))
    return padded[:needed]


def _run(query: ScreenQuery, values: Sequence[object]) -> QueryRun:
    params = _params_for(query.sql, values)
    display = inline_params(query.sql, params)
    try:
        return QueryRun(query, run_query(query.sql, params=params), None, display)
    except QueryError as exc:
        return QueryRun(query, None, exc.message, display)


def render_screen(code: str, key: str, extra: dict[str, str]) -> ScreenData:
    screen = get_screen(code)
    key = key.strip()
    if not key:
        return ScreenData(screen, key, False, f"Enter a {screen.key_label.lower()}.")
    values: list[object] = [key]
    for name in screen.params:
        value = extra.get(name, "").strip()
        if not value:
            return ScreenData(screen, key, False, f"Enter a {name.replace('_', ' ')}.")
        values.append(value)
    exists = _run(ScreenQuery("exists", "exists", screen.exists_sql), values)
    if exists.error:
        return ScreenData(screen, key, False, exists.error)
    rows = exists.result.rows if exists.result else []
    if not rows or rows[0][0] in (0, False, None):
        return ScreenData(screen, key, False, screen.not_found.format(key=key))
    runs = {query.name: _run(query, values) for query in screen.queries}
    return ScreenData(screen, key, True, "", runs)
