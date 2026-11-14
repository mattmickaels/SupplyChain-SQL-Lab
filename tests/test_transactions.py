"""Booking-based backlog procurement dates and lead-time safeguards."""

from datetime import date

import numpy as np
import pandas as pd

from sclab.constants import AS_OF_DATE, SEED
from sclab.data.transactions import _backlog_order_dates, _purchase_orders


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
