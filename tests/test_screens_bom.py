"""CS03 BOM display and CS15 where-used."""

import re
import time
from decimal import Decimal

import duckdb
from fastapi.testclient import TestClient

from sclab.engine.query import run_query
from sclab.engine.types import QueryResult
from sclab.screens.bom import _EXPLODE, _MAX_DEPTH, render_level
from sclab.screens.framework import render_screen
from sclab.web.app import create_app

_TOOL = "M-000011"
_GENERATOR = "M-001321"
_OTHER_GENERATOR = "M-001324"
_GENERATOR_TOOLS = {"M-000003", "M-000009", "M-000011", "M-000017"}
_LEVELS = re.compile(r'data-level="(\d+)"')
_NO_COMPONENTS = "This material has no components."


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
    assert f"level < {_MAX_DEPTH}" in run.display_sql
    assert "{max_depth}" not in run.display_sql
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
    assert f"level &lt; {_MAX_DEPTH}" in page.text
    assert "Long lead" in page.text


def test_one_level_page_offers_expand_and_explode(lab_home) -> None:
    page = _client().get("/screens/CS03", params={"key": _TOOL})
    assert "Expand" in page.text
    assert "Explode all" in page.text
    assert "WITH RECURSIVE" in page.text
    assert 'data-level="' not in page.text


def _tools_by_independent_walk(component: str) -> set[str]:
    """Guarded walk written here, not taken from the screen SQL."""
    sql = """
    WITH RECURSIVE ancestors AS (
        SELECT parent_material_no AS found_no, 1 AS depth
        FROM bom_item
        WHERE component_material_no = $1
        UNION ALL
        SELECT edge.parent_material_no, ancestors.depth + 1
        FROM ancestors
        JOIN bom_item AS edge ON edge.component_material_no = ancestors.found_no
        WHERE ancestors.depth < 10
    )
    SELECT DISTINCT item.material_no
    FROM ancestors
    JOIN material AS item ON item.material_no = ancestors.found_no
    WHERE item.material_type = 'TOOL'
    """
    return {row[0] for row in run_query(sql, params=[component], max_rows=100_000).rows}


def _screen_where_used(component: str):
    data = render_screen("CS15", component, {})
    run = data.runs["used"]
    assert run.result is not None
    assert run.error is None
    assert run.result.truncated is False
    columns = {name: index for index, name in enumerate(run.result.columns)}
    return run, columns


def test_where_used_matches_an_independent_walk(lab_home) -> None:
    run, columns = _screen_where_used(_GENERATOR)
    tools = {
        row[columns["material_no"]]
        for row in run.result.rows
        if row[columns["material_type"]] == "TOOL"
    }
    assert tools == _tools_by_independent_walk(_GENERATOR)
    assert tools == _GENERATOR_TOOLS
    for row in run.result.rows:
        if row[columns["material_no"]] in tools:
            assert str(row[columns["path"]]).startswith(_GENERATOR)
    rf_on_screen = {
        row[columns["material_no"]]
        for row in run.result.rows
        if "RF delivery" in str(row[columns["description"]])
    }
    rf_direct = {
        row[0]
        for row in _rows(
            """
            SELECT parent.material_no
            FROM bom_item AS edge
            JOIN material AS parent ON parent.material_no = edge.parent_material_no
            WHERE edge.component_material_no = $1
              AND parent.description LIKE 'RF delivery%'
            """,
            [_GENERATOR],
        )
    }
    assert rf_on_screen
    assert rf_on_screen == rf_direct
    other, other_columns = _screen_where_used(_OTHER_GENERATOR)
    other_tools = {
        row[other_columns["material_no"]]
        for row in other.result.rows
        if row[other_columns["material_type"]] == "TOOL"
    }
    assert other_tools == _tools_by_independent_walk(_OTHER_GENERATOR)
    assert len(other_tools) == 3
    page = _client().get("/screens/CS15", params={"key": _GENERATOR})
    assert page.status_code == 200
    for number in _GENERATOR_TOOLS:
        assert number in page.text
    assert "WITH RECURSIVE" in page.text
    assert f"level &lt; {_MAX_DEPTH}" in page.text


