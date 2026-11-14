"""Planted supply-chain story in the generated database."""

from datetime import date, timedelta

import pandas as pd

from sclab.constants import AS_OF_DATE
from sclab.data.story import (
    CORVANE_SUPPLIER_NO,
    HALCYON_CUSTOMER_NO,
    OVERLOAD_RATIO,
    OVERLOAD_WINDOW,
    OVERLOADED_BAY,
    SLIP_DAYS,
    SLIP_START,
    SPIKE_START,
)


def _weeks(start: date, end: date) -> list[date]:
    monday = start - timedelta(days=start.weekday())
    weeks = []
    while monday <= end:
        weeks.append(monday)
        monday += timedelta(days=7)
    return weeks


def test_corvane_slip(lab_conn) -> None:
    slipped = lab_conn.execute(
        """
        SELECT i.confirmed_delivery_date - i.requested_delivery_date AS days
        FROM purchase_order_item AS i
        JOIN purchase_order AS h ON h.po_no = i.po_no
        WHERE h.supplier_no = ?
          AND h.order_date >= ?
        """,
        [CORVANE_SUPPLIER_NO, SLIP_START],
    ).df()
    assert len(slipped) > 0
    assert slipped["days"].notna().all()
    assert int(slipped["days"].min()) >= SLIP_DAYS

    receipts = lab_conn.execute(
        """
        SELECT g.receipt_date,
               g.receipt_date <= i.confirmed_delivery_date AS on_time
        FROM goods_receipt AS g
        JOIN purchase_order_item AS i
          ON i.po_no = g.po_no AND i.line_no = g.line_no
        JOIN purchase_order AS h ON h.po_no = g.po_no
        WHERE h.supplier_no = ?
        """,
        [CORVANE_SUPPLIER_NO],
    ).df()
    receipts["receipt_date"] = pd.to_datetime(receipts["receipt_date"]).dt.date
    early = receipts[receipts["receipt_date"] < date(2026, 1, 1)]
    late = receipts[
        (receipts["receipt_date"] >= SLIP_START) & (receipts["receipt_date"] <= AS_OF_DATE)
    ]
    assert len(early) > 0
    assert len(late) > 0
    assert float(early["on_time"].mean()) >= 0.90
    assert float(late["on_time"].mean()) <= 0.45


def test_halcyon_spike(lab_conn) -> None:
    def etch_qty(start: date, end: date) -> int:
        qty = lab_conn.execute(
            """
            SELECT COALESCE(SUM(i.qty), 0)
            FROM sales_order_item AS i
            JOIN sales_order AS h ON h.so_no = i.so_no
            JOIN material AS m ON m.material_no = i.material_no
            WHERE h.customer_no = ?
              AND m.material_type = 'TOOL'
              AND m.family_code = 'ETC'
              AND h.order_date >= ?
              AND h.order_date < ?
              AND i.status <> 'Cancelled'
            """,
            [HALCYON_CUSTOMER_NO, start, end],
        ).fetchone()[0]
        return int(qty)

    prior_start = SPIKE_START.replace(year=SPIKE_START.year - 1)
    prior_end = AS_OF_DATE.replace(year=AS_OF_DATE.year - 1)
    # Both windows count every non-cancelled Etch tool order line.
    prior_qty = etch_qty(prior_start, prior_end)
    assert prior_qty > 0
    assert etch_qty(SPIKE_START, AS_OF_DATE) >= 1.5 * prior_qty


def test_bay_overload(lab_conn) -> None:
    start, end = OVERLOAD_WINDOW
    bays = lab_conn.execute("SELECT bay_id, weekly_slots FROM build_bay").df()
    orders = lab_conn.execute(
        """
        SELECT bay_id, planned_start
        FROM production_order
        WHERE planned_start BETWEEN ? AND ?
        """,
        [start, end],
    ).df()
    if orders.empty:
        orders["planned_start"] = pd.Series(dtype="object")
    else:
        orders["planned_start"] = pd.to_datetime(orders["planned_start"]).dt.date
    weeks = _weeks(start, end)
    slots = bays.set_index("bay_id")["weekly_slots"].astype(int).to_dict()
    loads: dict[str, list[int]] = {bay_id: [] for bay_id in slots}
    for week in weeks:
        low = max(week, start)
        high = min(week + timedelta(days=6), end)
        in_week = orders[(orders["planned_start"] >= low) & (orders["planned_start"] <= high)]
        counts = in_week.groupby("bay_id").size() if not in_week.empty else {}
        for bay_id in loads:
            loads[bay_id].append(int(counts.get(bay_id, 0)))
    overloaded = loads[OVERLOADED_BAY]
    assert sum(overloaded) / len(overloaded) >= OVERLOAD_RATIO * slots[OVERLOADED_BAY]
    for bay_id, counts_for_bay in loads.items():
        if bay_id == OVERLOADED_BAY:
            continue
        assert sum(counts_for_bay) / len(counts_for_bay) <= slots[bay_id]


