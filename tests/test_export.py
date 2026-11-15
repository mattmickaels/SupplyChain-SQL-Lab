"""Excel export of a query result."""

import re
from datetime import datetime
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from sclab.web.app import create_app

_ROOT = Path(__file__).resolve().parents[1]
_TEMPLATE_DIR = _ROOT / "src" / "sclab" / "web" / "templates"
_APP_JS = _ROOT / "src" / "sclab" / "static" / "app.js"
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_TYPES_SQL = (
    "SELECT DATE '2026-11-01' AS day, "
    "12.50::DECIMAL(10, 2) AS amount, "
    "NULL::VARCHAR AS missing, "
    "'  pad  ' AS label"
)


@pytest.fixture
def client(lab_home):
    test_client = TestClient(create_app(include_sample=True), base_url="http://127.0.0.1")
    test_client.headers.update({"HX-Request": "true"})
    return test_client


def _sheet(response):
    assert response.status_code == 200
    assert response.headers["content-type"].split(";")[0] == _XLSX
    book = load_workbook(BytesIO(response.content))
    sheet = book.active
    assert sheet is not None
    return sheet


def test_export_types(client) -> None:
    response = client.post(
        "/api/export",
        data={"sql": _TYPES_SQL, "target": "lesson", "label": "types"},
    )
    sheet = _sheet(response)
    assert sheet["A1"].value == "day"
    assert isinstance(sheet["A2"].value, datetime)
    assert sheet["A2"].value == datetime(2026, 11, 1)
    assert sheet["A2"].number_format == "yyyy-mm-dd"
    assert isinstance(sheet["B2"].value, int | float)
    assert sheet["B2"].value == 12.5
    assert sheet["C2"].value is None
    assert sheet["D2"].value == "  pad  "


def test_header_bold_frozen_and_filtered(client) -> None:
    response = client.post(
        "/api/export",
        data={
            "sql": "SELECT plant_code FROM plant ORDER BY 1",
            "target": "lesson",
            "label": "plants",
        },
    )
    sheet = _sheet(response)
    assert sheet["A1"].font.bold is True
    assert sheet.freeze_panes == "A2"
    assert sheet.auto_filter.ref is not None
    assert sheet.auto_filter.ref.startswith("A1:")
    assert sheet.column_dimensions["A"].width <= 60


def test_truncation_note(client, monkeypatch) -> None:
    monkeypatch.setattr("sclab.web.export.EXPORT_MAX_ROWS", 10)
    response = client.post(
        "/api/export",
        data={
            "sql": "SELECT * FROM plant CROSS JOIN range(10)",
            "target": "lesson",
            "label": "truncated",
        },
    )
    sheet = _sheet(response)
    note = sheet["A1"].value
    assert isinstance(note, str)
    assert note.startswith("First 10 of ")
    assert note.endswith(" rows")
    assert "30" in note
    assert sheet["A1"].font.italic is True
    assert sheet["A1"].font.bold is not True
    assert sheet["A2"].font.bold is True
    assert sheet.freeze_panes == "A3"
    assert sheet.auto_filter.ref is not None
    assert sheet.auto_filter.ref.startswith("A2:")


def test_query_error_returns_page_not_file(client) -> None:
    response = client.post(
        "/api/export",
        data={"sql": "SELEC 1", "target": "lesson", "label": "bad"},
    )
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "spreadsheet" not in response.headers["content-type"]
    assert "syntax error" in response.text.lower()
    assert "Back to the lesson" in response.text
    assert 'href="/"' in response.text
    assert not response.content.startswith(b"PK")


def test_filename_format(client) -> None:
    response = client.post(
        "/api/export",
        data={"sql": "SELECT 1 AS n", "target": "lesson", "label": "lesson-04-ex-3"},
    )
    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert re.search(r'filename="sclab-lesson-04-ex-3-\d{8}-\d{4}\.xlsx"', disposition)


def test_result_grid_offers_download(client) -> None:
    response = client.post(
        "/api/run",
        data={
            "sql": "SELECT 1 AS n",
            "target": "lesson",
            "form_id": "form-sample-plants",
            "label": "sample-plants",
        },
    )
    assert response.status_code == 200
    assert "Download Excel" in response.text
    assert 'data-export="form-sample-plants"' in response.text
    lesson = client.get("/lessons/00-sample")
    assert 'name="label" value="sample-plants"' in lesson.text
    assert 'name="form_id" value="form-sample-plants"' in lesson.text
    playground = client.get("/playground")
    assert 'name="label" value="playground"' in playground.text
    assert 'name="form_id" value="playground-form"' in playground.text
    grid = (_TEMPLATE_DIR / "_grid.html").read_text(encoding="utf-8")
    assert "Download Excel" in grid
    script = _APP_JS.read_text(encoding="utf-8")
    assert "data-export" in script
    assert "HX-Request" in script
    assert "/api/export" in script
