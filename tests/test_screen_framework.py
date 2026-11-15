"""Screen framework: bound parameters, display SQL, not-found, tcode box, read-only."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from sclab.engine.query import run_query
from sclab.screens.framework import (
    Screen,
    ScreenQuery,
    all_screens,
    get_screen,
    inline_params,
    register,
    render_screen,
)
from sclab.web.app import create_app

_SAMPLE = Screen(
    code="ZT01",
    title="Sample plant display",
    key_label="Plant",
    queries=(
        ScreenQuery(
            "header",
            "Plant header",
            "SELECT plant_code FROM plant WHERE plant_code = $1 ORDER BY 1",
        ),
    ),
    exists_sql="SELECT count(*) FROM plant WHERE plant_code = $1",
    not_found="Plant {key} was not found.",
)


@pytest.fixture(autouse=True)
def sample_screen():
    register(_SAMPLE)


@pytest.fixture
def client(lab_home):
    test_client = TestClient(create_app(include_sample=True), base_url="http://127.0.0.1")
    test_client.headers.update({"HX-Request": "true"})
    return test_client


def _known_plant() -> str:
    return run_query("SELECT min(plant_code) FROM plant").rows[0][0]


def test_params_bound_not_interpolated(client) -> None:
    key = "M-1' OR '1'='1"
    data = render_screen("ZT01", key, {})
    assert data.found is False
    response = client.get("/screens/ZT01", params={"key": key})
    assert response.status_code == 200
    assert "was not found" in response.text


def test_inline_params_literals() -> None:
    sql = "SELECT $1, $2, $3, $4, $1"
    shown = inline_params(sql, ["O'Neil", date(2026, 11, 1), 7, None])
    assert shown == "SELECT 'O''Neil', DATE '2026-11-01', 7, NULL, 'O''Neil'"


def test_displayed_sql_reproduces_rows(lab_home) -> None:
    plant = _known_plant()
    data = render_screen("ZT01", plant, {})
    run = data.runs["header"]
    again = run_query(run.display_sql)
    assert run.result.rows
    assert again.rows == run.result.rows
    assert again.columns == run.result.columns


def test_not_found_message(client) -> None:
    response = client.get("/screens/ZT01", params={"key": "ZZ99"})
    assert response.status_code == 200
    assert "Plant ZZ99 was not found." in response.text


def test_found_screen_shows_sql_panel(client) -> None:
    plant = _known_plant()
    response = client.get("/screens/ZT01", params={"key": plant})
    assert response.status_code == 200
    assert "Show the SQL" in response.text
    assert "Open in playground" in response.text
    assert f"plant_code = &#39;{plant}&#39;" in response.text


def test_tcode_box_redirects(client) -> None:
    response = client.get(
        "/screens/go", params={"tcode": "zt01", "key": "SC01"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/screens/ZT01?key=SC01"
    unknown = client.get("/screens/go", params={"tcode": "NOPE"}, follow_redirects=False)
    assert unknown.status_code == 303
    assert unknown.headers["location"].startswith("/screens?")


def test_index_lists_and_searches(client) -> None:
    assert "ZT01" in client.get("/screens").text
    assert "ZT01" not in client.get("/screens", params={"q": "QQQ"}).text


def test_screen_never_writes() -> None:
    for screen in all_screens():
        sqls = [screen.exists_sql, *(query.sql for query in screen.queries)]
        for sql in sqls:
            assert sql.lstrip().upper().startswith(("SELECT", "WITH")), screen.code


def test_unknown_screen_is_404(client) -> None:
    assert client.get("/screens/NOPE").status_code == 404
    with pytest.raises(KeyError):
        get_screen("NOPE")
