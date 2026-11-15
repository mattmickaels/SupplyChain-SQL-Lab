"""Notebook store, sample copy, and notebook pages."""

import json
import re
from html import unescape
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from markupsafe import escape

from sclab.engine.query import QueryError, reset_sandbox, run_query
from sclab.notebook.store import Cell, NotebookStore
from sclab.paths import notebooks_dir
from sclab.web.app import create_app

_ROOT = Path(__file__).resolve().parents[1]
_NOTEBOOK_TEMPLATES = _ROOT / "src" / "sclab" / "web" / "templates" / "notebook"
_APP_JS = _ROOT / "src" / "sclab" / "static" / "app.js"
_CHART_JS = _ROOT / "src" / "sclab" / "static" / "vendor" / "chart.umd.min.js"
_URL = re.compile(r"https?://")
_SCRIPT = "<script>alert(1)</script>"


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
    assert names == [f"{created.id}.json"]

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
    for path in _NOTEBOOK_TEMPLATES.glob("*.html"):
        assert _URL.search(path.read_text(encoding="utf-8")) is None, path.name
    index = client.get("/notebooks")
    assert index.status_code == 200
    assert _URL.search(index.text) is None
    page = _open_sample(client)
    assert "/static/vendor/chart.umd.min.js" in page
    assert _URL.search(page) is None
    assert "Corvane" in page
