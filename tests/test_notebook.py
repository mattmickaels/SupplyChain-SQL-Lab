"""Notebook store, sample copy, and notebook pages."""

import json
import os
import re
import threading
from html import unescape
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from markupsafe import escape
from openpyxl import load_workbook

from sclab.engine.query import QueryError, reset_sandbox, run_query
from sclab.notebook.store import Cell, NotebookStore
from sclab.paths import notebooks_dir, sandbox_db_path
from sclab.web.app import create_app

_ROOT = Path(__file__).resolve().parents[1]
_NOTEBOOK_TEMPLATES = _ROOT / "src" / "sclab" / "web" / "templates" / "notebook"
_APP_JS = _ROOT / "src" / "sclab" / "static" / "app.js"
_CHART_JS = _ROOT / "src" / "sclab" / "static" / "vendor" / "chart.umd.min.js"
_URL = re.compile(r"https?://")
_SCRIPT = "<script>alert(1)</script>"
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_notebooks_dir_follows_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCLAB_HOME", str(tmp_path))
    assert notebooks_dir() == tmp_path / "notebooks"
    assert notebooks_dir().is_dir()


def test_store_round_trip(tmp_path: Path) -> None:
    store = NotebookStore(tmp_path)
    created = store.create("Supply check")
    created.cells[0].source = "SELECT 1"
    created.cells[0].chart = {"type": "bar", "x": "plant_code", "y": "n"}
    created.cells.append(Cell(id="note", kind="markdown", source="Hello", chart=None))
    created.target = "sandbox"
    store.save(created)

    loaded = NotebookStore(tmp_path).get(created.id)
    assert loaded.title == "Supply check"
    assert loaded.target == "sandbox"
    assert loaded.cells[0].source == "SELECT 1"
    assert loaded.cells[0].chart == {"type": "bar", "x": "plant_code", "y": "n"}
    assert loaded.cells[1] == Cell(id="note", kind="markdown", source="Hello", chart=None)
    assert loaded.updated_at
    listed = NotebookStore(tmp_path).list()
    assert [item.id for item in listed] == [created.id]
    names = sorted(path.name for path in tmp_path.iterdir())
    assert names == [".sample-copied", f"{created.id}.json"]

    store.delete(created.id)
    with pytest.raises(KeyError):
        store.get(created.id)


