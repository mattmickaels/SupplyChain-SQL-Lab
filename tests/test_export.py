"""Excel export of a query result."""

import re
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from sclab.engine.query import _connect
from sclab.engine.query import run_query as real_run_query
from sclab.engine.types import QueryResult
from sclab.web.app import create_app, format_cell
from sclab.web.export import write_xlsx

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
    download = script.split("function downloadExport", 1)[1]
    assert download.index("saveEditors()") < download.index("new FormData")


def test_text_cells_are_not_formulas(client) -> None:
    sql = "SELECT '=2+3' AS \"=2+3\", '+1' AS \"+1\", '@x' AS \"@x\", '#N/A' AS \"#N/A\""
    response = client.post(
        "/api/export",
        data={"sql": sql, "target": "lesson", "label": "text"},
    )
    sheet = _sheet(response)
    for column, text in enumerate(("=2+3", "+1", "@x", "#N/A"), start=1):
        header = sheet.cell(1, column)
        value = sheet.cell(2, column)
        assert header.value == text
        assert header.data_type == "s"
        assert value.value == text
        assert value.data_type == "s"


def test_insert_returning_does_not_change_rows(client) -> None:
    reset = client.post("/api/sandbox/reset")
    assert reset.status_code == 200
    count_sql = "SELECT count(*) AS n FROM plant WHERE plant_code = 'ZZ99'"
    before = client.post("/api/run", data={"sql": count_sql, "target": "sandbox"})
    assert ">0<" in before.text
    insert = (
        "INSERT INTO plant (plant_code, name, city, country) "
        "VALUES ('ZZ99', 'Export Test', 'Nowhere', 'US') "
        "RETURNING plant_code"
    )
    response = client.post(
        "/api/export",
        data={"sql": insert, "target": "sandbox", "label": "insert"},
    )
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Download works for SELECT queries." in response.text
    assert "Run your change first, then download a SELECT of the rows." in response.text
    assert "Back to the lesson" in response.text
    assert not response.content.startswith(b"PK")
    after = client.post("/api/run", data={"sql": count_sql, "target": "sandbox"})
    assert ">0<" in after.text
    assert ">1<" not in after.text


def test_select_export_does_not_advance_sandbox_sequence(client) -> None:
    assert client.post("/api/sandbox/reset").status_code == 200
    created = client.post(
        "/api/run", data={"sql": "CREATE SEQUENCE export_sequence START 41", "target": "sandbox"}
    )
    assert "error" not in created.text.lower()
    for _ in range(2):
        response = client.post(
            "/api/export",
            data={"sql": "SELECT nextval('export_sequence')", "target": "sandbox"},
        )
        if _XLSX in response.headers["content-type"]:
            assert _sheet(response)["A2"].value == 41
        else:
            assert response.status_code == 200
            assert "text/html" in response.headers["content-type"]
            assert "Could not export" in response.text
    after = client.post(
        "/api/run", data={"sql": "SELECT nextval('export_sequence')", "target": "sandbox"}
    )
    assert ">41<" in after.text


def test_sandbox_writer_returns_export_error_without_fallback(client) -> None:
    assert client.post("/api/sandbox/reset").status_code == 200
    writer = _connect("sandbox")
    try:
        writer.execute("CREATE SEQUENCE held_sequence START 41")
        response = client.post(
            "/api/export",
            data={"sql": "SELECT nextval('held_sequence')", "target": "sandbox"},
        )
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Could not export" in response.text
        assert writer.execute("SELECT nextval('held_sequence')").fetchone() == (41,)
    finally:
        writer.close()


def test_every_grid_downloads_current_sql(client) -> None:
    ran = client.post(
        "/api/run",
        data={
            "sql": "SELECT 1 AS n",
            "target": "lesson",
            "form_id": "form-sample-plants",
            "exercise_id": "sample-plants",
            "label": "sample-plants",
        },
    )
    assert 'data-export="form-sample-plants"' in ran.text
    assert 'name="sql"' not in ran.text
    checked = client.post(
        "/api/check/sample-plants",
        data={"sql": "SELECT 'NOPE' AS plant_code"},
    )
    assert "Missing rows" in checked.text
    assert "Extra rows" in checked.text
    assert checked.text.count("Download Excel") == 2
    assert checked.text.count('data-export="form-sample-plants"') == 2
    assert 'name="sql"' not in checked.text
    schema = client.get("/schema/plant")
    assert 'data-export="schema-plant"' in schema.text
    assert "SELECT * FROM plant LIMIT 20" in schema.text
    playground = client.post(
        "/api/run",
        data={
            "sql": "SELECT 1 AS n",
            "target": "sandbox",
            "form_id": "playground-form",
            "label": "playground",
        },
    )
    assert 'data-export="playground-form"' in playground.text
    assert 'name="sql"' not in playground.text


