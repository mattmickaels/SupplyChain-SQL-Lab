"""Safe query runner: one statement, read-only lesson database, row cap, timeout."""

import threading
import time

import duckdb
import pytest

from sclab.engine import query
from sclab.engine.query import MAX_ROWS, QueryError, reset_sandbox, run_query

_TIMEOUT_MESSAGE = "Query stopped after 15 seconds. Check your JOIN conditions."


@pytest.fixture(autouse=True)
def _use_lab_home(lab_home):
    return lab_home


def test_select_returns_columns_and_rows(lab_conn) -> None:
    expected = lab_conn.execute("SELECT plant_code FROM plant ORDER BY 1").fetchall()
    result = run_query("SELECT plant_code FROM plant ORDER BY 1")
    assert result.columns == ["plant_code"]
    assert result.rows == [tuple(row) for row in expected]
    assert len(result.rows) == 3
    assert result.total_rows == 3
    assert result.truncated is False
    assert isinstance(result.elapsed_ms, int)
    assert result.elapsed_ms >= 0


def test_trailing_semicolon_and_comment_run() -> None:
    result = run_query("SELECT 1;  -- done")
    assert result.rows == [(1,)]
    assert result.total_rows == 1
    assert result.truncated is False


@pytest.mark.parametrize("sql", ["", "   ", "-- note", ";"])
def test_empty_query_asks_for_a_query(sql: str) -> None:
    with pytest.raises(QueryError) as caught:
        run_query(sql)
    assert caught.value.message == "Type a query first."


def test_multiple_statements_refused() -> None:
    with pytest.raises(QueryError) as caught:
        run_query("SELECT 1; SELECT 2")
    assert caught.value.message == "Run one statement at a time."


def test_syntax_error_is_plain_query_error() -> None:
    with pytest.raises(QueryError) as caught:
        run_query("SELEC 1")
    assert "syntax error" in caught.value.message


def test_lesson_db_is_read_only() -> None:
    with pytest.raises(QueryError):
        run_query("DELETE FROM plant")
    remaining = run_query("SELECT count(*) FROM plant")
    assert remaining.rows[0][0] == 3


@pytest.mark.parametrize("target", ["lesson", "sandbox"])
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_csv('x.csv')",
        "COPY plant TO 'x.csv'",
        "ATTACH 'x.duckdb'",
        "INSTALL httpfs",
    ],
)
def test_external_access_blocked(target: str, sql: str) -> None:
    with pytest.raises(QueryError):
        run_query(sql, target=target)


@pytest.mark.parametrize("target", ["lesson", "sandbox"])
def test_configuration_locked(target: str) -> None:
    with pytest.raises(QueryError):
        run_query("SET enable_external_access = true", target=target)


@pytest.mark.parametrize("target", ["lesson", "sandbox"])
def test_external_access_setting_is_off(target: str) -> None:
    setting = run_query("SELECT current_setting('enable_external_access')", target=target)
    assert setting.rows[0][0] is False


def test_huge_result_truncated(lab_conn) -> None:
    total = lab_conn.execute("SELECT count(*) FROM stock_movement").fetchone()[0]
    assert total > 1_000
    result = run_query("SELECT * FROM stock_movement")
    assert len(result.rows) == 1_000
    assert result.truncated is True
    assert result.total_rows == total


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT range FROM range(1001); /* done */",
        "SELECT range FROM range(1001) /* -- text */",
        "SELECT range FROM range(1001) /* -- note */;",
        "SELECT range FROM range(1001) /* outer /* inner */ still outer */",
        "SELECT range FROM range(1001) -- done",
        "SELECT range, '-- ; --' AS label FROM range(1001)",
    ],
)
def test_capped_count_keeps_comments_and_literals(sql: str) -> None:
    result = run_query(sql)
    assert len(result.rows) == MAX_ROWS
    assert result.truncated is True
    assert result.total_rows == 1001


def test_engine_never_rewrites_sql() -> None:
    for helper in ("_count_sql", "_without_trailing_semicolon", "_trailing_line_comment"):
        assert not hasattr(query, helper)


def test_capped_result_executes_original_sql_once(monkeypatch) -> None:
    sql = "SELECT range FROM range(25001) /* -- note */;"
    executed = []
    connection = duckdb.connect(":memory:")

    class Connection:
        def execute(self, text):
            executed.append(text)
            return connection.execute(text)

        def interrupt(self):
            connection.interrupt()

        def close(self):
            connection.close()

    monkeypatch.setattr(query, "_connect", lambda target: Connection())
    result = run_query(sql)
    assert executed == [sql]
    assert result.rows == [(number,) for number in range(MAX_ROWS)]
    assert result.total_rows == 25001
    assert result.truncated is True


def test_timeout_covers_counting_buffered_rows(monkeypatch) -> None:
    interrupted = threading.Event()

    class BufferedConnection:
        description = [("range",)]
        fetches = 0

        def execute(self, sql):
            return self

        def fetchmany(self, size):
            self.fetches += 1
            if self.fetches == 1:
                return [(0,)]
            assert interrupted.wait(2), "The timeout did not interrupt the connection."
            return [(1,)]

        def interrupt(self):
            interrupted.set()

        def close(self):
            pass

    connection = BufferedConnection()
    monkeypatch.setattr(query, "_connect", lambda target: connection)
    started = time.perf_counter()
    with pytest.raises(QueryError, match=_TIMEOUT_MESSAGE):
        run_query("SELECT range FROM range(1001)", max_rows=1, timeout_s=0.05)
    assert connection.fetches == 2
    assert time.perf_counter() - started < 5


def test_runaway_query_interrupted() -> None:
    started = time.perf_counter()
    with pytest.raises(QueryError) as caught:
        run_query(
            "SELECT count(*) FROM range(1000000) a, range(1000000) b",
            timeout_s=1,
        )
    assert caught.value.message == _TIMEOUT_MESSAGE
    assert time.perf_counter() - started < 5


def test_sandbox_write_then_reset() -> None:
    try:
        run_query("DELETE FROM plant", target="sandbox")
        remaining = run_query("SELECT count(*) FROM plant", target="sandbox")
        assert remaining.rows[0][0] == 0
        reset_sandbox()
        restored = run_query("SELECT count(*) FROM plant", target="sandbox")
        assert restored.rows[0][0] == 3
        lesson = run_query("SELECT count(*) FROM plant")
        assert lesson.rows[0][0] == 3
    finally:
        reset_sandbox()
