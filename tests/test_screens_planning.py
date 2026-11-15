"""MD04 stock/requirements list and CM01 capacity load agree with lessons 9 and 10."""

import tomllib
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient
from test_story import RISK_ROWS_SQL

from sclab.constants import AS_OF_DATE
from sclab.engine.query import run_query
from sclab.screens import planning
from sclab.screens.framework import render_screen
from sclab.web.app import create_app

EMPTY_TEXT = "No open requirements or receipts in the next 52 weeks."
WINDOW = (date(2026, 11, 30), date(2027, 2, 22))
LESSON_NINE = (
    Path(__file__).parents[1]
    / "src"
    / "sclab"
    / "lessons"
    / "content"
    / "09-planning-analytics"
    / "exercises.toml"
)
SPARE_ORDER = 3


@pytest.fixture(autouse=True)
def _lab(lab_home):
    return lab_home


@pytest.fixture
def client(lab_home):
    test_client = TestClient(create_app(), base_url="http://127.0.0.1")
    test_client.headers.update({"HX-Request": "true"})
    return test_client


def _dicts(run) -> list[dict]:
    return [dict(zip(run.result.columns, row, strict=True)) for row in run.result.rows]


def _md04(material: str, plant: str = "SC01"):
    data = render_screen("MD04", material, {"plant": plant})
    assert data.found, data.message
    run = data.runs["list"]
    assert run.error is None
    return data, _dicts(run)


def _rf_material() -> str:
    return run_query(
        "SELECT p.material_no FROM material_plant p JOIN material m"
        " ON m.material_no = p.material_no"
        " WHERE p.plant_code = 'SC01' AND m.description LIKE 'RF generator,%'"
        " ORDER BY 1 LIMIT 1"
    ).rows[0][0]


def test_md04_matches_at_risk_story(lab_conn) -> None:
    lines = lab_conn.execute(RISK_ROWS_SQL).fetchall()
    assert lines
    for so_no, so_line_no, _booked, started in lines:
        generators = [
            row[0]
            for row in lab_conn.execute(
                """
                WITH RECURSIVE tree AS (
                    SELECT b.component_material_no AS material_no
                    FROM production_order p JOIN bom_item b ON b.parent_material_no = p.material_no
                    WHERE p.so_no = ? AND p.so_line_no = ?
                    UNION ALL
                    SELECT b.component_material_no
                    FROM tree t JOIN bom_item b ON b.parent_material_no = t.material_no
                )
                SELECT DISTINCT t.material_no FROM tree t
                JOIN material m ON m.material_no = t.material_no
                WHERE m.description LIKE 'RF generator,%'
                """,
                [so_no, so_line_no],
            ).fetchall()
        ]
        assert generators, (so_no, so_line_no)
        shortage_dates = [
            row["date"]
            for generator in generators
            for row in _md04(generator)[1]
            if row["flag"] == "SHORTAGE"
        ]
        assert shortage_dates, (so_no, so_line_no)
        assert min(shortage_dates) <= started, (so_no, so_line_no)


def test_md04_running_total(lab_conn) -> None:
    material = _rf_material()
    _, rows = _md04(material)
    stock = lab_conn.execute(
        "SELECT COALESCE(SUM(qty_on_hand), 0) FROM inventory"
        " WHERE material_no = ? AND plant_code = 'SC01' AND storage_location = 'RM01'",
        [material],
    ).fetchone()[0]
    assert rows[0]["element"] == "Stock"
    assert rows[0]["date"] == AS_OF_DATE
    assert rows[0]["available_qty"] == stock
    assert len(rows) > 5
    available = 0
    for row in rows:
        receipt = row["receipt_qty"] or 0
        requirement = row["requirement_qty"] or 0
        assert requirement <= 0
        available += receipt + requirement
        assert row["available_qty"] == available
    assert any(row["receipt_qty"] for row in rows[1:])
    assert any(row["requirement_qty"] for row in rows)
    keys = [(row["date"], row["element_order"]) for row in rows]
    assert keys == sorted(keys)
    assert rows[-1]["date"] < AS_OF_DATE + timedelta(weeks=52)