PROJECTION_CTES = """
    WITH RECURSIVE tree AS (
        SELECT m.material_no AS root, m.family_code, b.component_material_no AS material_no,
               b.qty_per AS qty
        FROM material m JOIN bom_item b ON b.parent_material_no = m.material_no
        WHERE m.material_type = 'TOOL'
        UNION ALL
        SELECT t.root, t.family_code, b.component_material_no, t.qty * b.qty_per
        FROM tree t JOIN bom_item b ON b.parent_material_no = t.material_no
    ), leaves AS (
        SELECT t.root, t.family_code, t.material_no, SUM(t.qty) AS qty
        FROM tree t JOIN material m ON m.material_no = t.material_no
        WHERE m.material_type = 'COMPONENT'
        GROUP BY t.root, t.family_code, t.material_no
    ), spare_plant AS (
        SELECT material_no, MIN(CASE family_code WHEN 'ETC' THEN 'SC01'
                               WHEN 'CMP' THEN 'SG01' ELSE 'AU01' END) AS plant_code
        FROM leaves GROUP BY material_no
    ), requirements AS (
        SELECT p.prod_order_no, p.plant_code, p.planned_start AS need_date,
               l.material_no, p.qty * l.qty AS qty
        FROM production_order p JOIN leaves l ON l.root = p.material_no
        WHERE p.planned_start > DATE '2026-11-01'
        UNION ALL
        SELECT NULL, sp.plant_code, s.promised_date, s.material_no, s.qty
        FROM sales_order_item s JOIN spare_plant sp ON sp.material_no = s.material_no
        WHERE s.status = 'Open' AND s.promised_date > DATE '2026-11-01'
    ), outstanding AS (
        SELECT i.material_no, h.plant_code, h.supplier_no, i.requested_delivery_date,
               i.confirmed_delivery_date, i.order_qty - COALESCE(SUM(g.qty), 0) AS qty
        FROM purchase_order_item i JOIN purchase_order h ON h.po_no = i.po_no
        LEFT JOIN goods_receipt g ON g.po_no = i.po_no AND g.line_no = i.line_no
        WHERE h.order_date <= DATE '2026-11-01'
        GROUP BY i.po_no, i.line_no, i.material_no, h.plant_code, h.supplier_no,
                 i.requested_delivery_date, i.confirmed_delivery_date, i.order_qty
        HAVING i.order_qty > COALESCE(SUM(g.qty), 0)
    ), stock AS (
        SELECT material_no, plant_code, SUM(qty_on_hand) AS qty
        FROM inventory WHERE storage_location = 'RM01'
        GROUP BY material_no, plant_code
    )
"""

RISK_ROWS_SQL = (
    PROJECTION_CTES
    + """
    SELECT DISTINCT p.so_no, p.so_line_no, h.order_date, p.planned_start
    FROM requirements r
    JOIN production_order p ON p.prod_order_no = r.prod_order_no
    JOIN sales_order_item s ON s.so_no = p.so_no AND s.line_no = p.so_line_no
    JOIN sales_order h ON h.so_no = s.so_no
    JOIN material m ON m.material_no = p.material_no
    JOIN material rf ON rf.material_no = r.material_no
    LEFT JOIN stock st ON st.material_no = r.material_no AND st.plant_code = r.plant_code
    WHERE s.status = 'Open' AND m.family_code = 'ETC' AND rf.description = 'RF generator'
      AND h.order_date <= DATE '2026-11-01'
      AND COALESCE(st.qty, 0)
        + COALESCE((SELECT SUM(o.qty) FROM outstanding o
                    WHERE o.material_no = r.material_no AND o.plant_code = r.plant_code
                      AND o.supplier_no = 'V-10017'
                      AND o.confirmed_delivery_date <= r.need_date), 0)
        < (SELECT SUM(d.qty) FROM requirements d
           WHERE d.material_no = r.material_no AND d.plant_code = r.plant_code
             AND d.need_date <= r.need_date)
      AND COALESCE(st.qty, 0)
        + COALESCE((SELECT SUM(o.qty) FROM outstanding o
                    WHERE o.material_no = r.material_no AND o.plant_code = r.plant_code
                      AND o.supplier_no = 'V-10017'
                      AND o.requested_delivery_date <= r.need_date), 0)
        >= (SELECT SUM(d.qty) FROM requirements d
            WHERE d.material_no = r.material_no AND d.plant_code = r.plant_code
              AND d.need_date <= r.need_date)
"""
)
AT_RISK_SQL = "SELECT COUNT(*) FROM (" + RISK_ROWS_SQL + ")"


