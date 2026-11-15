"""Drill mode, Databricks notes, and the drill Excel download."""

import re
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from sclab.engine.query import run_query as real_run_query
from sclab.paths import progress_path
from sclab.web.app import create_app


@pytest.fixture
def client(lab_home):
    path = progress_path()
    before = path.read_bytes() if path.exists() else None
    test_client = TestClient(create_app(include_sample=True), base_url="http://127.0.0.1")
    test_client.headers.update({"HX-Request": "true"})
    yield test_client
    if before is None:
        path.unlink(missing_ok=True)
    else:
        path.write_bytes(before)


_ROOT = Path(__file__).resolve().parents[1]
_APP_JS = _ROOT / "src" / "sclab" / "static" / "app.js"
_PROMPT = "Count the plants, then summarize the result in three lines."
_MODEL_FIRST = "There are 3 plants."
_MODEL_LAST = "No action is needed."
_NOTE = "On Databricks: count(*) is spelled the same way on Databricks."
_JOINS_NOTE = (
    "On Databricks: date_add(requested_delivery_date, 56) matches "
    "requested_delivery_date + INTERVAL 56 DAY."
)
_TIME_UP = "Time is up - finish your answer, then write your summary"
_LEAD = (
    "Write three lines for an executive: the finding, the number that matters, what you&#39;d do"
)
_ANSWER = "SELECT count(*) AS plant_count FROM plant"
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _remaining(html: str) -> int:
    matched = re.search(r'data-remaining="(\d+)"', html)
    assert matched is not None
    return int(matched.group(1))


def test_prompt_hidden_before_start(client) -> None:
    page = client.get("/lessons/00-sample")
    assert page.status_code == 200
    assert "Timed plant count" in page.text
    assert "15 minutes" in page.text
    assert 'hx-post="/api/drill/sample-drill/start"' in page.text
    assert ">Start<" in page.text
    assert _PROMPT not in page.text
    assert _MODEL_FIRST not in page.text
    assert _MODEL_LAST not in page.text
    assert _NOTE not in page.text
    assert _LEAD not in page.text
    assert 'id="sql-sample-drill"' not in page.text
    assert "data-remaining" not in page.text


def test_start_reveals_prompt_and_timer(client, monkeypatch) -> None:
    monkeypatch.setattr("sclab.web.app._now", lambda: datetime(2026, 11, 1, 9, 0, 0))
    started = client.post("/api/drill/sample-drill/start")
    assert started.status_code == 200
    assert _PROMPT in started.text
    assert _remaining(started.text) == 900
    assert "15:00" in started.text
    assert 'id="sql-sample-drill"' in started.text
    assert ">Run<" in started.text
    assert ">Check<" in started.text
    assert _ANSWER not in started.text
    assert _MODEL_FIRST not in started.text
    assert "data-summary-box hidden" in started.text
    script = _APP_JS.read_text(encoding="utf-8")
    assert _TIME_UP in script
    assert "data-remaining" in script
    assert "drill-summary" in script
    countdown = script.split("function armCountdown", 1)[1]
    assert 'getAttribute("data-remaining")' in countdown
    assert "Date.now" not in countdown


def test_restart_keeps_original_start_time(client, monkeypatch) -> None:
    first = datetime(2026, 11, 1, 9, 0, 0)
    later = first + timedelta(seconds=90)
    monkeypatch.setattr("sclab.web.app._now", lambda: first)
    started = client.post("/api/drill/sample-drill/start")
    assert started.status_code == 200
    assert _remaining(started.text) == 900
    monkeypatch.setattr("sclab.web.app._now", lambda: later)
    again = client.post("/api/drill/sample-drill/start")
    assert again.status_code == 200
    assert _remaining(again.text) == 810
    assert _remaining(again.text) < _remaining(started.text)
    assert _PROMPT in again.text