def test_leaf_material_says_it_has_no_components(lab_home) -> None:
    assert _direct(_GENERATOR) == []
    client = _client()
    one_level = client.get("/screens/CS03", params={"key": _GENERATOR})
    exploded = client.get("/screens/CS03", params={"key": _GENERATOR, "view": "explode"})
    assert one_level.status_code == 200
    assert exploded.status_code == 200
    assert _NO_COMPONENTS in one_level.text
    assert _NO_COMPONENTS in exploded.text
    assert 'data-level="' not in exploded.text


def test_expand_guards_use_a_synthetic_bom(monkeypatch) -> None:
    connection = duckdb.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE material (
            material_no VARCHAR,
            description VARCHAR,
            material_type VARCHAR,
            is_long_lead BOOLEAN
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE bom_item (
            parent_material_no VARCHAR,
            component_material_no VARCHAR,
            qty_per DECIMAL(10, 3)
        )
        """
    )
    chain = [f"P{index}" for index in range(_MAX_DEPTH + 2)]
    materials = ["A", "B", *chain]
    connection.executemany(
        "INSERT INTO material VALUES (?, ?, 'COMPONENT', FALSE)",
        [(number, number) for number in materials],
    )
    edges = [("A", "B"), ("B", "A"), *list(zip(chain, chain[1:], strict=False))]
    connection.executemany(
        "INSERT INTO bom_item VALUES (?, ?, 1)",
        edges,
    )

    def run(sql: str, params=None, **_kwargs) -> QueryResult:
        bound = list(params) if params else None
        cursor = connection.execute(sql, bound) if bound is not None else connection.execute(sql)
        rows = [tuple(row) for row in cursor.fetchall()]
        columns = [item[0] for item in cursor.description]
        types = [str(item[1]) for item in cursor.description]
        return QueryResult(columns, rows, len(rows), False, 0, types)

    monkeypatch.setattr("sclab.screens.bom.run_query", run)
    open_cycle = render_level("A", "A")
    assert [row[0] for row in open_cycle["rows"]] == ["B"]
    repeated = render_level("A", "A/B/A")
    assert repeated["message"] == "This BOM path repeats a material."
    assert repeated["rows"] == []
    capped_path = "/".join(chain[:_MAX_DEPTH])
    capped = render_level(chain[_MAX_DEPTH - 1], capped_path)
    assert capped["capped"] is True
    assert [row[0] for row in capped["rows"]] == [chain[_MAX_DEPTH]]
    too_deep = render_level(chain[_MAX_DEPTH], "/".join(chain[: _MAX_DEPTH + 1]))
    assert too_deep["message"] == f"BOM expansion stops at level {_MAX_DEPTH}."
    assert too_deep["rows"] == []
    client = _client()
    repeat_page = client.get("/screens/CS03/level", params={"parent": "A", "path": "A/B/A"})
    assert repeat_page.status_code == 200
    assert "This BOM path repeats a material." in repeat_page.text
    assert ">B<" not in repeat_page.text
    deep_page = client.get(
        "/screens/CS03/level",
        params={"parent": chain[_MAX_DEPTH], "path": "/".join(chain[: _MAX_DEPTH + 1])},
    )
    assert f"BOM expansion stops at level {_MAX_DEPTH}." in deep_page.text
    assert chain[_MAX_DEPTH + 1] not in deep_page.text
    held = client.get(
        "/screens/CS03/level",
        params={"parent": chain[_MAX_DEPTH - 1], "path": capped_path},
    )
    assert chain[_MAX_DEPTH] in held.text
    assert "Expand" not in held.text
    exploded = connection.execute(_EXPLODE, [chain[0]]).fetchall()
    levels = {row[0] for row in exploded}
    assert max(levels) == _MAX_DEPTH
    assert chain[_MAX_DEPTH + 1] not in {row[1] for row in exploded}
    cycled = connection.execute(_EXPLODE, ["A"]).fetchall()
    assert cycled
    assert max(row[0] for row in cycled) == _MAX_DEPTH


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
