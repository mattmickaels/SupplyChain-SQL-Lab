"""Run one SQL statement against the lesson database or the sandbox."""

import shutil
import threading
import time
from pathlib import Path
from typing import Literal

import duckdb

from sclab.engine.types import QueryError, QueryResult
from sclab.paths import lab_db_path, sandbox_db_path

MAX_ROWS = 1000
TIMEOUT_S = 15.0
_TIMEOUT_MESSAGE = "Query stopped after 15 seconds. Check your JOIN conditions."
_FETCH_BATCH = 1000
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
) -> QueryResult:
    """Run one statement and return at most max_rows rows."""
    statements = _statements(sql)
    if target not in ("lesson", "sandbox"):
        raise QueryError("Choose the lesson database or the practice database.")
    started = time.perf_counter()
    try:
        connection = _connect(target)
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
    timer.start()
    try:
        columns, rows, total_rows, truncated = _collect(connection, statements[0], max_rows)
        return QueryResult(
            columns=columns,
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


def _connect(target: str):
    if target == "lesson":
        return duckdb.connect(str(lab_db_path()), read_only=True, config=_SAFETY)
    return duckdb.connect(str(sandbox_db_path()), read_only=False, config=_SAFETY)


def _collect(connection, statement, max_rows: int):
    pending = connection.execute(statement)
    columns = [column[0] for column in pending.description] if pending.description else []
    rows: list[tuple] = []
    remaining = max_rows
    while remaining > 0:
        batch = pending.fetchmany(min(_FETCH_BATCH, remaining))
        if not batch:
            break
        rows.extend(tuple(row) for row in batch)
        remaining -= len(batch)
    truncated = remaining == 0 and bool(pending.fetchmany(1))
    total_rows = len(rows)
    if truncated and statement.type == duckdb.StatementType.SELECT:
        # execute() returns the same connection. A second query replaces the
        # open result, so the count runs without reading the rest of the rows.
        counted = connection.execute(_count_sql(statement.query)).fetchone()
        total_rows = int(counted[0])
    return columns, rows, total_rows, truncated


def _count_sql(sql: str) -> str:
    return f"SELECT count(*) FROM ({_statement_body(sql)})"


def _statement_body(sql: str) -> str:
    text = sql.strip()
    while True:
        previous = text
        text = _without_trailing_line_comment(text).rstrip()
        while text.endswith(";"):
            text = text[:-1].rstrip()
        if text == previous:
            return text


def _without_trailing_line_comment(sql: str) -> str:
    in_single = False
    in_double = False
    index = 0
    while index < len(sql):
        char = sql[index]
        if char == "'" and not in_double:
            if in_single and index + 1 < len(sql) and sql[index + 1] == "'":
                index += 2
                continue
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double and sql.startswith("--", index):
            if "\n" not in sql[index:]:
                return sql[:index]
            index = sql.find("\n", index) + 1
            continue
        index += 1
    return sql


def _remove_wal(path: Path) -> None:
    wal = Path(str(path) + ".wal")
    if wal.exists():
        wal.unlink()
