"""Web app routes, result panels, and the port picker."""

import re
import socket
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sclab.cli import main, pick_port
from sclab.engine.query import run_query as real_run_query
from sclab.lessons.progress import ProgressStore
from sclab.paths import progress_path
from sclab.web.app import create_app


def _clear_progress() -> None:
    path = progress_path()
    if path.exists():
        path.unlink()


_URL = re.compile(r"https?://")
_ROOT = Path(__file__).resolve().parents[1]
_TEMPLATE_DIR = _ROOT / "src" / "sclab" / "web" / "templates"
_APP_JS = _ROOT / "src" / "sclab" / "static" / "app.js"
_SCRIPT = "<script>alert(1)</script>"
_ESCAPED_SCRIPT = "&lt;script&gt;alert(1)&lt;/script&gt;"


@pytest.fixture
def client(lab_home):
    test_client = TestClient(create_app(include_sample=True), base_url="http://127.0.0.1")
    test_client.headers.update({"HX-Request": "true"})
    return test_client


def test_home_lists_lessons(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Sample lesson" in response.text
    assert "Today in the lab is 1 November 2026" in response.text


def test_run_returns_grid(client) -> None:
    response = client.post(
        "/api/run",
        data={"sql": "SELECT plant_code FROM plant ORDER BY 1", "target": "lesson"},
    )
    assert response.status_code == 200
    assert "plant_code" in response.text
    assert "<table" in response.text
    assert response.text.count("<tr") == 4


def test_run_syntax_error_returns_message_not_500(client) -> None:
    response = client.post("/api/run", data={"sql": "SELEC 1", "target": "lesson"})
    assert response.status_code == 200
    assert "syntax error" in response.text.lower()


def test_run_unknown_column_returns_message_not_500(client) -> None:
    response = client.post(
        "/api/run",
        data={"sql": "SELECT no_such_column FROM plant", "target": "lesson"},
    )
    assert response.status_code == 200
    assert "no_such_column" in response.text
    assert "<table" not in response.text


def test_run_truncation_label(client) -> None:
    response = client.post(
        "/api/run",
        data={"sql": "SELECT * FROM stock_movement", "target": "lesson"},
    )
    assert response.status_code == 200
    assert "Showing 1,000 of" in response.text


def test_check_records_progress(client) -> None:
    response = client.post(
        "/api/check/sample-plants",
        data={"sql": "SELECT plant_code FROM plant"},
    )
    assert response.status_code == 200
    assert "Result matches." in response.text
    assert ProgressStore(progress_path()).status("sample-plants") == "passed"
    home = client.get("/")
    assert "1 of 3 passed" in home.text


def test_solution_hidden_before_first_attempt(client) -> None:
    hidden = client.get("/api/solution/sample-count")
    assert hidden.status_code == 200
    assert "Try a Check first, then the solution unlocks." in hidden.text
    assert "SELECT count(*) AS plant_count FROM plant" not in hidden.text
    attempted = client.post("/api/check/sample-count", data={"sql": "SELECT 1"})
    assert attempted.status_code == 200
    shown = client.get("/api/solution/sample-count")
    assert shown.status_code == 200
    assert "SELECT count(*) AS plant_count FROM plant" in shown.text


def test_schema_page_shows_sap_names(client) -> None:
    response = client.get("/schema/material")
    assert response.status_code == 200
    assert "SAP table: MARA" in response.text
    assert ">None<" not in response.text
    family = client.get("/schema/product_family")
    assert family.status_code == 200
    assert "SAP table:" not in family.text
    assert ">None<" not in family.text
    assert ">-<" in family.text
    assert 'class="prose"' in family.text


def test_sandbox_reset(client) -> None:
    deleted = client.post("/api/run", data={"sql": "DELETE FROM plant", "target": "sandbox"})
    assert deleted.status_code == 200
    remaining = client.post(
        "/api/run",
        data={"sql": "SELECT count(*) AS n FROM plant", "target": "sandbox"},
    )
    assert ">0<" in remaining.text
    reset = client.post("/api/sandbox/reset")
    assert reset.status_code == 200
    assert "Practice database reset." in reset.text
    restored = client.post(
        "/api/run",
        data={"sql": "SELECT count(*) AS n FROM plant", "target": "sandbox"},
    )
    assert ">3<" in restored.text


def test_no_external_urls_in_templates(client) -> None:
    for path in _TEMPLATE_DIR.glob("*.html"):
        assert _URL.search(path.read_text(encoding="utf-8")) is None, path.name
    for route in ("/", "/schema", "/schema/material", "/playground", "/lessons/00-sample"):
        page = client.get(route)
        assert page.status_code == 200
        assert _URL.search(page.text) is None, route
    for route in ("/docs", "/redoc", "/openapi.json"):
        page = client.get(route)
        assert page.status_code == 404, route


def test_pick_port_skips_busy_port() -> None:
    held: socket.socket | None = None
    try:
        for _ in range(32):
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])
            if port >= 65535:
                probe.close()
                continue
            nxt = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                nxt.bind(("127.0.0.1", port + 1))
            except OSError:
                nxt.close()
                probe.close()
                continue
            nxt.close()
            held = probe
            held.listen(1)
            assert pick_port(start=port, end=port + 5) == port + 1
            return
        raise AssertionError("No two consecutive free ports.")
    finally:
        if held is not None:
            held.close()