def test_at_risk_orders_exist(lab_conn) -> None:
    rows = lab_conn.execute(RISK_ROWS_SQL).fetchall()
    print(f"at_risk_lines={len(rows)}")
    assert 5 <= len(rows) <= 15
    assert all(booked <= AS_OF_DATE < started for _, _, booked, started in rows)


def _projected_shortages(lab_conn, *, requested: bool, rf_only: bool, horizon: date):
    delivery = "requested_delivery_date" if requested else "confirmed_delivery_date"
    rf_filter = "=" if rf_only else "<>"
    return lab_conn.execute(
        PROJECTION_CTES
        + f"""
        , events AS (
            SELECT material_no, plant_code, DATE '2026-11-01' AS day, qty FROM stock
            UNION ALL
            SELECT material_no, plant_code, GREATEST({delivery}, DATE '2026-11-01'), qty
            FROM outstanding WHERE {delivery} <= ?
            UNION ALL
            SELECT material_no, plant_code, need_date, -qty FROM requirements
            WHERE need_date <= ?
        ), daily AS (
            SELECT material_no, plant_code, day, SUM(qty) AS qty FROM events
            GROUP BY material_no, plant_code, day
        ), balances AS (
            SELECT material_no, plant_code, day,
                   SUM(qty) OVER (PARTITION BY material_no, plant_code ORDER BY day
                                  ROWS UNBOUNDED PRECEDING) AS qty
            FROM daily
        )
        SELECT b.* FROM balances b JOIN material m ON m.material_no = b.material_no
        WHERE m.description {rf_filter} 'RF generator' AND b.qty < 0
        """,
        [horizon, horizon],
    ).fetchall()


def test_corvane_delay_withholds_required_supply(lab_conn) -> None:
    # The complete RF backlog is funded under the original requests.
    # This rules out unplaced or insufficient purchases hiding outside the risk count.
    assert (
        _projected_shortages(lab_conn, requested=True, rf_only=True, horizon=date(2027, 10, 31))
        == []
    )


def test_no_other_shortages(lab_conn) -> None:
    assert (
        _projected_shortages(
            lab_conn, requested=False, rf_only=False, horizon=AS_OF_DATE + timedelta(weeks=26)
        )
        == []
    )


def test_no_sales_orders_after_snapshot(lab_conn) -> None:
    assert (
        lab_conn.execute(
            "SELECT COUNT(*) FROM sales_order WHERE order_date > DATE '2026-11-01'"
        ).fetchone()[0]
        == 0
    )


def test_backlog_booked_ahead(lab_conn) -> None:
    rows = lab_conn.execute("""
        SELECT i.requested_date - h.order_date AS days, i.requested_date
        FROM sales_order_item i JOIN sales_order h ON h.so_no = i.so_no
        JOIN material m ON m.material_no = i.material_no
        WHERE i.status = 'Open' AND m.material_type = 'TOOL'
    """).fetchall()
    assert rows
    assert all(
        90 <= days <= 365 and date(2026, 11, 1) <= requested <= date(2027, 10, 31)
        for days, requested in rows
    )


def test_production_start_follows_promised_date(lab_conn) -> None:
    wrong = lab_conn.execute("""
        SELECT p.prod_order_no FROM production_order p
        JOIN sales_order_item i ON i.so_no = p.so_no AND i.line_no = p.so_line_no
        JOIN material m ON m.material_no = p.material_no
        WHERE i.promised_date - p.planned_start <> CASE m.family_code
            WHEN 'DEP' THEN 56 WHEN 'ETC' THEN 70 WHEN 'CMP' THEN 84 WHEN 'INS' THEN 42 END
           OR p.planned_finish >= i.promised_date OR p.planned_finish <= p.planned_start
    """).fetchall()
    assert wrong == []


def test_po_volume_has_margin(lab_conn) -> None:
    count = lab_conn.execute("SELECT COUNT(*) FROM purchase_order_item").fetchone()[0]
    assert 52_500 <= count <= 85_500


def test_rf_snapshot_at_safety_stock(lab_conn) -> None:
    differences = lab_conn.execute("""
        SELECT p.material_no, p.plant_code, p.safety_stock, COALESCE(i.qty_on_hand, 0)
        FROM material_plant p JOIN material m ON m.material_no = p.material_no
        LEFT JOIN inventory i ON i.material_no = p.material_no
          AND i.plant_code = p.plant_code AND i.storage_location = 'RM01'
        WHERE m.description = 'RF generator'
          AND EXISTS (SELECT 1 FROM stock_movement s
                      WHERE s.material_no = p.material_no AND s.plant_code = p.plant_code)
          AND COALESCE(i.qty_on_hand, 0) <> p.safety_stock
    """).fetchall()
    assert differences == []