def test_model_summary_only_after_submit(client, monkeypatch) -> None:
    monkeypatch.setattr("sclab.web.app._now", lambda: datetime(2026, 11, 1, 9, 0, 0))
    page = client.get("/lessons/00-sample")
    assert _MODEL_FIRST not in page.text
    assert _MODEL_LAST not in page.text
    started = client.post("/api/drill/sample-drill/start")
    assert _MODEL_FIRST not in started.text
    assert _LEAD in started.text
    missed = client.post("/api/check/sample-drill", data={"sql": "SELECT 1"})
    assert missed.status_code == 200
    assert missed.headers.get("HX-Trigger") is None
    assert _MODEL_FIRST not in missed.text
    passed = client.post(
        "/api/check/sample-drill",
        data={"sql": _ANSWER},
    )
    assert "Result matches." in passed.text
    assert passed.headers.get("HX-Trigger") == "drill-summary"
    assert _MODEL_FIRST not in passed.text
    assert _MODEL_LAST not in passed.text
    shown = client.get("/lessons/00-sample")
    assert _LEAD in shown.text
    assert "data-summary-box hidden" not in shown.text
    assert _MODEL_FIRST not in shown.text
    submitted = client.post(
        "/api/drill/sample-drill/summary",
        data={"text": "There are 3 plants.\nThe number is 3.\nNo change."},
    )
    assert submitted.status_code == 200
    assert _MODEL_FIRST in submitted.text
    assert _MODEL_LAST in submitted.text
    assert "There are 3 plants." in submitted.text
    assert "Names the number that matters" in submitted.text
    assert "Recommends an action" in submitted.text
    assert "Three lines or fewer" in submitted.text
    assert submitted.text.count('type="checkbox"') == 3
    assert "summary-review" in submitted.text
    assert "Your summary" in submitted.text
    assert "Model summary" in submitted.text


def test_draft_survives_expired_reload_without_model(client, monkeypatch) -> None:
    first = datetime(2026, 11, 1, 9, 0, 0)
    monkeypatch.setattr("sclab.web.app._now", lambda: first)
    started = client.post("/api/drill/sample-drill/start")
    assert started.status_code == 200
    monkeypatch.setattr("sclab.web.app._now", lambda: first + timedelta(seconds=900))
    draft = "Draft finding: three plants stay at 3."
    saved = client.post("/api/drill/sample-drill/draft", data={"text": draft})
    assert saved.status_code == 200
    assert _MODEL_FIRST not in saved.text
    reloaded = client.get("/lessons/00-sample")
    assert draft in reloaded.text
    assert _TIME_UP in reloaded.text
    assert _MODEL_FIRST not in reloaded.text
    assert _MODEL_LAST not in reloaded.text
    assert 'hx-post="/api/drill/sample-drill/draft"' in reloaded.text
    assert "delay:1s" in reloaded.text
    script = _APP_JS.read_text(encoding="utf-8")
    saver = script.split("function saveDrillDrafts", 1)[1]
    assert "pagehide" in script
    assert "keepalive" in saver
    assert "HX-Request" in saver
    submitted = client.post("/api/drill/sample-drill/summary", data={"text": draft})
    assert _MODEL_FIRST in submitted.text
    assert _MODEL_LAST in submitted.text
    assert draft in submitted.text


def test_summary_persists_across_reload(client, monkeypatch) -> None:
    first = datetime(2026, 11, 1, 9, 0, 0)
    monkeypatch.setattr("sclab.web.app._now", lambda: first)
    started = client.post("/api/drill/sample-drill/start")
    assert started.status_code == 200
    monkeypatch.setattr("sclab.web.app._now", lambda: first + timedelta(seconds=900))
    expired = client.get("/lessons/00-sample")
    assert _TIME_UP in expired.text
    assert _remaining(expired.text) == 0
    assert _PROMPT in expired.text
    assert 'id="sql-sample-drill"' in expired.text
    assert ">Run<" in expired.text
    assert ">Check<" in expired.text
    assert "data-summary-box hidden" not in expired.text
    assert _MODEL_FIRST not in expired.text
    text = "Finding: 3 plants.\nNumber: 3.\nAction: <b>none</b>."
    submitted = client.post("/api/drill/sample-drill/summary", data={"text": text})
    assert _MODEL_FIRST in submitted.text
    assert "&lt;b&gt;none&lt;/b&gt;" in submitted.text
    assert "<b>none</b>" not in submitted.text
    monkeypatch.setattr("sclab.web.app._now", lambda: first + timedelta(seconds=1200))
    reloaded = client.get("/lessons/00-sample")
    assert "Finding: 3 plants." in reloaded.text
    assert "&lt;b&gt;none&lt;/b&gt;" in reloaded.text
    assert _MODEL_FIRST in reloaded.text
    assert _MODEL_LAST in reloaded.text
    assert _TIME_UP in reloaded.text
    assert _remaining(reloaded.text) == 0
    assert 'type="checkbox"' in reloaded.text