def test_md04_flags_follow_safety_stock() -> None:
    generators = [
        row[0]
        for row in run_query(
            "SELECT p.material_no FROM material_plant p JOIN material m"
            " ON m.material_no = p.material_no"
            " WHERE p.plant_code = 'SC01' AND m.description LIKE 'RF generator,%' ORDER BY 1"
        ).rows
    ]
    seen = set()
    for generator in generators:
        data, rows = _md04(generator)
        safety = data.runs["header"].result.rows[0][3]
        for row in rows:
            available = row["available_qty"]
            expected = "SHORTAGE" if available < 0 else "below safety" if available < safety else ""
            assert row["flag"] == expected
            seen.add(row["flag"])
    assert {"SHORTAGE", "below safety", ""} <= seen


def test_md04_agrees_with_lesson_nine_balance() -> None:
    reference = next(
        item
        for item in tomllib.loads(LESSON_NINE.read_text(encoding="utf-8"))["exercise"]
        if item["id"] == "plan-weekly-balance"
    )["reference_sql"][0]
    weekly = run_query(reference).rows
    assert len(weekly) == 26
    _, rows = _md04("M-001324")
    # Lesson 9 leaves spare sales lines out and ends each week with the balance so far.
    balance = 0
    ledger = []
    for row in rows:
        if row["element_order"] == SPARE_ORDER:
            continue
        balance += (row["receipt_qty"] or 0) + (row["requirement_qty"] or 0)
        ledger.append((row["date"], balance))
    for week_start, _receipts, _demand, projected in weekly:
        end = week_start + timedelta(days=7)
        upto = [value for day, value in ledger if day < end]
        assert upto[-1] == projected, week_start


def _empty_material() -> tuple[str, str]:
    """A component and plant with no purchase orders; scan until nothing else is open either."""
    candidates = run_query(
        """
        SELECT p.material_no, p.plant_code
        FROM material_plant p JOIN material m ON m.material_no = p.material_no
        WHERE m.material_type = 'COMPONENT'
          AND NOT EXISTS (SELECT 1 FROM purchase_order_item i
                          JOIN purchase_order h ON h.po_no = i.po_no
                          WHERE i.material_no = p.material_no AND h.plant_code = p.plant_code)
        ORDER BY 1, 2
        """
    ).rows
    for material, plant in candidates:
        if len(_md04(material, plant)[1]) == 1:
            return material, plant
    raise AssertionError("no component with an empty list")


def test_md04_empty_material(client) -> None:
    material, plant = _empty_material()
    _, rows = _md04(material, plant)
    assert len(rows) == 1
    assert rows[0]["element"] == "Stock"
    response = client.get("/screens/MD04", params={"key": material, "plant": plant})
    assert response.status_code == 200
    assert EMPTY_TEXT in response.text
    busy = client.get("/screens/MD04", params={"key": _rf_material(), "plant": "SC01"})
    assert EMPTY_TEXT not in busy.text


def test_md04_not_found_and_prompts(client) -> None:
    for key in ("M-999999", "M-1' OR '1'='1"):
        response = client.get("/screens/MD04", params={"key": key, "plant": "SC01"})
        assert response.status_code == 200
        assert "was not found" in response.text
    missing = client.get("/screens/MD04", params={"key": _rf_material()})
    assert "Enter a plant." in missing.text
    elsewhere = render_screen("MD04", _rf_material(), {"plant": "ZZ99"})
    assert elsewhere.found is False


def test_md04_header(lab_conn) -> None:
    data, _ = _md04("M-001324")
    run = data.runs["header"]
    header = _dicts(run)[0]
    safety, lead = lab_conn.execute(
        "SELECT safety_stock, planned_lead_time_days FROM material_plant"
        " WHERE material_no = 'M-001324' AND plant_code = 'SC01'"
    ).fetchone()
    assert header["safety_stock"] == safety
    assert header["lead_time_days"] == lead
    assert header["sources"] == "Corvane RF Systems (sole)"