def test_corrupt_file_is_quarantined(tmp_path: Path) -> None:
    store = NotebookStore(tmp_path)
    kept = store.create("Keep")
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    listed = store.list()
    assert [item.id for item in listed] == [kept.id]
    assert not (tmp_path / "broken.json").exists()
    quarantined = list(tmp_path.glob("broken.corrupt-*.json"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == "{"


def test_only_corrupt_file_is_replaced_with_the_sample(tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text("[]", encoding="utf-8")
    listed = NotebookStore(tmp_path).list()
    assert [item.id for item in listed] == ["corvane-supply-review"]
    assert list(tmp_path.glob("broken.corrupt-*.json"))


def test_sample_is_copied_once(tmp_path: Path) -> None:
    store = NotebookStore(tmp_path)
    first = store.list()
    assert [item.id for item in first] == ["corvane-supply-review"]
    assert first[0].title
    assert first[0].target == "lesson"
    assert len(first[0].cells) == 6
    assert [cell.kind for cell in first[0].cells] == [
        "sql",
        "sql",
        "sql",
        "sql",
        "sql",
        "markdown",
    ]
    assert first[0].cells[-1].source == ""
    sources = [cell.source for cell in first[0].cells[:5]]
    assert "INTERVAL 56 DAY" in sources[0]
    assert "on_time_pct" in sources[1]
    assert "RF generator,%" in sources[2]
    assert "ETC" in sources[3]
    assert "days_at_risk" in sources[4]
    second = store.list()
    assert [item.id for item in second] == ["corvane-supply-review"]
    assert [path.name for path in tmp_path.glob("*.json")] == ["corvane-supply-review.json"]


def test_read_error_does_not_quarantine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = NotebookStore(tmp_path)
    created = store.create("Keep")
    (tmp_path / ".sample-copied").unlink()
    target = tmp_path / f"{created.id}.json"
    original = target.read_text(encoding="utf-8")
    real_read = Path.read_text

    def read_text(self, *args, **kwargs):
        if self.name == target.name:
            raise OSError(32, "sharing violation")
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    assert store.list() == []
    monkeypatch.undo()
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob("*.corrupt-*.json")) == []
    assert not (tmp_path / "corvane-supply-review.json").exists()


def test_quarantine_failure_does_not_break_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_replace = os.replace

    def replace(src, dst, *args, **kwargs):
        if ".corrupt-" in Path(dst).name:
            raise OSError(13, "denied")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr("sclab.notebook.store.os.replace", replace)
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    listed = NotebookStore(tmp_path).list()
    assert [item.id for item in listed] == ["corvane-supply-review"]
    assert (tmp_path / "broken.json").read_text(encoding="utf-8") == "{"


def test_deleted_sample_is_not_copied_again(tmp_path: Path) -> None:
    store = NotebookStore(tmp_path)
    assert [item.id for item in store.list()] == ["corvane-supply-review"]
    assert (tmp_path / ".sample-copied").is_file()
    store.delete("corvane-supply-review")
    assert store.list() == []
    assert not (tmp_path / "corvane-supply-review.json").exists()


def test_get_sample_without_opening_the_index(tmp_path: Path) -> None:
    notebook = NotebookStore(tmp_path).get("corvane-supply-review")
    assert notebook.id == "corvane-supply-review"
    assert notebook.title
    assert len(notebook.cells) == 6


def test_interleaved_updates_both_survive(tmp_path: Path) -> None:
    store = NotebookStore(tmp_path)
    notebook = store.create("Race")

    def add_cell(item) -> None:
        item.cells.append(Cell(id="second", kind="sql", source="", chart=None))

    store.update(notebook.id, add_cell)
    started = threading.Event()
    release = threading.Event()
    entered = threading.Event()
    errors: list[BaseException] = []

    def first(item) -> None:
        started.set()
        assert release.wait(timeout=5)
        item.cells[0].source = "SELECT 1"

    def second(item) -> None:
        entered.set()
        item.cells[1].source = "SELECT 2"

    def run(change) -> None:
        try:
            store.update(notebook.id, change)
        except BaseException as exc:
            errors.append(exc)

    left = threading.Thread(target=run, args=(first,))
    right = threading.Thread(target=run, args=(second,))
    left.start()
    assert started.wait(timeout=5)
    right.start()
    assert entered.wait(timeout=0.5) is False
    release.set()
    left.join(timeout=5)
    right.join(timeout=5)
    assert errors == []
    assert not left.is_alive()
    assert not right.is_alive()
    loaded = store.get(notebook.id)
    assert [cell.source for cell in loaded.cells] == ["SELECT 1", "SELECT 2"]


def test_sample_sql_cells_return_rows(lab_home, tmp_path: Path) -> None:
    notebook = NotebookStore(tmp_path).list()[0]
    for cell in notebook.cells:
        if cell.kind != "sql":
            continue
        result = run_query(cell.source)
        assert result.columns
        assert result.rows


@pytest.fixture
def client(lab_home, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    folder = tmp_path / "notebooks"
    folder.mkdir()
    monkeypatch.setattr("sclab.paths.notebooks_dir", lambda: folder)
    monkeypatch.setattr("sclab.web.notebook_routes.notebooks_dir", lambda: folder)
    test_client = TestClient(create_app(), base_url="http://127.0.0.1")
    test_client.headers.update({"HX-Request": "true"})
    return test_client


def _cell_form(page: str) -> dict[str, str]:
    match = re.search(r'<form id="(form-[^"]+)">(.*?)</form>', page, re.S)
    assert match is not None
    body = match.group(2)
    data: dict[str, str] = {}
    for tag in re.findall(r"<input\b[^>]*>", body):
        attrs = dict(re.findall(r'([\w:-]+)="([^"]*)"', tag))
        if "name" in attrs:
            data[attrs["name"]] = unescape(attrs.get("value", ""))
    area = re.search(r"<textarea\b([^>]*)>(.*?)</textarea>", body, re.S)
    assert area is not None
    attrs = dict(re.findall(r'([\w:-]+)="([^"]*)"', area.group(1)))
    assert attrs.get("name") == "source"
    data["source"] = unescape(area.group(2))
    selected = re.search(r"<option\b([^>]*)\bselected\b[^>]*>", body)
    if selected:
        option = dict(re.findall(r'([\w:-]+)="([^"]*)"', selected.group(1)))
        data["chart_type"] = unescape(option.get("value", ""))
    else:
        data["chart_type"] = ""
    return data


def _open_sample(client: TestClient) -> str:
    page = client.get("/notebooks/corvane-supply-review")
    assert page.status_code == 200
    return page.text


def test_run_sql_cell_returns_a_grid(client: TestClient) -> None:
    created = client.post("/notebooks", data={"title": "Grid"})
    assert created.status_code == 200
    notebook_id = created.headers["HX-Redirect"].rsplit("/", 1)[-1]
    page = client.get(f"/notebooks/{notebook_id}")
    cell_id = re.search(r'data-cell="([^"]+)"', page.text)
    assert cell_id is not None
    ran = client.post(
        f"/notebooks/{notebook_id}/cells/{cell_id.group(1)}/run",
        data={"source": "SELECT plant_code FROM plant ORDER BY 1"},
    )
    assert ran.status_code == 200
    assert "<table" in ran.text
    assert "plant_code" in ran.text
    assert ran.text.count("<tr") == 4
    assert "Download Excel" in ran.text


def test_cell_form_exports_a_spreadsheet(client: TestClient) -> None:
    created = client.post("/notebooks", data={"title": "Export"})
    notebook_id = created.headers["HX-Redirect"].rsplit("/", 1)[-1]
    page = client.get(f"/notebooks/{notebook_id}")
    cell_id = re.search(r'data-cell="([^"]+)"', page.text).group(1)
    sql = "SELECT plant_code FROM plant ORDER BY 1"
    saved = client.post(f"/notebooks/{notebook_id}/cells/{cell_id}", data={"source": sql})
    assert saved.status_code == 204
    posted = _cell_form(client.get(f"/notebooks/{notebook_id}").text)
    assert "sql" not in posted
    assert posted["source"] == sql
    assert posted["target"] == "lesson"
    response = client.post("/api/export", data=posted)
    assert response.status_code == 200
    assert response.headers["content-type"].split(";")[0] == _XLSX
    book = load_workbook(BytesIO(response.content))
    sheet = book.active
    assert sheet is not None
    assert [sheet[f"A{row}"].value for row in range(1, 5)] == [
        "plant_code",
        "AU01",
        "SC01",
        "SG01",
    ]


def test_lesson_write_is_refused_and_sandbox_write_runs(client: TestClient) -> None:
    sql = "CREATE TABLE nb_probe AS SELECT 1 AS n"
    with pytest.raises(QueryError) as caught:
        run_query(sql, target="lesson")
    created = client.post("/notebooks", data={"title": "Writes"})
    notebook_id = created.headers["HX-Redirect"].rsplit("/", 1)[-1]
    page = client.get(f"/notebooks/{notebook_id}")
    cell_id = re.search(r'data-cell="([^"]+)"', page.text).group(1)
    run_url = f"/notebooks/{notebook_id}/cells/{cell_id}/run"
    try:
        refused = client.post(run_url, data={"source": sql})
        assert refused.status_code == 200
        assert str(escape(caught.value.message)) in refused.text
        assert "<table" not in refused.text
        still = run_query("SELECT count(*) FROM plant")
        assert still.rows[0][0] == 3

        switched = client.post(f"/notebooks/{notebook_id}/target", data={"target": "sandbox"})
        assert switched.status_code == 204
        allowed = client.post(run_url, data={"source": sql})
        assert allowed.status_code == 200
        assert caught.value.message not in allowed.text
        probe = run_query("SELECT n FROM nb_probe", target="sandbox")
        assert probe.rows[0][0] == 1
    finally:
        reset_sandbox()


def test_chart_reload_does_not_write(client: TestClient) -> None:
    sql = "CREATE TABLE nb_chart_get_probe AS SELECT 1 AS n"
    created = client.post("/notebooks", data={"title": "Chart write"})
    notebook_id = created.headers["HX-Redirect"].rsplit("/", 1)[-1]
    client.post(f"/notebooks/{notebook_id}/target", data={"target": "sandbox"})
    page = client.get(f"/notebooks/{notebook_id}")
    cell_id = re.search(r'data-cell="([^"]+)"', page.text).group(1)
    saved = client.post(
        f"/notebooks/{notebook_id}/cells/{cell_id}",
        data={"source": sql, "chart_type": "bar", "chart_x": "n", "chart_y": "n"},
    )
    assert saved.status_code == 204
    reset_sandbox()
    try:
        loaded = client.get(f"/notebooks/{notebook_id}")
        assert loaded.status_code == 200
        with pytest.raises(QueryError):
            run_query("SELECT n FROM nb_chart_get_probe", target="sandbox")
    finally:
        reset_sandbox()


def test_chart_before_sandbox_exists_stays_plain(client: TestClient) -> None:
    created = client.post("/notebooks", data={"title": "No sandbox"})
    notebook_id = created.headers["HX-Redirect"].rsplit("/", 1)[-1]
    client.post(f"/notebooks/{notebook_id}/target", data={"target": "sandbox"})
    page = client.get(f"/notebooks/{notebook_id}")
    cell_id = re.search(r'data-cell="([^"]+)"', page.text).group(1)
    saved = client.post(
        f"/notebooks/{notebook_id}/cells/{cell_id}",
        data={"source": "SELECT 1 AS n", "chart_type": "bar", "chart_x": "n", "chart_y": "n"},
    )
    assert saved.status_code == 204
    sandbox = sandbox_db_path()
    sandbox.unlink()
    Path(f"{sandbox}.wal").unlink(missing_ok=True)
    try:
        loaded = client.get(f"/notebooks/{notebook_id}")
        assert loaded.status_code == 200
        message = "Run this cell to create the practice database and draw the chart."
        assert message in loaded.text
        assert "IO Error" not in loaded.text
        assert not sandbox.is_file()
    finally:
        reset_sandbox()


def test_chart_json_has_no_bare_nan(client: TestClient) -> None:
    sql = (
        "SELECT 'a' AS bucket, 'NaN'::DOUBLE AS amount "
        "UNION ALL SELECT 'b', 'Infinity'::DOUBLE "
        "UNION ALL SELECT 'c', '-Infinity'::DOUBLE "
        "UNION ALL SELECT 'd', 1.25::DOUBLE"
    )
    created = client.post("/notebooks", data={"title": "NaN"})
    notebook_id = created.headers["HX-Redirect"].rsplit("/", 1)[-1]
    page = client.get(f"/notebooks/{notebook_id}")
    cell_id = re.search(r'data-cell="([^"]+)"', page.text).group(1)
    saved = client.post(
        f"/notebooks/{notebook_id}/cells/{cell_id}",
        data={"source": sql, "chart_type": "line", "chart_x": "bucket", "chart_y": "amount"},
    )
    assert saved.status_code == 204
    reloaded = client.get(f"/notebooks/{notebook_id}")
    match = re.search(r'data-chart="([^"]*)"', reloaded.text)
    assert match is not None
    raw = unescape(match.group(1))
    assert "NaN" not in raw
    assert "Infinity" not in raw
    payload = json.loads(raw)
    assert payload["values"] == [None, None, None, 1.25]


def test_run_uses_the_form_target(client: TestClient, tmp_path: Path) -> None:
    sql = "CREATE TABLE nb_run_target_probe AS SELECT 1 AS n"
    created = client.post("/notebooks", data={"title": "Target"})
    notebook_id = created.headers["HX-Redirect"].rsplit("/", 1)[-1]
    page = client.get(f"/notebooks/{notebook_id}")
    cell_id = re.search(r'data-cell="([^"]+)"', page.text).group(1)
    reset_sandbox()
    try:
        ran = client.post(
            f"/notebooks/{notebook_id}/cells/{cell_id}/run",
            data={"source": sql, "target": "sandbox"},
        )
        assert ran.status_code == 200
        probe = run_query("SELECT n FROM nb_run_target_probe", target="sandbox")
        assert probe.rows[0][0] == 1
        stored = NotebookStore(tmp_path / "notebooks").get(notebook_id)
        assert stored.target == "lesson"
    finally:
        reset_sandbox()


def test_delete_without_htmx_header_is_refused(client: TestClient) -> None:
    created = client.post("/notebooks", data={"title": "Guard"})
    notebook_id = created.headers["HX-Redirect"].rsplit("/", 1)[-1]
    page = client.get(f"/notebooks/{notebook_id}")
    cell_id = re.search(r'data-cell="([^"]+)"', page.text).group(1)
    raw = TestClient(client.app, base_url="http://127.0.0.1")
    refused = raw.delete(f"/notebooks/{notebook_id}/cells/{cell_id}")
    assert refused.status_code == 403
    assert "Use the lab page to run that action." in refused.text
    again = client.get(f"/notebooks/{notebook_id}")
    assert f'data-cell="{cell_id}"' in again.text


def test_markdown_script_is_escaped(client: TestClient) -> None:
    created = client.post("/notebooks", data={"title": "Notes"})
    notebook_id = created.headers["HX-Redirect"].rsplit("/", 1)[-1]
    added = client.post(f"/notebooks/{notebook_id}/cells", data={"kind": "markdown"})
    assert added.status_code == 200
    cell_id = re.search(r'data-cell="([^"]+)"', added.text).group(1)
    saved = client.post(
        f"/notebooks/{notebook_id}/cells/{cell_id}",
        data={"source": f"Hello {_SCRIPT}"},
    )
    assert saved.status_code == 204
    page = client.get(f"/notebooks/{notebook_id}")
    assert page.status_code == 200
    assert _SCRIPT not in page.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page.text


def test_note_drops_external_images_and_links(client: TestClient) -> None:
    created = client.post("/notebooks", data={"title": "Links"})
    notebook_id = created.headers["HX-Redirect"].rsplit("/", 1)[-1]
    added = client.post(f"/notebooks/{notebook_id}/cells", data={"kind": "markdown"})
    cell_id = re.search(r'data-cell="([^"]+)"', added.text).group(1)
    note = "\n".join(
        [
            "![logo](https://evil.example/logo.png)",
            "",
            "[site](https://evil.example/docs)",
            "",
            "[sneaky](//evil.example/path)",
            "",
            "[local](/notebooks)",
        ]
    )
    saved = client.post(f"/notebooks/{notebook_id}/cells/{cell_id}", data={"source": note})
    assert saved.status_code == 204
    page = client.get(f"/notebooks/{notebook_id}")
    assert page.status_code == 200
    prose = re.findall(r'<div class="prose">(.*?)</div>', page.text, re.S)
    assert prose
    rendered = "\n".join(prose)
    assert "evil.example" not in rendered
    assert "<img" not in rendered
    assert "https://" not in rendered
    assert 'href="/notebooks"' in rendered
    assert ">local<" in rendered
    assert "site" in rendered
    assert "logo" in rendered
    outside_editor = re.sub(r"<textarea\b[^>]*>.*?</textarea>", "", page.text, flags=re.S)
    assert "evil.example" not in outside_editor
    assert "<img" not in page.text
    assert 'href="http' not in page.text
    assert 'href="//' not in page.text


def test_chart_is_saved_and_embedded(client: TestClient) -> None:
    created = client.post("/notebooks", data={"title": "Chart"})
    notebook_id = created.headers["HX-Redirect"].rsplit("/", 1)[-1]
    page = client.get(f"/notebooks/{notebook_id}")
    cell_id = re.search(r'data-cell="([^"]+)"', page.text).group(1)
    sql = "SELECT plant_code, COUNT(*) AS n FROM plant GROUP BY plant_code ORDER BY plant_code"
    saved = client.post(
        f"/notebooks/{notebook_id}/cells/{cell_id}",
        data={
            "source": sql,
            "chart_type": "bar",
            "chart_x": "plant_code",
            "chart_y": "n",
        },
    )
    assert saved.status_code == 204
    reloaded = client.get(f"/notebooks/{notebook_id}")
    assert reloaded.status_code == 200
    match = re.search(r'<canvas[^>]*data-chart="([^"]*)"', reloaded.text)
    assert match is not None
    payload = json.loads(unescape(match.group(1)))
    assert payload["type"] == "bar"
    assert payload["x"] == "plant_code"
    assert payload["y"] == "n"
    assert payload["labels"] == ["AU01", "SC01", "SG01"]
    assert payload["values"] == [1, 1, 1]
    assert 'value="bar" selected' in reloaded.text


def test_chart_survives_reload_from_the_store(client: TestClient, tmp_path: Path) -> None:
    created = client.post("/notebooks", data={"title": "Chart"})
    notebook_id = created.headers["HX-Redirect"].rsplit("/", 1)[-1]
    page = client.get(f"/notebooks/{notebook_id}")
    cell_id = re.search(r'data-cell="([^"]+)"', page.text).group(1)
    client.post(
        f"/notebooks/{notebook_id}/cells/{cell_id}",
        data={
            "source": "SELECT plant_code FROM plant ORDER BY 1",
            "chart_type": "line",
            "chart_x": "plant_code",
            "chart_y": "plant_code",
        },
    )
    files = [path for path in tmp_path.rglob("*.json") if ".corrupt-" not in path.name]
    assert len(files) == 1
    saved = json.loads(files[0].read_text(encoding="utf-8"))
    assert saved["cells"][0]["chart"] == {
        "type": "line",
        "x": "plant_code",
        "y": "plant_code",
    }


def test_move_and_delete_cells(client: TestClient) -> None:
    created = client.post("/notebooks", data={"title": "Order"})
    notebook_id = created.headers["HX-Redirect"].rsplit("/", 1)[-1]
    first = client.get(f"/notebooks/{notebook_id}")
    first_id = re.search(r'data-cell="([^"]+)"', first.text).group(1)
    added = client.post(f"/notebooks/{notebook_id}/cells", data={"kind": "sql"})
    second_id = re.search(r'data-cell="([^"]+)"', added.text).group(1)
    client.post(f"/notebooks/{notebook_id}/cells/{first_id}", data={"source": "SELECT 1"})
    client.post(f"/notebooks/{notebook_id}/cells/{second_id}", data={"source": "SELECT 2"})
    assert 'data-move="up"' in first.text
    moved = client.post(
        f"/notebooks/{notebook_id}/cells/{second_id}/move",
        data={"direction": "up"},
    )
    assert moved.status_code == 200
    assert moved.text.index(f'data-cell="{second_id}"') < moved.text.index(
        f'data-cell="{first_id}"'
    )
    removed = client.delete(f"/notebooks/{notebook_id}/cells/{first_id}")
    assert removed.status_code == 200
    page = client.get(f"/notebooks/{notebook_id}")
    assert f'data-cell="{first_id}"' not in page.text
    assert f'data-cell="{second_id}"' in page.text
    assert "SELECT 2" in page.text


def test_notebook_pages_stay_local(client: TestClient) -> None:
    assert _CHART_JS.is_file()
    assert _CHART_JS.stat().st_size > 1000
    script = _APP_JS.read_text(encoding="utf-8")
    assert "setTimeout(fire, 1000)" in script
    assert "data-autosave" in script
    handler = script.split('closest("[data-move]")', 1)[1]
    assert handler.index("flushAutosaves()") < handler.index("button.click()")
    assert "keptResults[node.id] = node.innerHTML" in script
    assert "node.innerHTML = prior" in script
    for path in _NOTEBOOK_TEMPLATES.glob("*.html"):
        assert _URL.search(path.read_text(encoding="utf-8")) is None, path.name
    index = client.get("/notebooks")
    assert index.status_code == 200
    assert _URL.search(index.text) is None
    page = _open_sample(client)
    assert "/static/vendor/chart.umd.min.js" in page
    assert _URL.search(page) is None
    assert "Corvane" in page
    assert "1 Nov 2026 00:00" in index.text
    assert 'hx-confirm="Delete this notebook?"' in page
    assert 'getPropertyValue("--accent")' in script
    assert 'getPropertyValue("--ink")' in script
    assert 'getPropertyValue("--line")' in script


def test_move_keeps_the_sql_textarea_hidden(client: TestClient) -> None:
    created = client.post("/notebooks", data={"title": "Move"})
    notebook_id = created.headers["HX-Redirect"].rsplit("/", 1)[-1]
    client.post(f"/notebooks/{notebook_id}/cells", data={"kind": "markdown"})
    page = client.get(f"/notebooks/{notebook_id}")
    moved = client.post(
        re.search(r'hx-post="([^"]+/move)"', page.text).group(1),
        data={"direction": "down"},
    )
    assert moved.status_code == 200
    for tag in re.findall(r"<textarea\b[^>]*>", moved.text):
        hidden_attr = re.search(r"\shidden(?:\s|>)", tag)
        if "data-editor" in tag:
            assert hidden_attr
        else:
            assert not hidden_attr


def test_notebook_export_error_returns_to_the_notebook(client: TestClient) -> None:
    created = client.post("/notebooks", data={"title": "Back"})
    notebook_id = created.headers["HX-Redirect"].rsplit("/", 1)[-1]
    posted = _cell_form(client.get(f"/notebooks/{notebook_id}").text)
    assert posted["return_to"] == f"/notebooks/{notebook_id}"
    for target in ("lesson", "sandbox"):
        response = client.post(
            "/api/export",
            data={
                "sql": "SELEC 1",
                "target": target,
                "return_to": posted["return_to"],
                "label": "bad",
            },
        )
        assert f'href="/notebooks/{notebook_id}"' in response.text


def test_delete_notebook_is_htmx_guarded(client: TestClient) -> None:
    created = client.post("/notebooks", data={"title": "Gone"})
    notebook_id = created.headers["HX-Redirect"].rsplit("/", 1)[-1]
    removed = client.delete(f"/notebooks/{notebook_id}")
    assert removed.status_code == 200
    assert removed.headers["HX-Redirect"] == "/notebooks"
    assert client.get(f"/notebooks/{notebook_id}").status_code == 404
    raw = TestClient(create_app(), base_url="http://127.0.0.1")
    refused = raw.delete(f"/notebooks/{notebook_id}")
    assert refused.status_code == 403