def test_databricks_note_rendered(client, monkeypatch) -> None:
    monkeypatch.setattr("sclab.web.app._now", lambda: datetime(2026, 11, 1, 9, 0, 0))
    hidden = client.get("/lessons/00-sample")
    assert _NOTE not in hidden.text
    started = client.post("/api/drill/sample-drill/start")
    assert _NOTE in started.text
    assert started.text.index(_PROMPT) < started.text.index(_NOTE)
    joins = client.get("/lessons/04-joins")
    assert joins.status_code == 200
    assert _JOINS_NOTE in joins.text
    prompt = "confirmed_delivery_date is at least 56 days after requested_delivery_date."
    assert joins.text.index(prompt) < joins.text.index(_JOINS_NOTE)


def test_export_receives_posted_sql_and_script_saves_editors(client, monkeypatch) -> None:
    """Export runs the posted SQL, and the page script saves editors before the form body."""
    monkeypatch.setattr("sclab.web.app._now", lambda: datetime(2026, 11, 1, 9, 0, 0))
    seen: list[tuple[str, bool]] = []

    def spy(sql, target="lesson", max_rows=1000, timeout_s=15, *, read_only=False):
        seen.append((sql, read_only))
        return real_run_query(
            sql,
            target,
            max_rows,
            timeout_s,
            read_only=read_only,
        )

    monkeypatch.setattr("sclab.web.app.run_query", spy)
    started = client.post("/api/drill/sample-drill/start")
    assert 'id="form-sample-drill"' in started.text
    assert 'name="sql"' in started.text
    ran_sql = "SELECT 41 AS n"
    edited_sql = "SELECT 42 AS n"
    ran = client.post(
        "/api/run",
        data={
            "sql": ran_sql,
            "target": "lesson",
            "form_id": "form-sample-drill",
            "label": "sample-drill",
            "exercise_id": "sample-drill",
            "return_to": "/lessons/00-sample#exercise-sample-drill",
        },
    )
    assert ran.status_code == 200
    assert "Download Excel" in ran.text
    assert 'data-export="form-sample-drill"' in ran.text
    assert ran_sql not in ran.text
    assert 'name="sql"' not in ran.text
    exported = client.post(
        "/api/export",
        data={
            "sql": edited_sql,
            "target": "lesson",
            "form_id": "form-sample-drill",
            "label": "sample-drill",
            "exercise_id": "sample-drill",
            "return_to": "/lessons/00-sample#exercise-sample-drill",
        },
    )
    assert exported.status_code == 200
    assert exported.headers["content-type"].split(";")[0] == _XLSX
    assert [sql for sql, read_only in seen if read_only] == [edited_sql]
    book = load_workbook(BytesIO(exported.content))
    sheet = book.active
    assert sheet is not None
    assert sheet["A2"].value == 42
    script = _APP_JS.read_text(encoding="utf-8")
    download = script.split("function downloadExport", 1)[1]
    assert download.index("saveEditors()") < download.index("new FormData")


def test_drill_download_posts_editor_form(client, monkeypatch) -> None:
    """The drill Download button posts /api/export using the drill editor form."""
    monkeypatch.setattr("sclab.web.app._now", lambda: datetime(2026, 11, 1, 9, 0, 0))
    started = client.post("/api/drill/sample-drill/start")
    assert 'id="form-sample-drill"' in started.text
    assert 'id="sql-sample-drill"' in started.text
    assert 'name="sql"' in started.text
    ran = client.post(
        "/api/run",
        data={
            "sql": "SELECT 41 AS n",
            "target": "lesson",
            "form_id": "form-sample-drill",
            "label": "sample-drill",
            "exercise_id": "sample-drill",
            "return_to": "/lessons/00-sample#exercise-sample-drill",
        },
    )
    assert "Download Excel" in ran.text
    assert 'data-export="form-sample-drill"' in ran.text
    assert 'name="sql"' not in ran.text
    script = _APP_JS.read_text(encoding="utf-8")
    download = script.split("function downloadExport", 1)[1]
    assert "/api/export" in download
    assert 'getAttribute("data-export")' in download
    assert "getElementById(formId)" in download
    assert download.index("saveEditors()") < download.index("/api/export")