def test_md04_displayed_sql_reproduces_rows(lab_home) -> None:
    data, _ = _md04(_rf_material())
    for run in data.runs.values():
        again = run_query(run.display_sql)
        assert again.rows == run.result.rows
        assert again.columns == run.result.columns
    assert "$" not in data.runs["list"].display_sql


def _cm01(plant: str = "SC01"):
    data = render_screen("CM01", plant, {})
    assert data.found, data.message
    assert all(run.error is None for run in data.runs.values())
    return data


def test_cm01_flags_overloaded_bay() -> None:
    rows = _dicts(_cm01().runs["load"])
    window = [r for r in rows if WINDOW[0] <= r["week_start"] <= WINDOW[1]]
    assert len(window) == 2 * 13
    b3 = [r for r in window if r["bay_id"] == "SC01-B3"]
    assert len(b3) == 13
    assert all(r["overloaded"] for r in b3)
    assert not any(r["overloaded"] for r in window if r["bay_id"] != "SC01-B3")
    for row in rows:
        assert row["overloaded"] == (row["starts"] > row["weekly_slots"])
        assert row["load_pct"] == round(100 * row["starts"] / row["weekly_slots"], 2)


def test_cm01_covers_26_weeks_and_matches_drill_totals(lab_conn) -> None:
    rows = _dicts(_cm01().runs["load"])
    weeks = sorted({r["week_start"] for r in rows})
    assert len(weeks) == 26
    assert weeks[0].weekday() == 0
    assert AS_OF_DATE < weeks[0] <= AS_OF_DATE + timedelta(days=7)
    assert all(b - a == timedelta(days=7) for a, b in zip(weeks, weeks[1:], strict=False))
    # Lesson 10 counts starts from 1 December; the weekly grid also counts 30 November starts.
    before_window = {
        bay: count
        for bay, count in lab_conn.execute(
            "SELECT bay_id, COUNT(*) FROM production_order"
            " WHERE status IN ('Planned', 'Released') AND planned_start > ?"
            "   AND planned_start < DATE '2026-12-01' AND planned_start >= ?"
            " GROUP BY bay_id",
            [AS_OF_DATE, WINDOW[0]],
        ).fetchall()
    }
    totals: dict[str, list[int]] = {}
    for r in rows:
        if WINDOW[0] <= r["week_start"] <= WINDOW[1]:
            starts, slots = totals.get(r["bay_id"], [0, 0])
            totals[r["bay_id"]] = [starts + r["starts"], slots + r["weekly_slots"]]
    for bay in totals:
        totals[bay][0] -= before_window.get(bay, 0)
    assert totals["SC01-B3"] == [104, 78]
    assert round(100 * 104 / 78, 2) == 133.33
    assert totals["SC01-B2"] == [34, 52]
    assert round(100 * 34 / 52, 2) == 65.38


def test_cm01_drill_lists_orders_of_every_bay_week() -> None:
    data = _cm01()
    orders = _dicts(data.runs["orders"])
    grid = [r for r in _dicts(data.runs["load"]) if r["starts"]]
    assert orders
    assert len(orders) == sum(r["starts"] for r in grid)
    assert {(r["bay_id"], r["week_start"]) for r in orders} == {
        (r["bay_id"], r["week_start"]) for r in grid
    }
    assert any(not r["overloaded"] for r in orders)
    assert any(r["overloaded"] for r in orders)
    assert all(r["overloaded"] == (r["starts"] > r["weekly_slots"]) for r in orders)
    assert all(r["status"] in ("Planned", "Released") for r in orders)
    assert all(r["planned_start"] > AS_OF_DATE for r in orders)


def test_cm01_first_week_assumes_snapshot_is_a_sunday() -> None:
    # The grid starts on the Monday after the snapshot; revisit CM01 if AS_OF_DATE moves.
    assert AS_OF_DATE.weekday() == 6