def test_app_js_syncs_editor_on_change() -> None:
    source = _APP_JS.read_text(encoding="utf-8")
    matched = re.search(
        r"""\.on\(\s*["']change["']\s*,\s*(?:function\s*\([^)]*\)|\([^)]*\)\s*=>)[\s\S]{0,80}?\.save\(\)""",
        source,
    )
    assert matched is not None


def test_slow_query_does_not_block_other_requests(lab_home, monkeypatch) -> None:
    def limited(sql: str, target: str = "lesson", max_rows: int = 1000, timeout_s: float = 3.0):
        return real_run_query(sql, target=target, max_rows=max_rows, timeout_s=3.0)

    monkeypatch.setattr("sclab.web.app.run_query", limited)
    # count(*) of two ranges returns immediately, so the cross join is summed instead.
    sql = "SELECT sum(a.range) FROM range(1000000) a, range(3000) b"
    box: dict = {}
    # One portal, so both requests share an event loop. A fresh TestClient does not.
    with TestClient(create_app(include_sample=True), base_url="http://127.0.0.1") as client:
        client.headers.update({"HX-Request": "true"})

        def post() -> None:
            box["response"] = client.post("/api/run", data={"sql": sql, "target": "lesson"})

        worker = threading.Thread(target=post)
        worker.start()
        time.sleep(0.2)
        assert worker.is_alive()
        started = time.perf_counter()
        home = client.get("/")
        elapsed = time.perf_counter() - started
        assert home.status_code == 200
        assert elapsed < 1
        assert worker.is_alive()
        worker.join(timeout=10)
        assert not worker.is_alive()
        assert box["response"].status_code == 200


def test_browser_opens_after_server_ready(lab_home, monkeypatch) -> None:
    calls: list[str] = []

    def fake_run(*_args, **kwargs):
        calls.append("server")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind((kwargs["host"], kwargs["port"]))
        sock.listen(1)
        sock.settimeout(5)
        try:
            conn, _addr = sock.accept()
            conn.close()
        finally:
            sock.close()

    def fake_open(_url):
        calls.append("browser")

    monkeypatch.setattr("sclab.cli.uvicorn.run", fake_run)
    monkeypatch.setattr("sclab.cli.webbrowser.open", fake_open)
    main([])
    assert calls == ["server", "browser"]


def test_help_exits_before_building_the_database(monkeypatch, capsys) -> None:
    def boom(*_args, **_kwargs):
        raise AssertionError("database")

    monkeypatch.setattr("sclab.cli.ensure_database", boom)
    with pytest.raises(SystemExit) as caught:
        main(["--help"])
    assert caught.value.code == 0
    output = capsys.readouterr().out
    assert "usage" in output.lower()
    assert "--no-browser" in output


