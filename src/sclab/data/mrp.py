"""Pure, deterministic material requirements planning."""

from bisect import bisect_left
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class PlannedReceipt:
    order_date: date
    requested_date: date
    qty: int


def plan_purchases(
    demand: list[tuple[date, int]],
    opening_stock: int,
    safety_stock: int,
    lead_time_days: int,
    buffer_days: int,
    lot_days: int,
) -> list[PlannedReceipt]:
    """Net carried stock against demand within each rolling lot window.

    Demand must be sorted by need date; a lot covers [need, need + lot_days).
    Planned supply is counted once, so later needs consume the carried balance.
    """
    if min(opening_stock, safety_stock, lead_time_days, buffer_days) < 0 or lot_days < 1:
        raise ValueError("Stock and timing must be nonnegative, with positive lot_days")
    days = [day for day, _ in demand]
    if days != sorted(days) or any(qty < 0 for _, qty in demand):
        raise ValueError("Demand must be sorted with nonnegative quantities")
    cumulative = [0]
    for _, qty in demand:
        cumulative.append(cumulative[-1] + qty)
    stock = opening_stock
    planned = []
    for index, (need, qty) in enumerate(demand):
        if stock - qty < safety_stock:
            end = bisect_left(days, need + timedelta(days=lot_days))
            purchase = cumulative[end] - cumulative[index] + safety_stock - stock
            requested = need - timedelta(days=buffer_days)
            planned.append(
                PlannedReceipt(requested - timedelta(days=lead_time_days), requested, purchase)
            )
            stock += purchase
        stock -= qty
    return planned
