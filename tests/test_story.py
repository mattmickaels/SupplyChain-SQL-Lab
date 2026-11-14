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


AT_RISK_SQL = """
    WITH RECURSIVE explosion AS (
        SELECT p.prod_order_no, p.plant_code, p.planned_start,
               b.component_material_no AS material_no, p.qty * b.qty_per AS qty
        FROM production_order p
        JOIN bom_item b ON b.parent_material_no = p.material_no
        WHERE p.planned_start > DATE '2026-11-01'
        UNION ALL
        SELECT e.prod_order_no, e.plant_code, e.planned_start,
               b.component_material_no, e.qty * b.qty_per
        FROM explosion e JOIN bom_item b ON b.parent_material_no = e.material_no
    ), requirements AS (
        SELECT e.prod_order_no, e.plant_code, e.planned_start, e.material_no, SUM(e.qty) AS qty
        FROM explosion e JOIN material m ON m.material_no = e.material_no
        WHERE m.description = 'RF generator'
        GROUP BY e.prod_order_no, e.plant_code, e.planned_start, e.material_no
    ), outstanding AS (
        SELECT i.material_no, h.plant_code, i.confirmed_delivery_date,
               i.order_qty - COALESCE(SUM(g.qty), 0) AS qty
        FROM purchase_order_item i
        JOIN purchase_order h ON h.po_no = i.po_no
        LEFT JOIN goods_receipt g ON g.po_no = i.po_no AND g.line_no = i.line_no
        WHERE h.supplier_no = 'V-10017' AND h.order_date <= DATE '2026-11-01'
        GROUP BY i.po_no, i.line_no, i.material_no, h.plant_code,
                 i.confirmed_delivery_date, i.order_qty
        HAVING i.order_qty > COALESCE(SUM(g.qty), 0)
    )
    SELECT COUNT(DISTINCT (p.so_no, p.so_line_no))
    FROM requirements r
    JOIN production_order p ON p.prod_order_no = r.prod_order_no
    JOIN sales_order_item s ON s.so_no = p.so_no AND s.line_no = p.so_line_no
    JOIN material m ON m.material_no = p.material_no
    WHERE s.status = 'Open' AND m.family_code = 'ETC'
      AND COALESCE((SELECT SUM(i.qty_on_hand) FROM inventory i
                    WHERE i.material_no = r.material_no AND i.plant_code = r.plant_code
                      AND i.storage_location = 'RM01'), 0)
        + COALESCE((SELECT SUM(o.qty) FROM outstanding o
                    WHERE o.material_no = r.material_no AND o.plant_code = r.plant_code
                      AND o.confirmed_delivery_date <= r.planned_start), 0)
        < (SELECT SUM(d.qty) FROM requirements d
           WHERE d.material_no = r.material_no AND d.plant_code = r.plant_code
             AND d.planned_start <= r.planned_start)
"""


def test_at_risk_orders_exist(lab_conn) -> None:
    hits = lab_conn.execute(AT_RISK_SQL).fetchone()[0]
    print(f"at_risk_lines={hits}")
    assert hits >= 5


def test_corvane_delay_withholds_required_supply(lab_conn) -> None:
    # Enough RF was actually ordered by the snapshot, but its promises miss need.
    # This excludes shortages caused solely by future, not-yet-placed purchases.
    delayed = (
        AT_RISK_SQL
        + """
      AND COALESCE((SELECT SUM(i.qty_on_hand) FROM inventory i
                    WHERE i.material_no = r.material_no AND i.plant_code = r.plant_code
                      AND i.storage_location = 'RM01'), 0)
        + COALESCE((SELECT SUM(o.qty) FROM outstanding o
                    WHERE o.material_no = r.material_no AND o.plant_code = r.plant_code), 0)
        >= (SELECT SUM(d.qty) FROM requirements d
            WHERE d.material_no = r.material_no AND d.plant_code = r.plant_code
              AND d.planned_start <= r.planned_start)
      AND EXISTS (
          SELECT 1 FROM purchase_order_item i JOIN purchase_order h ON h.po_no = i.po_no
          WHERE h.supplier_no = 'V-10017'
            AND h.order_date BETWEEN DATE '2026-08-01' AND DATE '2026-11-01'
            AND i.material_no = r.material_no AND h.plant_code = r.plant_code
            AND i.requested_delivery_date <= r.planned_start
            AND i.confirmed_delivery_date > r.planned_start
      )
    """
    )
    hits = lab_conn.execute(delayed).fetchone()[0]
    print(f"at_risk_from_delay={hits}")
    assert hits >= 5


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
