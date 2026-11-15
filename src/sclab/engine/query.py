"""Run one SQL statement against the lesson database or the sandbox."""

import shutil
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import duckdb

from sclab.engine.types import QueryError, QueryResult
from sclab.paths import lab_db_path, sandbox_db_path

MAX_ROWS = 1000
TIMEOUT_S = 15.0
_TIMEOUT_MESSAGE = "Query stopped after 15 seconds. Check your JOIN conditions."
_FETCH_BATCH = 10_000
_SAFETY = {
    "enable_external_access": False,
    "autoinstall_known_extensions": False,
    "autoload_known_extensions": False,
    "lock_configuration": True,
}

__all__ = [
    "MAX_ROWS",
    "TIMEOUT_S",
    "QueryError",
    "QueryResult",
    "reset_sandbox",
    "run_query",
]


def run_query(
    sql: str,
    target: Literal["lesson", "sandbox"] = "lesson",
    max_rows: int = MAX_ROWS,
    timeout_s: float = TIMEOUT_S,
    params: Sequence[object] | None = None,
    *,
    read_only: bool = False,
) -> QueryResult:
    """Run one statement, optionally forcing the sandbox to be read-only too.

    With params, DuckDB binds positional $1, $2 placeholders; learner paths never pass params.
    """
    _statements(sql)
    if target not in ("lesson", "sandbox"):
        raise QueryError("Choose the lesson database or the practice database.")
    started = time.perf_counter()
    try:
        connection = _connect(target, read_only=read_only)
    except duckdb.Error as exc:
        raise QueryError(str(exc)) from exc
    interrupted = False

    def on_timeout() -> None:
        nonlocal interrupted
        interrupted = True
        try:
            connection.interrupt()
        except Exception:
            return

    timer = threading.Timer(timeout_s, on_timeout)
    timer.daemon = True
    deadline = time.perf_counter() + timeout_s
    timer.start()
    try:
        columns, column_types, rows, total_rows, truncated = _collect(
            connection, sql, max_rows, deadline, params
        )
        if interrupted or time.perf_counter() >= deadline:
            raise QueryError(_TIMEOUT_MESSAGE)
        return QueryResult(
            columns=columns,
            column_types=column_types,
            rows=rows,
            total_rows=total_rows,
            truncated=truncated,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )
    except QueryError:
        raise
    except duckdb.Error as exc:
        if interrupted or isinstance(exc, duckdb.InterruptException):
            raise QueryError(_TIMEOUT_MESSAGE) from exc
        raise QueryError(str(exc)) from exc
    finally:
        timer.cancel()
        try:
            connection.close()
        except duckdb.Error:
            pass


def reset_sandbox() -> None:
    """Replace the sandbox database with a fresh copy of the lesson database."""
    lab = lab_db_path()
    sandbox = sandbox_db_path()
    sandbox.parent.mkdir(parents=True, exist_ok=True)
    _remove_wal(sandbox)
    shutil.copy2(lab, sandbox)
    _remove_wal(sandbox)


def _statements(sql: str) -> list:
    try:
        statements = duckdb.extract_statements(sql)
    except duckdb.Error as exc:
        raise QueryError(str(exc)) from exc
    if not statements:
        raise QueryError("Type a query first.")
    if len(statements) > 1:
        raise QueryError("Run one statement at a time.")
    return statements


def _connect(target: str, *, read_only: bool = False):
    if target == "lesson":
        return duckdb.connect(str(lab_db_path()), read_only=True, config=_SAFETY)
    return duckdb.connect(str(sandbox_db_path()), read_only=read_only, config=_SAFETY)


def _column_info(description) -> tuple[list[str], list[str]]:
    if not description:
        return [], []
    columns: list[str] = []
    column_types: list[str] = []
    for column in description:
        columns.append(str(column[0]))
        type_name = column[1] if len(column) > 1 and column[1] is not None else ""
        column_types.append(str(type_name))
    return columns, column_types


def _collect(
    connection, sql: str, max_rows: int, deadline: float, params: Sequence[object] | None = None
):
    pending = connection.execute(sql, list(params)) if params else connection.execute(sql)
    columns, column_types = _column_info(pending.description)
    rows: list[tuple] = []
    remaining = max_rows
    while remaining > 0:
        if time.perf_counter() >= deadline:
            raise QueryError(_TIMEOUT_MESSAGE)
        batch = pending.fetchmany(min(_FETCH_BATCH, remaining))
        if not batch:
            break
        rows.extend(tuple(row) for row in batch)
        remaining -= len(batch)
    total_rows = len(rows)
    while True:
        if time.perf_counter() >= deadline:
            raise QueryError(_TIMEOUT_MESSAGE)
        batch = pending.fetchmany(_FETCH_BATCH)
        if not batch:
            break
        total_rows += len(batch)
    return columns, column_types, rows, total_rows, total_rows > max_rows


def _remove_wal(path: Path) -> None:
    wal = Path(str(path) + ".wal")
    if wal.exists():
        wal.unlink()