def test_no_browser_skips_the_browser(lab_home, monkeypatch) -> None:
    calls: list[str] = []

    def fake_run(*_args, **kwargs):
        calls.append("server")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind((kwargs["host"], kwargs["port"]))
        sock.listen(1)
        sock.settimeout(5)
        try:
            conn, _addr = sock.accept()
            conn.close()
        finally:
            sock.close()

    def fake_open(_url):
        calls.append("browser")

    monkeypatch.setattr("sclab.cli.uvicorn.run", fake_run)
    monkeypatch.setattr("sclab.cli.webbrowser.open", fake_open)
    main(["--no-browser"])
    assert calls == ["server"]


def test_result_grid_escapes_script_literal(client) -> None:
    response = client.post(
        "/api/run",
        data={"sql": f"SELECT '{_SCRIPT}' AS note", "target": "lesson"},
    )
    assert response.status_code == 200
    assert _ESCAPED_SCRIPT in response.text
    assert "<script>" not in response.text


def test_run_formats_cells_and_aligns_numbers(client) -> None:
    response = client.post(
        "/api/run",
        data={
            "sql": (
                "SELECT DATE '2026-11-01' AS day, "
                "1.50::DECIMAL(10,2) AS amount, "
                "NULL::INTEGER AS empty, "
                "TRUE AS flag"
            ),
            "target": "lesson",
        },
    )
    assert response.status_code == 200
    assert ">2026-11-01</td>" in response.text
    assert "Decimal" not in response.text
    assert ">1.50</td>" in response.text
    assert '<span class="null">NULL</span>' in response.text
    assert ">true</td>" in response.text
    assert 'class="num"' in response.text
    assert 'class="num">2026-11-01' not in response.text
    assert 'class="num">true' not in response.text


def test_check_feedback_renders_row_grids(client) -> None:
    response = client.post(
        "/api/check/sample-plants",
        data={"sql": "SELECT 'NOPE' AS plant_code"},
    )
    assert response.status_code == 200
    assert "<table" in response.text
    assert "plant_code" in response.text
    assert ">NOPE</td>" in response.text
    assert "('NOPE'," not in response.text
    counted = client.post(
        "/api/check/sample-count",
        data={"sql": "SELECT 1 AS plant_count"},
    )
    assert counted.status_code == 200
    assert "plant_count" in counted.text
    assert 'class="num"' in counted.text


def test_check_empty_sql_does_not_record_or_unlock(client) -> None:
    _clear_progress()
    response = client.post("/api/check/sample-count", data={"sql": "   "})
    assert response.status_code == 200
    assert "Type a query first." in response.text
    store = ProgressStore(progress_path())
    assert store.status("sample-count") == "new"
    assert store.last_sql("sample-count") is None
    hidden = client.get("/api/solution/sample-count")
    assert "Try a Check first, then the solution unlocks." in hidden.text
    client.post("/api/check/sample-count", data={"sql": "SELECT 1"})
    emptied = client.post("/api/check/sample-count", data={"sql": ""})
    assert emptied.status_code == 200
    assert "Type a query first." in emptied.text
    saved = ProgressStore(progress_path())
    assert saved.status("sample-count") == "attempted"
    assert saved.last_sql("sample-count") == "SELECT 1"


def test_check_query_error_does_not_record_or_overwrite(client) -> None:
    _clear_progress()
    client.post("/api/check/sample-plants", data={"sql": "SELECT 1"})
    response = client.post("/api/check/sample-plants", data={"sql": "SELEC 1"})
    assert response.status_code == 200
    assert "syntax error" in response.text.lower()
    store = ProgressStore(progress_path())
    assert store.status("sample-plants") == "attempted"
    assert store.last_sql("sample-plants") == "SELECT 1"
    fresh = client.post("/api/check/sample-count", data={"sql": "SELEC 1"})
    assert fresh.status_code == 200
    assert ProgressStore(progress_path()).status("sample-count") == "new"
    hidden = client.get("/api/solution/sample-count")
    assert "Try a Check first, then the solution unlocks." in hidden.text


