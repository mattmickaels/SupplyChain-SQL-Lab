"""One set of type rules for the result grid and the Excel workbook."""

from datetime import date, datetime, time
from decimal import Decimal

_DATE = frozenset({"DATE"})
_DATETIME = frozenset(
    {
        "TIMESTAMP",
        "TIMESTAMP_S",
        "TIMESTAMP_MS",
        "TIMESTAMP_NS",
        "TIMESTAMP WITH TIME ZONE",
        "TIMESTAMP_TZ",
        "DATETIME",
    }
)
_BOOLEAN = frozenset({"BOOLEAN", "BOOL"})
_NUMERIC = frozenset(
    {
        "TINYINT",
        "SMALLINT",
        "INTEGER",
        "BIGINT",
        "HUGEINT",
        "UTINYINT",
        "USMALLINT",
        "UINTEGER",
        "UBIGINT",
        "UHUGEINT",
        "FLOAT",
        "DOUBLE",
        "REAL",
        "DECIMAL",
        "NUMERIC",
    }
)


def type_head(type_name: object) -> str:
    """Return the DuckDB type name without precision or quotes."""
    if not isinstance(type_name, str):
        return ""
    return type_name.strip().strip('"').upper().split("(", 1)[0].strip()


def is_numeric_type(type_name: object) -> bool:
    """True when the column should line up as a number in the grid."""
    return type_head(type_name) in _NUMERIC


def cell_kind(type_name: object, value: object) -> str:
    """Return empty, date, datetime, number, boolean, or text.

    Known column types win. Other types fall back to Python temporal values.
    """
    if value is None:
        return "empty"
    head = type_head(type_name)
    if head in _DATE:
        return "date"
    if head in _DATETIME:
        if isinstance(value, datetime) and _has_time(value):
            return "datetime"
        return "date"
    if head in _BOOLEAN:
        return "boolean"
    if head in _NUMERIC:
        return "number"
    if head in {"VARCHAR", "CHAR", "TEXT"}:
        return "text"
    if isinstance(value, datetime):
        return "datetime" if _has_time(value) else "date"
    if isinstance(value, date):
        return "date"
    if head:
        return "text"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, Decimal | int | float):
        return "number"
    return "text"


def display_text(type_name: object, value: object) -> str | None:
    """Grid text for one cell. None means show an empty NULL marker."""
    kind = cell_kind(type_name, value)
    if kind == "empty":
        return None
    if kind == "boolean":
        return "true" if value else "false"
    if kind == "date":
        stamped = _as_date(value)
        return stamped.isoformat() if stamped is not None else str(value)
    if kind == "datetime":
        stamped = _as_datetime(value)
        return stamped.isoformat() if stamped is not None else str(value)
    return str(value)


def _has_time(value: datetime) -> bool:
    naive = value.replace(tzinfo=None) if value.tzinfo is not None else value
    return naive.time() != time.min


def _as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _as_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is not None else value
    return None
