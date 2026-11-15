"""Planted story values shared by the generators."""

from datetime import date

CORVANE_SUPPLIER_NO = "V-10017"
HALCYON_CUSTOMER_NO = "C-2003"
# The customer whose last tool order is exactly 90 days before the snapshot.
BOUNDARY_CUSTOMER_NO = "C-2025"
OVERLOADED_BAY = "SC01-B3"
SLIP_START = date(2026, 8, 1)
SLIP_DAYS = 56
SPIKE_START = date(2026, 7, 1)
SPIKE_FACTOR = 1.6
OVERLOAD_WINDOW = (date(2026, 12, 1), date(2027, 2, 28))
OVERLOAD_RATIO = 1.3
# Units above on-hand stock that buyers added to idle long-lead safety stocks (see messiness.py).
SUPPLIER_WARNING_RAISES = (3, 6)