def test_export_timeout_returns_page(client, monkeypatch) -> None:
    def limited(
        sql: str,
        target: str = "lesson",
        max_rows: int = 1000,
        timeout_s: float = 15.0,
        *,
        read_only: bool = False,
    ):
        return real_run_query(
            sql, target=target, max_rows=max_rows, timeout_s=0.05, read_only=read_only
        )

    monkeypatch.setattr("sclab.web.app.run_query", limited)
    response = client.post(
        "/api/export",
        data={
            "sql": "SELECT sum(a.range) FROM range(1000000) a, range(3000) b",
            "target": "lesson",
            "label": "slow",
            "exercise_id": "sample-plants",
        },
    )
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "spreadsheet" not in response.headers["content-type"]
    assert "Back to the lesson" in response.text
    assert 'href="/lessons/00-sample#exercise-sample-plants"' in response.text
    assert "15 seconds" in response.text
    assert not response.content.startswith(b"PK")


def test_grid_and_workbook_share_column_types(client) -> None:
    sql = (
        "SELECT DATE '2026-11-01' AS day, "
        "12.50::DECIMAL(10, 2) AS amount, "
        "NULL::INTEGER AS empty, "
        "'  pad  ' AS label, "
        "TRUE AS flag, "
        "'2026-11-01' AS text_day, "
        "TIMESTAMP '2026-11-01 12:34:56' AS ts, "
        "CAST('2026-11-01 12:34:56' AS TIMESTAMP_S) AS ts_s, "
        "CAST('2026-11-01 12:34:56' AS TIMESTAMP_MS) AS ts_ms, "
        "CAST('2026-11-01 12:34:56' AS TIMESTAMP_NS) AS ts_ns, "
        "TIMESTAMPTZ '2026-11-01 12:34:56+05:30' AS ts_zone"
    )
    grid = client.post(
        "/api/run", data={"sql": sql, "target": "lesson", "form_id": "form-sample-plants"}
    )
    assert ">2026-11-01<" in grid.text
    assert ">12.50<" in grid.text
    assert '<span class="null">NULL</span>' in grid.text
    assert ">  pad  <" in grid.text
    assert ">true<" in grid.text
    sheet = _sheet(
        client.post("/api/export", data={"sql": sql, "target": "lesson", "label": "types"})
    )
    assert sheet["A2"].value == datetime(2026, 11, 1)
    assert isinstance(sheet["B2"].value, int | float)
    assert sheet["B2"].value == 12.5
    assert sheet["C2"].value is None
    assert sheet["D2"].value == "  pad  "
    assert sheet["E2"].value is True
    assert sheet["F2"].value == "2026-11-01"
    assert sheet["F2"].data_type == "s"
    for column in "GHIJ":
        assert sheet[f"{column}2"].data_type == "d"
        assert sheet[f"{column}2"].value == datetime(2026, 11, 1, 12, 34, 56)
        assert sheet[f"{column}2"].number_format == "yyyy-mm-dd hh:mm:ss"
    assert sheet["K1"].value == "ts_zone (UTC)"
    assert sheet["K2"].data_type == "d"
    assert sheet["K2"].value == datetime(2026, 11, 1, 7, 4, 56)
    moment = datetime(2026, 11, 1, 0, 0)
    typed = QueryResult(
        columns=["as_text", "as_date"],
        column_types=["VARCHAR", "DATE"],
        rows=[(moment, moment)],
        total_rows=1,
        truncated=False,
        elapsed_ms=1,
    )
    direct = load_workbook(BytesIO(write_xlsx(typed, "types", None))).active
    assert direct is not None
    assert direct["A2"].data_type == "s"
    assert direct["A2"].value == format_cell(moment, "VARCHAR")
    assert isinstance(direct["B2"].value, datetime)
    assert "2026-11-01" in format_cell(moment, "DATE")


def test_python_temporal_values_and_midnight_zone_are_excel_dates() -> None:
    moment = datetime(2026, 11, 1, 12, 34, 56)
    midnight = datetime(2026, 11, 1, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    result = QueryResult(
        columns=["fallback", "midnight"],
        column_types=["UNRECOGNIZED", "TIMESTAMP WITH TIME ZONE"],
        rows=[(moment, midnight)],
        total_rows=1,
        truncated=False,
        elapsed_ms=0,
    )
    sheet = load_workbook(BytesIO(write_xlsx(result, "temporal", None))).active
    assert sheet is not None
    assert sheet["A2"].data_type == "d"
    assert sheet["A2"].value == moment
    assert sheet["B1"].value == "midnight (UTC)"
    assert sheet["B2"].data_type == "d"
    assert sheet["B2"].value == datetime(2026, 10, 31, 18, 30)


def test_error_link_returns_to_the_editor(client) -> None:
    sql = "SELEC plant_code FROM plant"
    lesson = client.post(
        "/api/export",
        data={
            "sql": sql,
            "target": "lesson",
            "label": "sample-plants",
            "exercise_id": "sample-plants",
        },
    )
    assert 'href="/lessons/00-sample#exercise-sample-plants"' in lesson.text
    page = client.get("/lessons/00-sample")
    assert sql in page.text
    playground = client.post(
        "/api/export",
        data={"sql": "SELEC 1", "target": "sandbox", "label": "playground"},
    )
    assert 'href="/playground?sql=SELEC+1"' in playground.text
    returned = client.get("/playground", params={"sql": "SELEC 1"})
    assert "SELEC 1" in returned.text
