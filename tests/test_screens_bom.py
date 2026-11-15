"""CS03 BOM display and CS15 where-used."""

import re
import time
from decimal import Decimal

from fastapi.testclient import TestClient

from sclab.engine.query import run_query
from sclab.screens.bom import render_level
from sclab.screens.framework import render_screen
from sclab.web.app import create_app

_TOOL = "M-000011"
_GENERATOR = "M-001321"
_LEVELS = re.compile(r'data-level="(\d+)"')


def _client() -> TestClient:
    test_client = TestClient(create_app(), base_url="http://127.0.0.1")
    test_client.headers.update({"HX-Request": "true"})
    return test_client


def _rows(sql: str, params: list[object] | None = None) -> list[tuple]:
    return run_query(sql, params=params).rows


def _direct(parent: str) -> list[tuple]:
    return _rows(
        "SELECT component_material_no, qty_per FROM bom_item"
        " WHERE parent_material_no = $1 ORDER BY component_material_no",
        [parent],
    )


def test_direct_children_match_bom_item(lab_home) -> None:
    data = render_screen("CS03", _TOOL, {})
    shown = data.runs["children"].result
    assert shown is not None
    assert [(row[0], row[2]) for row in shown.rows] == _direct(_TOOL)
    page = _client().get("/screens/CS03", params={"key": _TOOL})
    assert page.status_code == 200
    direct = {row[0] for row in _direct(_TOOL)}
    assert direct
    for number in direct:
        assert number in page.text
    grandchildren = {
        row[0]
        for row in _rows(
            """
            SELECT g.component_material_no
            FROM bom_item AS c
            JOIN bom_item AS g ON g.parent_material_no = c.component_material_no
            WHERE c.parent_material_no = $1
            """,
            [_TOOL],
        )
    }
    leaked = grandchildren - direct - {_TOOL}
    assert leaked
    assert all(number not in page.text for number in leaked)


def test_expand_returns_only_that_parents_children(lab_home) -> None:
    child = _direct(_TOOL)[0][0]
    page = _client().get(
        "/screens/CS03/level",
        params={"parent": child, "path": f"{_TOOL}/{child}"},
    )
    assert page.status_code == 200
    assert "Kestrel ERP" not in page.text
    expected = {row[0] for row in _direct(child)}
    assert expected
    for number in expected:
        assert number in page.text
    deeper = {
        row[0]
        for row in _rows(
            """
            SELECT g.component_material_no
            FROM bom_item AS c
            JOIN bom_item AS g ON g.parent_material_no = c.component_material_no
            WHERE c.parent_material_no = $1
            """,
            [child],
        )
    }
    hidden = deeper - expected - {child, _TOOL}
    assert hidden
    assert all(number not in page.text for number in hidden)
    hostile = _client().get(
        "/screens/CS03/level",
        params={"parent": child, "path": f"{_TOOL}/{child}' OR '1'='1"},
    )
    assert hostile.status_code == 200
    assert all(number not in hostile.text for number in hidden)


def test_explode_all_shows_four_levels_and_extended_qty(lab_home) -> None:
    data = render_screen("CS03", _TOOL, {})
    run = data.runs["explode"]
    assert run.result is not None
    assert run.error is None
    assert run.result.truncated is False
    columns = {name: index for index, name in enumerate(run.result.columns)}
    levels = {row[columns["level"]] for row in run.result.rows}
    assert levels == {0, 1, 2, 3}
    assert "level < 10" in run.display_sql
    qty = {
        (parent, component): Decimal(amount)
        for parent, component, amount in run_query(
            "SELECT parent_material_no, component_material_no, qty_per FROM bom_item",
            max_rows=100_000,
        ).rows
    }
    for row in run.result.rows:
        nodes = [part.strip() for part in str(row[columns["path"]]).split("/")]
        assert nodes[-1] == row[columns["material_no"]]
        assert row[columns["level"]] == len(nodes) - 1
        product = Decimal("1.000000")
        for parent, component in zip(nodes, nodes[1:], strict=False):
            product = (product * qty[(parent, component)]).quantize(Decimal("0.000001"))
        assert Decimal(row[columns["extended_qty"]]).quantize(Decimal("0.000001")) == product
    started = time.perf_counter()
    page = _client().get("/screens/CS03", params={"key": _TOOL, "view": "explode"})
    assert time.perf_counter() - started < 2
    assert page.status_code == 200
    assert "Show one level" in page.text
    assert set(_LEVELS.findall(page.text)) == {"0", "1", "2", "3"}
    assert "WITH RECURSIVE" in page.text
    assert "level &lt; 10" in page.text
    assert "Long lead" in page.text


def test_one_level_page_offers_expand_and_explode(lab_home) -> None:
    page = _client().get("/screens/CS03", params={"key": _TOOL})
    assert "Expand" in page.text
    assert "Explode all" in page.text
    assert "WITH RECURSIVE" in page.text
    assert 'data-level="' not in page.text


def test_where_used_lists_etch_tools_and_path(lab_home) -> None:
    data = render_screen("CS15", _GENERATOR, {})
    run = data.runs["used"]
    assert run.result is not None
    assert run.result.truncated is False
    columns = {name: index for index, name in enumerate(run.result.columns)}
    etch = [
        row
        for row in run.result.rows
        if row[columns["material_type"]] == "TOOL"
        and str(row[columns["description"]]).startswith("Etch")
    ]
    assert etch
    for row in etch:
        assert str(row[columns["path"]]).startswith(_GENERATOR)
        assert row[columns["level"]] >= 1
    page = _client().get("/screens/CS15", params={"key": _GENERATOR})
    assert page.status_code == 200
    for row in etch:
        assert row[columns["material_no"]] in page.text
    assert "WITH RECURSIVE" in page.text
    assert "level &lt; 10" in page.text
    names = {name for _, name in enumerate(run.result.columns)}
    assert "path" in names


def test_missing_material_is_not_found(lab_home) -> None:
    client = _client()
    for code in ("CS03", "CS15"):
        page = client.get(f"/screens/{code}", params={"key": "M-999999"})
        assert page.status_code == 200
        assert "Material M-999999 was not found." in page.text
        hostile = client.get(f"/screens/{code}", params={"key": "M-1' OR '1'='1"})
        assert hostile.status_code == 200
        assert "was not found" in hostile.text
        assert "Parser" not in hostile.text
    level = client.get("/screens/CS03/level", params={"parent": "M-999999", "path": "M-999999"})
    assert level.status_code == 200
    assert "Material M-999999 was not found." in level.text
    assert render_level("M-999999", "M-999999")["message"] == "Material M-999999 was not found."


def test_displayed_sql_reproduces_rows(lab_home) -> None:
    screens = (("CS03", _TOOL), ("CS15", _GENERATOR))
    for code, key in screens:
        data = render_screen(code, key, {})
        assert data.found is True
        for run in data.runs.values():
            assert run.result is not None
            assert run.error is None
            again = run_query(run.display_sql)
            assert again.columns == run.result.columns
            assert again.rows == run.result.rows


def test_long_lead_badge_on_expanded_component(lab_home) -> None:
    parent = _rows(
        "SELECT parent_material_no FROM bom_item"
        " WHERE component_material_no = $1 ORDER BY parent_material_no LIMIT 1",
        [_GENERATOR],
    )[0][0]
    page = _client().get(
        "/screens/CS03/level",
        params={"parent": parent, "path": f"{_TOOL}/M-000111/{parent}"},
    )
    assert _GENERATOR in page.text
    assert "Long lead" in page.text
    assert "Expand" not in page.text
