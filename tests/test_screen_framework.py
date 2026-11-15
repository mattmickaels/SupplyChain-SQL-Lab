"""Screen framework: bound parameters, display SQL, not-found, tcode box, read-only."""

from datetime import date

import duckdb
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
    summary="Sample plant display.",
)

_SCOPED = Screen(
    code="ZT02",
    title="Sample scoped display",
    key_label="Material",
    queries=(
        ScreenQuery(
            "item",
            "Material in plant",
            "SELECT material_no, plant_code FROM material_plant"
            " WHERE material_no = $1 AND plant_code = $2",
        ),
    ),
    exists_sql=("SELECT count(*) FROM material_plant WHERE material_no = $1 AND plant_code = $2"),
    not_found="Material {key} was not found.",
    params=("plant_code",),
    summary="Sample material in one plant.",
)

_BROKEN = Screen(
    code="ZT03",
    title="Sample broken display",
    key_label="Plant",
    queries=(),
    exists_sql="SELECT count(*) FROM no_such_table WHERE x = $1",
    not_found="Plant {key} was not found.",
    summary="Sample screen whose query fails.",
)


@pytest.fixture(autouse=True)
def sample_screen():
    register(_SAMPLE)
    register(_SCOPED)
    register(_BROKEN)


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
    plant = _known_plant()
    assert client.get("/screens/ZT01", params={"key": plant}).text.count("Show the SQL") == 1
    hostile = render_screen("ZT01", plant + "' OR '1'='1", {})
    assert hostile.found is False
    assert inline_params(_SAMPLE.queries[0].sql, [key]).count("'M-1'' OR ''1''=''1'") == 1


def test_inline_params_literals() -> None:
    sql = "SELECT $1, $2, $3, $4, $1"
    shown = inline_params(sql, ["O'Neil", date(2026, 11, 1), 7, None])
    assert shown == "SELECT 'O''Neil', DATE '2026-11-01', 7, NULL, 'O''Neil'"


def test_inline_params_skips_strings_comments_identifiers() -> None:
    assert inline_params("SELECT '$1', $1", ["x"]) == "SELECT '$1', 'x'"
    sql = 'SELECT "a$1", $1 -- $2\n'
    assert inline_params(sql, ["x"]) == "SELECT \"a$1\", 'x' -- $2\n"
    assert inline_params("SELECT $1 + $1", [2]) == "SELECT 2 + 2"


def test_inline_params_nan_and_inf_are_valid_sql() -> None:
    shown = inline_params("SELECT $1, $2, $3", [float("nan"), float("inf"), float("-inf")])
    assert shown == (
        "SELECT CAST('NaN' AS DOUBLE), CAST('Infinity' AS DOUBLE), CAST('-Infinity' AS DOUBLE)"
    )
    row = duckdb.connect().execute(shown).fetchone()
    assert row[0] != row[0]
    assert row[1] == float("inf")
    assert row[2] == float("-inf")


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
            statements = duckdb.extract_statements(sql)
            assert len(statements) == 1, screen.code
            assert statements[0].type == duckdb.StatementType.SELECT, screen.code


def test_unknown_screen_is_404(client) -> None:
    assert client.get("/screens/NOPE").status_code == 404
    with pytest.raises(KeyError):
        get_screen("NOPE")


def _scoped_key() -> tuple[str, str]:
    return run_query(
        "SELECT material_no, plant_code FROM material_plant ORDER BY 1, 2 LIMIT 1"
    ).rows[0]


def test_declared_params_ignore_stray_values(lab_home) -> None:
    material, plant = _scoped_key()
    data = render_screen("ZT02", material, {"aaa": "1", "plant_code": plant, "zzz": "2"})
    assert data.found is True
    assert data.runs["item"].result.rows == [(material, plant)]
    assert plant in data.runs["item"].display_sql


def test_missing_declared_param_asks_for_it(lab_home) -> None:
    material, _ = _scoped_key()
    data = render_screen("ZT02", material, {"aaa": "1"})
    assert data.found is False
    assert data.message == "Enter a plant code."


def test_declared_params_bind_in_declared_order(lab_home) -> None:
    multi = Screen(
        code="ZT04",
        title="Order",
        key_label="Key",
        queries=(ScreenQuery("q", "Order", "SELECT $1 AS a, $2 AS b, $3 AS c"),),
        exists_sql="SELECT 1",
        not_found="{key}",
        params=("zeta", "alpha"),
        summary="Sample screen with two extra parameters.",
    )
    register(multi)
    data = render_screen("ZT04", "k", {"alpha": "A", "zeta": "Z", "extra": "x"})
    assert data.runs["q"].result.rows == [("k", "Z", "A")]


def test_exists_error_shows_message_not_not_found(client) -> None:
    data = render_screen("ZT03", "SC01", {})
    assert data.found is False
    assert "no_such_table" in data.message
    assert "was not found" not in data.message
    assert "no_such_table" in client.get("/screens/ZT03", params={"key": "SC01"}).text


def test_empty_key_message(client) -> None:
    response = client.get("/screens/ZT01")
    assert response.status_code == 200
    assert "Enter a plant." in response.text


def test_default_template_renders_rows(client) -> None:
    plant = _known_plant()
    text = client.get("/screens/ZT01", params={"key": plant}).text
    assert "<table" in text
    assert f"<td>{plant}</td>" in text


def test_inline_params_handles_non_ascii_before_placeholder() -> None:
    assert inline_params("SELECT 'é', $1", ["x"]) == "SELECT 'é', 'x'"
    sql = "SELECT 1 -- café\nWHERE a = 'a–b' AND b = $2"
    assert inline_params(sql, ["x", "y"]) == "SELECT 1 -- café\nWHERE a = 'a–b' AND b = 'y'"
    assert inline_params("SELECT $1, 'é'", ["Zürich"]) == "SELECT 'Zürich', 'é'"


def test_render_screen_with_non_ascii_literal_before_param(lab_home) -> None:
    plant = _known_plant()
    screen = Screen(
        code="ZT05",
        title="Unicode",
        key_label="Plant",
        queries=(
            ScreenQuery(
                "u",
                "Unicode",
                "SELECT 'café – Zürich' AS label, plant_code FROM plant WHERE plant_code = $1",
            ),
        ),
        exists_sql="SELECT count(*) FROM plant WHERE plant_code = $1",
        not_found="Plant {key} was not found.",
        summary="Sample screen with a non-ASCII literal.",
    )
    register(screen)
    data = render_screen("ZT05", plant, {})
    run = data.runs["u"]
    assert run.error is None
    assert run.result.rows == [("café – Zürich", plant)]
    assert f"plant_code = '{plant}'" in run.display_sql
    assert "café – Zürich" in run.display_sql
    assert run_query(run.display_sql).rows == run.result.rows
