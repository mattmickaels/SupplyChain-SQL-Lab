"""Booking-based backlog procurement dates and lead-time safeguards."""

from datetime import date

import numpy as np
import pandas as pd

from sclab.constants import AS_OF_DATE, SEED
from sclab.data.transactions import _backlog_order_dates, _purchase_orders


def test_supply_receipt_variation(lab_conn):
    rows = lab_conn.execute("""
        SELECT i.*, COALESCE(SUM(g.qty), 0) received,
               MIN(g.receipt_date) first_receipt, MAX(g.receipt_date) last_receipt,
               COUNT(g.gr_no) receipts
        FROM purchase_order_item i LEFT JOIN goods_receipt g USING (po_no, line_no)
        GROUP BY ALL
    """).df()
    today = pd.Timestamp(AS_OF_DATE)
    future = rows[rows.confirmed_delivery_date >= today]
    assert 0.10 <= ((future.received > 0) & (future.received < future.order_qty)).mean() <= 0.20
    past = rows[rows.confirmed_delivery_date < today]
    assert 0.02 <= (past.received < past.order_qty).mean() <= 0.04
    assert (rows.confirmed_delivery_date == today).sum() >= 20
    multi = rows[rows.receipts > 1]
    assert len(multi) > 0
    assert (multi.last_receipt - multi.first_receipt).dt.days.between(1, 21).all()
    assert (
        lab_conn.execute("""
        SELECT COUNT(*) FROM goods_receipt g JOIN purchase_order h USING (po_no)
        WHERE g.receipt_date < h.order_date OR g.receipt_date > DATE '2026-11-01'
    """).fetchone()[0]
        == 0
    )


def test_history_boundary_and_material_creation(lab_conn):
    for name, column in (
        ("purchase_order", "order_date"),
        ("goods_receipt", "receipt_date"),
        ("stock_movement", "posting_date"),
    ):
        assert (
            lab_conn.execute(
                f"SELECT COUNT(*) FROM {name} WHERE {column} < DATE '2021-11-01'"
            ).fetchone()[0]
            == 0
        )
    assert (
        lab_conn.execute("""
        SELECT COUNT(*) FROM stock_movement
        WHERE movement_type = '561' AND posting_date = DATE '2021-11-01'
    """).fetchone()[0]
        > 0
    )
    assert (
        lab_conn.execute("""
        WITH uses AS (
            SELECT material_no, posting_date AS used_on FROM stock_movement
            UNION ALL SELECT i.material_no, h.order_date
            FROM purchase_order_item i JOIN purchase_order h USING (po_no)
            UNION ALL SELECT i.material_no, h.order_date
            FROM sales_order_item i JOIN sales_order h USING (so_no)
        ) SELECT COUNT(*) FROM uses u JOIN material m USING (material_no)
          WHERE m.created_on > u.used_on
    """).fetchone()[0]
        == 0
    )


def test_backlog_purchases_follow_bookings_and_keep_lead_time():
    master = {
        "material": pd.DataFrame({"material_no": ["part"], "description": ["Fitting"]}),
        "material_plant": pd.DataFrame(
            {
                "material_no": ["part"],
                "plant_code": ["SC01"],
                "safety_stock": [0],
                "planned_lead_time_days": [14],
            }
        ),
        "material_supplier": pd.DataFrame(
            {
                "material_no": ["part"],
                "supplier_no": ["vendor"],
                "is_primary": [True],
                "unit_price": [1.0],
            }
        ),
    }
    demand = pd.DataFrame(
        {
            "material_no": ["part", "part"],
            "plant_code": ["SC01", "SC01"],
            "qty": [10, 10],
            "need_date": pd.to_datetime(["2027-01-01", "2027-05-01"]),
            "booking_date": pd.to_datetime(["2026-09-01", "2026-10-10"]),
        }
    )
    lines = _purchase_orders(np.random.default_rng(SEED), master, demand)["lines"]
    lines = lines.sort_values("requested")
    assert list(lines["order_qty"]) == [10, 10]
    assert list(lines["requested"].dt.date) == [date(2026, 12, 25), date(2027, 4, 24)]
    assert (lines["confirmed"] == lines["requested"]).all()
    for (_, row), booked in zip(lines.iterrows(), demand["booking_date"], strict=True):
        assert booked + pd.Timedelta(days=3) <= row["order_date"] <= pd.Timestamp(AS_OF_DATE)
        assert row["order_date"].weekday() < 5
        assert (row["requested"] - row["order_date"]).days >= 14


def test_backlog_release_uses_last_feasible_working_day():
    planned = pd.Series(pd.to_datetime(["2027-01-01", "2026-10-01"]))
    booked = pd.Series(pd.to_datetime(["2026-10-27", None]))
    released = _backlog_order_dates(np.random.default_rng(SEED), planned, booked)
    # Only Friday remains after three processing days; the snapshot is Sunday.
    assert list(released.dt.date) == [date(2026, 10, 30), date(2026, 10, 1)]