def test_md04_spare_lines_sit_at_the_spare_plant(lab_conn) -> None:
    material = "M-001327"
    spare_by_plant = {}
    for plant in ("AU01", "SC01", "SG01"):
        _, rows = _md04(material, plant)
        spare_by_plant[plant] = [r for r in rows if r["element_order"] == SPARE_ORDER]
    holders = [plant for plant, rows in spare_by_plant.items() if rows]
    assert len(holders) == 1
    spare = spare_by_plant[holders[0]]
    expected = lab_conn.execute(
        "SELECT so_no, line_no, promised_date, qty FROM sales_order_item"
        " WHERE material_no = ? AND status = 'Open' AND promised_date > ?"
        "   AND promised_date < ? ORDER BY so_no, line_no",
        [material, AS_OF_DATE, AS_OF_DATE + timedelta(weeks=52)],
    ).fetchall()
    assert expected
    assert len(spare) == len(expected)
    by_doc = {(r["doc_no"], r["doc_line"]): r for r in spare}
    for so_no, line_no, promised, qty in expected:
        row = by_doc[(so_no, line_no)]
        assert row["date"] == promised
        assert row["requirement_qty"] == -qty


def _scratch_copy(lab_conn, material: str):
    """Copy the tables MD04 reads for one material into memory so rows can be added."""
    conn = duckdb.connect()
    queries = {
        "material": "SELECT * FROM material",
        "bom_item": "SELECT * FROM bom_item",
        "material_plant": "SELECT * FROM material_plant WHERE material_no = ?",
        "inventory": "SELECT * FROM inventory WHERE material_no = ?",
        "purchase_order_item": "SELECT * FROM purchase_order_item WHERE material_no = ?",
        "purchase_order": "SELECT * FROM purchase_order WHERE po_no IN"
        " (SELECT po_no FROM purchase_order_item WHERE material_no = ?)",
        "goods_receipt": "SELECT * FROM goods_receipt WHERE po_no IN"
        " (SELECT po_no FROM purchase_order_item WHERE material_no = ?)",
        "production_order": "SELECT * FROM production_order",
        "sales_order_item": "SELECT * FROM sales_order_item WHERE material_no = ?",
    }
    for name, sql in queries.items():
        frame = lab_conn.execute(sql, [material] if "?" in sql else []).df()
        conn.register(f"src_{name}", frame)
        conn.execute(f"CREATE TABLE {name} AS SELECT * FROM src_{name}")
    return conn


def test_md04_excludes_late_po_lines_and_started_orders(lab_conn) -> None:
    material = _rf_material()
    conn = _scratch_copy(lab_conn, material)
    after = AS_OF_DATE + timedelta(days=1)
    tool = conn.execute(
        """
        WITH RECURSIVE up(parent_material_no, level) AS (
            SELECT parent_material_no, 1 FROM bom_item WHERE component_material_no = ?
            UNION ALL
            SELECT b.parent_material_no, up.level + 1
            FROM up JOIN bom_item b ON b.component_material_no = up.parent_material_no
            WHERE up.level < 10
        )
        SELECT DISTINCT m.material_no FROM up
        JOIN material m ON m.material_no = up.parent_material_no
        WHERE m.material_type = 'TOOL'
        ORDER BY 1 LIMIT 1
        """,
        [material],
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO purchase_order VALUES ('PO-LATE', 'V-10017', 'SC01', ?),"
        " ('PO-EDGE', 'V-10017', 'SC01', ?)",
        [after, AS_OF_DATE],
    )
    template = conn.execute("SELECT * FROM purchase_order_item LIMIT 1").df()
    for po_no in ("PO-LATE", "PO-EDGE"):
        row = template.copy()
        row["po_no"] = po_no
        row["line_no"] = 1
        row["material_no"] = material
        row["order_qty"] = 99
        row["confirmed_delivery_date"] = AS_OF_DATE + timedelta(days=10)
        conn.register("late_item", row)
        conn.execute("INSERT INTO purchase_order_item SELECT * FROM late_item")
    started = conn.execute("SELECT * FROM production_order LIMIT 1").df()
    for order_no, start, status in (
        ("PR-STARTED", AS_OF_DATE, "Planned"),
        ("PR-FUTURE", after, "Planned"),
    ):
        row = started.copy()
        row["prod_order_no"] = order_no
        row["material_no"] = tool
        row["plant_code"] = "SC01"
        row["planned_start"] = start
        row["status"] = status
        conn.register("new_order", row)
        conn.execute("INSERT INTO production_order SELECT * FROM new_order")
    rows = conn.execute(planning.MD04_LIST_SQL, [material, "SC01"]).fetchall()
    docs = {row[3] for row in rows}
    assert "PO-LATE" not in docs
    assert "PO-EDGE" in docs
    assert "PR-STARTED" not in docs
    assert "PR-FUTURE" in docs


