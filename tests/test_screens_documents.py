"""Document screens: material, supplier, purchase order and sales order."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_story import RISK_ROWS_SQL

from sclab.data.story import CORVANE_SUPPLIER_NO, HALCYON_CUSTOMER_NO
from sclab.engine.query import run_query
from sclab.screens.framework import get_screen, render_screen
from sclab.web.app import create_app

_CODES = ("MM03", "XK03", "ME23N", "VA03")


def test_four_screens_are_registered() -> None:
    import sclab.screens  # noqa: F401

    assert get_screen("MM03").title == "Display Material"
    assert get_screen("XK03").title == "Display Supplier"
    assert get_screen("ME23N").title == "Display Purchase Order"
    assert get_screen("VA03").title == "Display Sales Order"
    assert get_screen("MM03").params == ("plant",)
    assert get_screen("MD04").params == ("plant",)


@pytest.fixture
def client(lab_home):
    test_client = TestClient(create_app(), base_url="http://127.0.0.1")
    return test_client


def _rf_at_sc01() -> str:
    rows = run_query(
        """
        SELECT m.material_no
        FROM material AS m
        JOIN material_supplier AS ms
          ON ms.material_no = m.material_no AND ms.supplier_no = 'V-10017'
        JOIN purchase_order_item AS i ON i.material_no = m.material_no
        JOIN purchase_order AS h ON h.po_no = i.po_no
        LEFT JOIN (
            SELECT po_no, line_no, SUM(qty) AS received_qty
            FROM goods_receipt
            GROUP BY po_no, line_no
        ) AS r ON r.po_no = i.po_no AND r.line_no = i.line_no
        WHERE m.description LIKE 'RF generator,%'
          AND h.supplier_no = 'V-10017'
          AND h.plant_code = 'SC01'
          AND h.order_date <= DATE '2026-11-01'
          AND i.order_qty > COALESCE(r.received_qty, 0)
        ORDER BY m.material_no
        LIMIT 1
        """
    ).rows
    assert rows, "expected an open Corvane RF generator line at SC01"
    return rows[0][0]


def _open_corvane_po() -> str:
    rows = run_query(
        """
        SELECT h.po_no
        FROM purchase_order AS h
        JOIN purchase_order_item AS i ON i.po_no = h.po_no
        LEFT JOIN (
            SELECT po_no, line_no, SUM(qty) AS received_qty
            FROM goods_receipt
            GROUP BY po_no, line_no
        ) AS r ON r.po_no = i.po_no AND r.line_no = i.line_no
        WHERE h.supplier_no = 'V-10017'
          AND h.order_date <= DATE '2026-11-01'
          AND i.order_qty > COALESCE(r.received_qty, 0)
        ORDER BY h.po_no
        LIMIT 1
        """
    ).rows
    assert rows, "expected an open Corvane purchase order"
    return rows[0][0]


def _at_risk_sales_order() -> str:
    rows = run_query(
        f"""
        SELECT q.so_no
        FROM ({RISK_ROWS_SQL}) AS q
        JOIN sales_order AS h ON h.so_no = q.so_no
        ORDER BY CASE WHEN h.customer_no = '{HALCYON_CUSTOMER_NO}' THEN 0 ELSE 1 END, q.so_no
        LIMIT 1
        """
    ).rows
    assert rows, "expected an at-risk Etch sales order"
    return rows[0][0]


def test_mm03_renders_corvane_rf_generator(client) -> None:
    material = _rf_at_sc01()
    response = client.get("/screens/MM03", params={"key": material, "plant": "SC01"})
    assert response.status_code == 200
    text = response.text
    assert "Display Material" in text
    assert "RF generator," in text
    assert "SC01" in text
    assert f"/screens/XK03?key={CORVANE_SUPPLIER_NO}" in text
    assert "/screens/ME23N?key=" in text
    assert "Show the SQL" in text
    data = render_screen("MM03", material, {"plant": "SC01"})
    assert data.found is True
    sources = data.runs["sources"].result.rows
    assert sources
    assert {row[0] for row in sources} == {CORVANE_SUPPLIER_NO}
    assert data.runs["open_po"].result.rows


def test_mm03_needs_a_plant_and_unknown_material(client) -> None:
    material = _rf_at_sc01()
    missing_plant = render_screen("MM03", material, {})
    assert missing_plant.found is False
    assert missing_plant.message == "Enter a plant."
    response = client.get("/screens/MM03", params={"key": "M-999999", "plant": "SC01"})
    assert response.status_code == 200
    assert "Material M-999999 was not found." in response.text
    hostile = client.get("/screens/MM03", params={"key": "M-1' OR '1'='1", "plant": "SC01"})
    assert hostile.status_code == 200
    assert "was not found" in hostile.text


def test_xk03_renders_corvane_and_shows_the_slip(client) -> None:
    response = client.get("/screens/XK03", params={"key": CORVANE_SUPPLIER_NO})
    assert response.status_code == 200
    text = response.text
    assert "Corvane RF Systems" in text
    assert "On-time rate by quarter" in text
    assert "/screens/MM03?key=" in text
    assert "/screens/ME23N?key=" in text
    data = render_screen("XK03", CORVANE_SUPPLIER_NO, {})
    assert data.found is True
    assert data.runs["materials"].result.rows
    assert data.runs["open_po"].result.rows
    assert "2026 Q4 covers October only, up to the lab&#39;s today." in text
    assert "On-time rate, last 90 days" in text
    rows = data.runs["on_time"].result.rows
    assert len(rows) == 8
    assert [(row[1], row[2]) for row in rows[-2:]] == [(2026, 3), (2026, 4)]
    # Lesson 5 pins these rates. Q3 stays at 53.6 because July receipts are still on time.
    # Q4 is October only, through the day before the lab's today.
    assert rows[-2][3] == 28
    assert float(rows[-2][4]) == 53.6
    assert rows[-1][3] == 2
    assert float(rows[-1][4]) < 45
    assert float(rows[-3][4]) > float(rows[-2][4])
    quarters_2025 = [row for row in rows if row[1] == 2025]
    assert quarters_2025
    assert all(float(row[4]) >= 90 for row in quarters_2025)
    recent = data.runs["on_time_90"].result.rows
    assert len(recent) == 1
    assert recent[0][0] > 0
    assert float(recent[0][1]) < 45
    assert "DATE '2025-01-01'" in data.runs["on_time"].display_sql
    assert "DATE '2026-11-01'" in data.runs["on_time"].display_sql
    assert "DATE '2026-11-01' - INTERVAL 90 DAY" in data.runs["on_time_90"].display_sql
    missing = client.get("/screens/XK03", params={"key": "V-99999"})
    assert missing.status_code == 200
    assert "Supplier V-99999 was not found." in missing.text


def test_me23n_renders_open_corvane_po(client) -> None:
    po_no = _open_corvane_po()
    response = client.get("/screens/ME23N", params={"key": po_no})
    assert response.status_code == 200
    text = response.text
    assert "Display Purchase Order" in text
    assert f"/screens/XK03?key={CORVANE_SUPPLIER_NO}" in text
    assert "/screens/MM03?key=" in text
    assert "&amp;plant=" in text
    data = render_screen("ME23N", po_no, {})
    assert data.found is True
    assert data.runs["header"].result.rows[0][1] == CORVANE_SUPPLIER_NO
    items = data.runs["items"].result.rows
    assert items
    assert any(row[-1] > 0 for row in items)
    missing = client.get("/screens/ME23N", params={"key": "PO-9999999"})
    assert "Purchase order PO-9999999 was not found." in missing.text


def test_va03_renders_at_risk_sales_order(client) -> None:
    so_no = _at_risk_sales_order()
    response = client.get("/screens/VA03", params={"key": so_no})
    assert response.status_code == 200
    text = response.text
    assert "Display Sales Order" in text
    assert "/screens/MM03?key=" in text
    assert "&amp;plant=" in text
    assert 'class="drill"' in text
    assert "/screens/CO03" not in text
    data = render_screen("VA03", so_no, {})
    assert data.found is True
    assert data.runs["items"].result.rows
    prod_order = next(row[8] for row in data.runs["items"].result.rows if row[8])
    assert prod_order in text
    missing = client.get("/screens/VA03", params={"key": "SO-9999999"})
    assert "Sales order SO-9999999 was not found." in missing.text


def test_displayed_sql_reproduces_rows(lab_home) -> None:
    material = _rf_at_sc01()
    cases = (
        ("MM03", material, {"plant": "SC01"}),
        ("XK03", CORVANE_SUPPLIER_NO, {}),
        ("ME23N", _open_corvane_po(), {}),
        ("VA03", _at_risk_sales_order(), {}),
    )
    for code, key, extra in cases:
        data = render_screen(code, key, extra)
        assert data.found is True, code
        for name, run in data.runs.items():
            assert run.error is None, (code, name, run.error)
            assert "$1" not in run.display_sql
            again = run_query(run.display_sql)
            assert again.columns == run.result.columns
            assert again.rows == run.result.rows


def _spare_sales_line() -> tuple[str, str]:
    rows = run_query(
        """
        SELECT h.so_no, i.material_no
        FROM sales_order_item AS i
        JOIN sales_order AS h ON h.so_no = i.so_no
        JOIN material AS m ON m.material_no = i.material_no
        LEFT JOIN production_order AS p
          ON p.so_no = i.so_no AND p.so_line_no = i.line_no
        WHERE m.material_type = 'COMPONENT'
          AND p.prod_order_no IS NULL
        ORDER BY h.so_no, i.line_no
        LIMIT 1
        """
    ).rows
    assert rows, "expected a spare-part sales line with no production order"
    return rows[0]


def test_va03_spare_material_link_opens_mm03(client) -> None:
    so_no, material = _spare_sales_line()
    text = client.get("/screens/VA03", params={"key": so_no}).text
    marker = f"/screens/MM03?key={material}&amp;plant="
    assert marker in text
    href = text[text.index(marker) : text.index('"', text.index(marker))].replace("&amp;", "&")
    opened = client.get(href)
    assert opened.status_code == 200
    assert "Enter a plant." not in opened.text
    assert "was not found" not in opened.text
    assert "Plant data" in opened.text


def test_planning_screens_are_reachable(client) -> None:
    for code in ("MD04", "CM01"):
        response = client.get(f"/screens/{code}")
        assert response.status_code == 200
        assert code in response.text


def test_screens_index_lists_document_screens(client) -> None:
    text = client.get("/screens").text
    for code in _CODES:
        assert code in text
    assert "MM03" not in client.get("/screens", params={"q": "VA03"}).text
    assert "Weekly load of each build bay." in text
    home = client.get("/").text
    assert 'class="schema-list screen-list"' in home
    assert 'class="schema-list screen-list"' in text
    css = (Path(__file__).resolve().parents[1] / "src" / "sclab" / "static" / "app.css").read_text(
        encoding="utf-8"
    )
    assert "grid-template-columns: 6.5ch 28ch minmax(0, 1fr)" in css


def test_document_sql_stays_on_the_data() -> None:
    import sclab.screens  # noqa: F401

    for code in ("XK03", "VA03", "MM03", "ME23N"):
        for query in get_screen(code).queries:
            assert "tool_of" not in query.sql
            assert "bay_choice" not in query.sql
    items = next(query for query in get_screen("VA03").queries if query.name == "items")
    assert "p.plant_code" in items.sql
    assert "spare_plant" not in items.sql


def test_supplier_with_no_recent_receipts_says_so(client) -> None:
    page = client.get("/screens/XK03", params={"key": "V-10022"})
    assert page.status_code == 200
    assert "No receipts in the last 90 days." in page.text


def test_header_uses_plain_labels(client) -> None:
    po_no = run_query("SELECT po_no FROM purchase_order ORDER BY po_no LIMIT 1").rows[0][0]
    page = client.get("/screens/ME23N", params={"key": po_no})
    assert "PO number" in page.text
    assert "Po Number" not in page.text
    material, plant = run_query(
        """
        SELECT material_no, plant_code
        FROM material_plant
        ORDER BY material_no, plant_code
        LIMIT 1
        """
    ).rows[0]
    material_page = client.get("/screens/MM03", params={"key": material, "plant": plant})
    assert "Base unit" in material_page.text
    assert "Long lead" in material_page.text
    empty_page = client.get("/screens/MM03").text
    # Check the material prompt, not the workbench's unrelated hidden warnings.
    assert '<p class="screen-prompt">Enter a material.</p>' in empty_page


def test_mm03_planned_lead_time_shows_days(client) -> None:
    material, plant, days = run_query(
        """
        SELECT material_no, plant_code, planned_lead_time_days
        FROM material_plant
        WHERE planned_lead_time_days IS NOT NULL
        ORDER BY material_no, plant_code
        LIMIT 1
        """
    ).rows[0]
    page = client.get("/screens/MM03", params={"key": material, "plant": plant})
    assert f"{int(days)} days" in page.text


def test_every_open_spare_line_uses_the_md04_plant(client) -> None:
    from sclab.screens.plants import link_plants

    rows = run_query(
        """
        SELECT h.so_no, i.material_no,
               (
                   SELECT MIN(CASE tool.family_code
                                  WHEN 'ETC' THEN 'SC01'
                                  WHEN 'CMP' THEN 'SG01'
                                  ELSE 'AU01' END)
                   FROM (
                       SELECT b0.parent_material_no AS tool_no
                       FROM bom_item AS b0
                       WHERE b0.component_material_no = i.material_no
                       UNION
                       SELECT b1.parent_material_no
                       FROM bom_item AS b0
                       JOIN bom_item AS b1
                         ON b1.component_material_no = b0.parent_material_no
                       WHERE b0.component_material_no = i.material_no
                       UNION
                       SELECT b2.parent_material_no
                       FROM bom_item AS b0
                       JOIN bom_item AS b1
                         ON b1.component_material_no = b0.parent_material_no
                       JOIN bom_item AS b2
                         ON b2.component_material_no = b1.parent_material_no
                       WHERE b0.component_material_no = i.material_no
                       UNION
                       SELECT b3.parent_material_no
                       FROM bom_item AS b0
                       JOIN bom_item AS b1
                         ON b1.component_material_no = b0.parent_material_no
                       JOIN bom_item AS b2
                         ON b2.component_material_no = b1.parent_material_no
                       JOIN bom_item AS b3
                         ON b3.component_material_no = b2.parent_material_no
                       WHERE b0.component_material_no = i.material_no
                   ) AS hit
                   JOIN material AS tool
                     ON tool.material_no = hit.tool_no AND tool.material_type = 'TOOL'
               ) AS spare_plant
        FROM sales_order_item AS i
        JOIN sales_order AS h ON h.so_no = i.so_no
        JOIN material AS m ON m.material_no = i.material_no
        LEFT JOIN production_order AS p
          ON p.so_no = i.so_no AND p.so_line_no = i.line_no
        WHERE m.material_type = 'COMPONENT'
          AND i.status = 'Open'
          AND i.promised_date > DATE '2026-11-01'
          AND p.prod_order_no IS NULL
        ORDER BY h.so_no, i.line_no
        """
    ).rows
    assert rows
    assert ("SO-0012761", "M-001540", "AU01") in rows
    spare = link_plants([material for _so, material, _plant in rows]).spare
    pages: dict[str, str] = {}
    for so_no, material, plant in rows:
        assert plant
        assert spare[material] == plant
        if so_no not in pages:
            pages[so_no] = client.get("/screens/VA03", params={"key": so_no}).text
        assert f"/screens/MM03?key={material}&amp;plant={plant}" in pages[so_no]
