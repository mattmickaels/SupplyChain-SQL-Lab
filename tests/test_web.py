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

_URL = re.compile(r"https?://")
_ROOT = Path(__file__).resolve().parents[1]
_TEMPLATE_DIR = _ROOT / "src" / "sclab" / "web" / "templates"
_APP_JS = _ROOT / "src" / "sclab" / "static" / "app.js"
_SCRIPT = "<script>alert(1)</script>"
_ESCAPED_SCRIPT = "&lt;script&gt;alert(1)&lt;/script&gt;"


@pytest.fixture
def client(lab_home):
    return TestClient(create_app(include_sample=True))


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
    assert "1 of 2 passed" in home.text


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
    assert "MARA" in response.text


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
    held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    held.bind(("127.0.0.1", 8765))
    held.listen(1)
    try:
        assert pick_port() == 8766
    finally:
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
    with TestClient(create_app(include_sample=True)) as client:

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
    main()
    assert calls == ["server", "browser"]


def test_result_grid_escapes_script_literal(client) -> None:
    response = client.post(
        "/api/run",
        data={"sql": f"SELECT '{_SCRIPT}' AS note", "target": "lesson"},
    )
    assert response.status_code == 200
    assert _ESCAPED_SCRIPT in response.text
    assert "<script>" not in response.text


def test_error_panel_escapes_script_literal(client) -> None:
    response = client.post(
        "/api/run",
        data={"sql": f"SELECT '{_SCRIPT}' FROM missing_table", "target": "lesson"},
    )
    assert response.status_code == 200
    assert _ESCAPED_SCRIPT in response.text
    assert "<script>" not in response.text
