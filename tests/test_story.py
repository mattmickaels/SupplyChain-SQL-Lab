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


def test_at_risk_orders_exist(lab_conn) -> None:
    materials = lab_conn.execute(
        "SELECT material_no, description, material_type, family_code FROM material"
    ).df()
    bom = lab_conn.execute("SELECT parent_material_no, component_material_no FROM bom_item").df()
    children: dict[str, list[str]] = {}
    for parent, component in zip(
        bom["parent_material_no"],
        bom["component_material_no"],
        strict=True,
    ):
        children.setdefault(parent, []).append(component)
    rf = set(materials.loc[materials["description"] == "RF generator", "material_no"])

    def rf_under(tool_no: str) -> set[str]:
        found: set[str] = set()
        stack = [tool_no]
        seen: set[str] = set()
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            if node in rf:
                found.add(node)
            stack.extend(children.get(node, ()))
        return found

    orders = lab_conn.execute(
        """
        SELECT i.material_no, p.planned_start
        FROM sales_order_item AS i
        JOIN material AS m ON m.material_no = i.material_no
        JOIN production_order AS p
          ON p.so_no = i.so_no AND p.so_line_no = i.line_no
        WHERE i.status = 'Open'
          AND m.material_type = 'TOOL'
          AND m.family_code = 'ETC'
        """
    ).df()
    open_pos = lab_conn.execute(
        """
        SELECT i.material_no, i.confirmed_delivery_date
        FROM purchase_order_item AS i
        JOIN purchase_order AS h ON h.po_no = i.po_no
        LEFT JOIN goods_receipt AS g
          ON g.po_no = i.po_no AND g.line_no = i.line_no
        WHERE h.supplier_no = ?
          AND h.order_date <= ?
        GROUP BY i.po_no, i.line_no, i.material_no, i.order_qty, i.confirmed_delivery_date
        HAVING COALESCE(SUM(g.qty), 0) < i.order_qty
        """,
        [CORVANE_SUPPLIER_NO, AS_OF_DATE],
    ).df()
    confirmed_by_material: dict[str, list[date]] = {}
    for material_no, confirmed in zip(
        open_pos["material_no"],
        open_pos["confirmed_delivery_date"],
        strict=True,
    ):
        confirmed_by_material.setdefault(material_no, []).append(pd.Timestamp(confirmed).date())
    cache: dict[str, set[str]] = {}
    hits = 0
    for material_no, planned_start in zip(
        orders["material_no"],
        orders["planned_start"],
        strict=True,
    ):
        if material_no not in cache:
            cache[material_no] = rf_under(material_no)
        start = pd.Timestamp(planned_start).date()
        for component_no in cache[material_no]:
            if any(day > start for day in confirmed_by_material.get(component_no, ())):
                hits += 1
                break
    assert hits >= 5
