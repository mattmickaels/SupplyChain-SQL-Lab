"""Web app routes, result panels, and the port picker."""

import re
import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sclab.cli import pick_port
from sclab.lessons.progress import ProgressStore
from sclab.paths import progress_path
from sclab.web.app import create_app

_URL = re.compile(r"https?://")
_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "src" / "sclab" / "web" / "templates"


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
    assert hidden.status_code == 403
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


def test_pick_port_skips_busy_port() -> None:
    held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    held.bind(("127.0.0.1", 8765))
    held.listen(1)
    try:
        assert pick_port() == 8766
    finally:
        held.close()