def test_run_saves_draft_shown_ahead_of_older_sql(client) -> None:
    _clear_progress()
    client.post("/api/check/sample-plants", data={"sql": "SELECT 1"})
    draft = "SELECT plant_code FROM plant"
    saved = client.post(
        "/api/run",
        data={"sql": draft, "target": "lesson", "exercise_id": "sample-plants"},
    )
    assert saved.status_code == 200
    page = client.get("/lessons/00-sample")
    assert draft in page.text
    assert "SELECT 1" not in page.text
    client.post("/api/check/sample-plants", data={"sql": "SELECT 9"})
    checked = client.get("/lessons/00-sample")
    assert "SELECT 9" in checked.text
    assert draft not in checked.text


def test_post_without_htmx_header_is_forbidden(lab_home) -> None:
    raw = TestClient(create_app(include_sample=True), base_url="http://127.0.0.1")
    response = raw.post("/api/run", data={"sql": "SELECT 1", "target": "lesson"})
    assert response.status_code == 403
    assert 'class="message"' in response.text
    assert "Use the lab page to run that action." in response.text


def test_untrusted_host_is_rejected(lab_home) -> None:
    raw = TestClient(create_app(include_sample=True), base_url="http://evil.example")
    response = raw.get("/")
    assert response.status_code == 400


def test_unexpected_post_errors_render_plain_panel(client, monkeypatch) -> None:
    def boom(*_args, **_kwargs):
        raise RuntimeError("secret boom")

    monkeypatch.setattr("sclab.web.app.run_query", boom)
    monkeypatch.setattr("sclab.web.app.check_answer", boom)
    monkeypatch.setattr("sclab.web.app.reset_sandbox", boom)
    for path, data in (
        ("/api/run", {"sql": "SELECT 1", "target": "lesson"}),
        ("/api/check/sample-plants", {"sql": "SELECT 1"}),
        ("/api/sandbox/reset", {}),
    ):
        response = client.post(path, data=data)
        assert response.status_code == 200
        assert 'class="message"' in response.text
        assert "Something went wrong. Try again." in response.text
        assert "secret boom" not in response.text


def test_ui_polish_sources() -> None:
    css = (_ROOT / "src" / "sclab" / "static" / "app.css").read_text(encoding="utf-8")
    script = _APP_JS.read_text(encoding="utf-8")
    lesson = (_TEMPLATE_DIR / "lesson.html").read_text(encoding="utf-8")
    playground = (_TEMPLATE_DIR / "playground.html").read_text(encoding="utf-8")
    assert "viewportMargin: Infinity" in script
    assert "htmx:responseError" in script
    assert "Something went wrong. Try again." in script
    assert 'aria-label="{{ exercise.title }}"' in lesson
    assert 'aria-label="Playground"' in playground
    assert ".CodeMirror-scroll" in css
    assert "min-height: 8rem" in css.split(".CodeMirror-scroll", 1)[1][:200]
    assert "white-space: pre-wrap" in css
    for token in (
        ".cm-keyword",
        ".cm-string",
        ".cm-number",
        ".cm-comment",
        ".cm-operator",
        ".cm-variable-2",
    ):
        assert token in css
    assert ".prose pre" in css
    assert ".prose code" in css
    number = re.search(r"\.lesson-number\s*\{([^}]*)\}", css)
    assert number is not None
    assert "width: 3ch" in number.group(1)
    assert "text-align: right" in number.group(1)


def test_error_panel_escapes_script_literal(client) -> None:
    response = client.post(
        "/api/run",
        data={"sql": f"SELECT '{_SCRIPT}' FROM missing_table", "target": "lesson"},
    )
    assert response.status_code == 200
    assert _ESCAPED_SCRIPT in response.text
    assert "<script>" not in response.text
