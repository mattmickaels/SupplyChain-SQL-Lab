"""Controlled purchase planning cases independent of data generation."""

from datetime import date, timedelta

from sclab.data.mrp import PlannedReceipt, plan_purchases


def test_plan_purchases_skips_order_when_stock_covers_demand():
    start = date(2026, 1, 10)
    demand = [(start + timedelta(days=10 * i), 30) for i in range(3)]
    assert plan_purchases(demand, 100, 10, 20, 7, 15) == []
    demand += [(start + timedelta(days=30), 30), (start + timedelta(days=40), 30)]
    assert plan_purchases(demand, 100, 10, 20, 7, 15) == [
        PlannedReceipt(start + timedelta(days=3), start + timedelta(days=23), 60)
    ]
    # Ten units carried from the first three events reduce the next purchase.
    assert plan_purchases(demand[3:], 0, 10, 20, 7, 15)[0].qty == 70


def test_plan_purchases_receipts_precede_need():
    start = date(2026, 1, 10)
    demand = [(start + timedelta(days=10 * i), 30) for i in range(5)]
    planned = plan_purchases(demand, 0, 10, 20, 7, 15)
    assert [p.requested_date for p in planned] == [
        demand[i][0] - timedelta(days=7) for i in (0, 2, 4)
    ]
    assert all(p.order_date == p.requested_date - timedelta(days=20) for p in planned)
    assert [p.qty for p in planned] == [70, 60, 30]