def test_cm01_displayed_sql_reproduces_rows(lab_home) -> None:
    for run in _cm01().runs.values():
        again = run_query(run.display_sql)
        assert again.rows == run.result.rows
        assert again.columns == run.result.columns


def test_cm01_not_found_and_page(client) -> None:
    missing = client.get("/screens/CM01", params={"key": "ZZ99"})
    assert missing.status_code == 200
    assert "Plant ZZ99 was not found." in missing.text
    page = client.get("/screens/CM01", params={"key": "SC01"})
    assert page.status_code == 200
    assert "SC01-B3" in page.text
    assert "over 100 percent" in page.text
    assert page.text.count("Show the SQL") == 3


def test_planning_screens_are_read_only() -> None:
    for screen in (planning.MD04, planning.CM01):
        for sql in (screen.exists_sql, *(q.sql for q in screen.queries)):
            statements = duckdb.extract_statements(sql)
            assert len(statements) == 1
            assert statements[0].type == duckdb.StatementType.SELECT


def test_md04_states_the_lines_left_out(client) -> None:
    unconfirmed = run_query(
        """
        SELECT i.material_no, h.plant_code
        FROM purchase_order_item AS i
        JOIN purchase_order AS h ON h.po_no = i.po_no
        LEFT JOIN goods_receipt AS g ON g.po_no = i.po_no AND g.line_no = i.line_no
        WHERE h.order_date <= DATE '2026-11-01'
          AND i.confirmed_delivery_date IS NULL
        GROUP BY i.material_no, h.plant_code, i.po_no, i.line_no, i.order_qty
        HAVING i.order_qty > COALESCE(SUM(g.qty), 0)
        LIMIT 1
        """
    ).rows
    assert unconfirmed
    material, plant = unconfirmed[0]
    po_lines, spare_lines = run_query(planning.MD04_OMITTED_SQL, params=[material, plant]).rows[0]
    assert po_lines > 0
    page = client.get("/screens/MD04", params={"key": material, "plant": plant})
    assert f"without a confirmed date ({po_lines})" in page.text
    assert f"promised date ({spare_lines})" in page.text
    spare_hit = None
    candidates = run_query(
        """
        SELECT DISTINCT material_no
        FROM sales_order_item
        WHERE status = 'Open' AND promised_date <= DATE '2026-11-01'
        ORDER BY material_no
        LIMIT 40
        """
    ).rows
    for (spare_material,) in candidates:
        for (spare_plant,) in run_query("SELECT plant_code FROM plant").rows:
            past = run_query(planning.MD04_OMITTED_SQL, params=[spare_material, spare_plant]).rows[
                0
            ][1]
            if past > 0:
                spare_hit = (spare_material, spare_plant, past)
                break
        if spare_hit:
            break
    assert spare_hit is not None
    spare_material, spare_plant, past = spare_hit
    spare_page = client.get("/screens/MD04", params={"key": spare_material, "plant": spare_plant})
    assert f"promised date ({past})" in spare_page.text


def test_spare_plant_cte_is_shared() -> None:
    assert planning._SPARE_PLANT_CTE in planning.MD04_LIST_SQL
    assert planning._SPARE_PLANT_CTE in planning.MD04_OMITTED_SQL
    assert "origin.material_type = 'COMPONENT'" in planning._SPARE_PLANT_CTE


def test_displayed_sql_is_dedented() -> None:
    data = render_screen("CM01", "SC01", {})
    for run in data.runs.values():
        assert run.display_sql
        assert not any(line.startswith(" " * 12) for line in run.display_sql.splitlines())
