"""Sales, production, purchasing, stock, and quality for the lab database."""

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

from sclab.constants import AS_OF_DATE, HISTORY_START
from sclab.data.master import HOME_PLANT, TOOL_PRICES, _segment
from sclab.data.mrp import plan_purchases
from sclab.data.story import (
    BOUNDARY_CUSTOMER_NO,
    CORVANE_SUPPLIER_NO,
    HALCYON_CUSTOMER_NO,
    OVERLOAD_RATIO,
    OVERLOAD_WINDOW,
    OVERLOADED_BAY,
    SLIP_DAYS,
    SLIP_START,
)

AS_OF_TS = pd.Timestamp(AS_OF_DATE)
SLIP_TS = pd.Timestamp(SLIP_START)
EARLY_END = pd.Timestamp(date(2026, 1, 1))
HISTORY_MONTH = pd.Timestamp(date(2021, 11, 1))
LOT_DAYS = 100
# The supplier slip overruns this early-order buffer by 13 days.
# That overrun is what steers the Corvane-caused at-risk Etch lines to about 8-10.
RF_BUFFER_DAYS = SLIP_DAYS - 13
RECEIPT_BUFFER_DAYS = 7
# Confirmations stay inside the first weekly bucket, from the snapshot day on.
CONFIRM_SPREAD_DAYS = 7
COMPONENT_LOCATION = "RM01"
TOOL_LOCATION = "FG01"
BUILD_DAYS = {"DEP": 56, "ETC": 70, "CMP": 84, "INS": 42}
PROMISE_GAP = 7
# Monthly tool bookings follow an industry cycle: strong 2022, a 2023 downturn, then an upswing.
CYCLE_ANCHORS = (
    (0, 34),
    (7, 41),
    (13, 38),
    (19, 29),
    (25, 26),
    (31, 31),
    (37, 36),
    (43, 42),
    (49, 47),
    (55, 50),
    (59, 50),
)
# Quarter-end months book more, and the summer shutdown books less.
SEASONALITY = {
    1: 0.90,
    2: 0.95,
    3: 1.12,
    4: 0.98,
    5: 1.00,
    6: 1.10,
    7: 0.88,
    8: 0.85,
    9: 1.08,
    10: 1.00,
    11: 1.00,
    12: 1.15,
}
TWO_UNIT_SHARE = 0.05
TOOL_PRICE_DRIFT = 0.025


def generate_transactions(
    rng: np.random.Generator,
    master: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    """Build the transactional tables from master data and one random stream."""
    leaf = _tool_components(master)
    plant_of = _component_plant(leaf, master)
    lines = _order_lines(rng, master, plant_of)
    lines = _apply_status(rng, lines)
    production = _production_orders(lines)
    forecast = _forecast(master, lines)
    demand, issues = _component_demand(lines, production, leaf)
    supply = _supply(rng, master, demand)
    movements, inventory = _stock(
        lines, production, supply["goods_receipt"], issues, supply["opening"]
    )
    return {
        "sales_order": _sales_orders(lines),
        "sales_order_item": _sales_items(lines),
        "demand_forecast": forecast,
        "production_order": _production_frame(production),
        "purchase_order": supply["purchase_order"],
        "purchase_order_item": supply["purchase_order_item"],
        "goods_receipt": supply["goods_receipt"],
        "stock_movement": movements,
        "inventory": inventory,
        "quality_notification": supply["quality_notification"],
    }


def _tool_components(master: dict[str, pd.DataFrame]) -> pd.DataFrame:
    bom = master["bom_item"][["parent_material_no", "component_material_no", "qty_per"]]
    types = master["material"].set_index("material_no")["material_type"]
    tools = master["material"].loc[master["material"]["material_type"] == "TOOL", "material_no"]
    current = pd.DataFrame(
        {"root": tools.to_numpy(), "material_no": tools.to_numpy(), "qty": np.ones(len(tools))}
    )
    leaves: list[pd.DataFrame] = []
    for _level in range(6):
        merged = current.merge(bom, left_on="material_no", right_on="parent_material_no")
        merged["qty"] = merged["qty"] * merged["qty_per"]
        merged["material_no"] = merged["component_material_no"]
        is_component = merged["material_no"].map(types).eq("COMPONENT")
        leaves.append(merged.loc[is_component, ["root", "material_no", "qty"]])
        current = merged.loc[~is_component, ["root", "material_no", "qty"]]
        if current.empty:
            break
    leaf = pd.concat(leaves, ignore_index=True)
    leaf = leaf.groupby(["root", "material_no"], as_index=False)["qty"].sum()
    leaf["qty"] = leaf["qty"].round().astype(int)
    return leaf


def _component_plant(leaf: pd.DataFrame, master: dict[str, pd.DataFrame]) -> pd.Series:
    family = master["material"].set_index("material_no")["family_code"]
    planted = pd.DataFrame(
        {
            "material_no": leaf["material_no"].to_numpy(),
            "plant_code": leaf["root"].map(family).map(HOME_PLANT).to_numpy(),
        }
    )
    planted = planted.drop_duplicates().sort_values(["material_no", "plant_code"])
    return planted.groupby("material_no")["plant_code"].first()


def _order_lines(rng, master: dict[str, pd.DataFrame], plant_of: pd.Series) -> pd.DataFrame:
    materials = master["material"]
    tools = materials.loc[materials["material_type"] == "TOOL"].sort_values("material_no")
    tool_ids = tools["material_no"].to_numpy()
    etch_ids = tools.loc[tools["family_code"] == "ETC", "material_no"].to_numpy()
    family_of = tools.set_index("material_no")["family_code"]
    customers = np.sort(master["customer"]["customer_no"].to_numpy())
    months = pd.date_range(HISTORY_MONTH, AS_OF_TS - pd.Timedelta(days=1), freq="MS")
    counts, halcyon_n = _month_counts(rng, months)
    list_price = _tool_list_prices(tools)
    normal = _normal_tool_lines(
        rng, months, counts, halcyon_n, tool_ids, etch_ids, customers, list_price
    )
    normal = _pin_ninety_day_customer(normal, customers)
    normal["promised_date"] = normal["order_date"] + pd.to_timedelta(
        rng.integers(90, 366, size=len(normal)), unit="D"
    )
    tools_frame = _schedule_overload(normal, master, etch_ids)
    # Price after scheduling, because the bay schedule can move a booking to another Etch tool.
    base_price = list_price.loc[tools_frame["material_no"]].to_numpy()
    tools_frame["unit_price"] = (
        np.round(base_price * tools_frame["price_factor"] / 500) * 500
    ).astype(int)
    tools_frame["family_code"] = tools_frame["material_no"].map(family_of)
    tools_frame["plant_code"] = tools_frame["family_code"].map(HOME_PLANT)
    missing_bay = tools_frame["bay_id"].isna()
    tools_frame.loc[missing_bay, "bay_id"] = _assign_bays(
        tools_frame.loc[missing_bay, ["plant_code", "family_code"]],
        master["build_bay"],
    )
    spares = _spare_lines(rng, plant_of, customers, _spare_prices(master))
    spares["promised_date"] = spares["order_date"] + pd.Timedelta(days=21)
    lines = pd.concat([tools_frame, spares], ignore_index=True)
    lines["order_date"] = pd.to_datetime(lines["order_date"])
    lines["requested_date"] = lines["promised_date"]
    return lines


def _pin_ninety_day_customer(normal: pd.DataFrame, customers: np.ndarray) -> pd.DataFrame:
    """Make one customer's last tool order fall exactly 90 days before the snapshot.

    Only the customer on a few order lines changes, never a date, quantity or tool.
    """
    frame = normal.copy()
    boundary = AS_OF_TS - pd.Timedelta(days=90)
    free = ~frame["protected"]
    later = free & frame["customer_no"].eq(BOUNDARY_CUSTOMER_NO) & frame["order_date"].gt(boundary)
    on_day = free & frame["order_date"].eq(boundary)
    last = frame.groupby("customer_no")["order_date"].max()
    recipients = [
        no
        for no in customers
        if no != BOUNDARY_CUSTOMER_NO
        and last.get(no, pd.Timestamp.min) >= AS_OF_TS - pd.Timedelta(days=30)
    ]
    frame.loc[later, "customer_no"] = [
        recipients[i % len(recipients)] for i in range(int(later.sum()))
    ]
    if (
        not frame.loc[free & frame["customer_no"].eq(BOUNDARY_CUSTOMER_NO), "order_date"]
        .eq(boundary)
        .any()
    ):
        frame.loc[frame.index[on_day][:1], "customer_no"] = BOUNDARY_CUSTOMER_NO
    return frame


def _month_counts(rng, months: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray]:
    index = np.arange(len(months))
    anchors = np.array(CYCLE_ANCHORS, dtype=float)
    cycle = np.interp(index, anchors[:, 0], anchors[:, 1])
    season = np.array([SEASONALITY[int(month)] for month in months.month])
    noise = rng.integers(-3, 4, size=len(months))
    counts = np.clip(np.rint(cycle * season).astype(int) + noise, 18, 60)
    halcyon = np.zeros(len(months), dtype=int)
    halcyon[(months.year == 2025) & months.month.isin([7, 8, 9, 10])] = 4
    halcyon[(months.year == 2026) & months.month.isin([7, 8, 9, 10])] = 8
    return counts, halcyon


def _tool_list_prices(tools: pd.DataFrame) -> pd.Series:
    """List price by family and chamber count, in USD."""
    chambers = tools["description"].str.extract(r"(\d+)-chamber")[0].astype(int)
    base = tools["family_code"].map(lambda code: TOOL_PRICES[code][0])
    per_chamber = tools["family_code"].map(lambda code: TOOL_PRICES[code][1])
    return pd.Series((base + per_chamber * chambers).to_numpy(), index=tools["material_no"])


def _spare_prices(master: dict[str, pd.DataFrame]) -> pd.Series:
    sources = master["material_supplier"]
    primary = sources.loc[sources["is_primary"]].sort_values(["material_no", "supplier_no"])
    return primary.drop_duplicates("material_no").set_index("material_no")["unit_price"]


def _normal_tool_lines(rng, months, counts, halcyon_n, tool_ids, etch_ids, customers, list_price):
    month_of_row = np.repeat(np.arange(len(months)), counts)
    total = len(month_of_row)
    starts = np.repeat(np.cumsum(counts) - counts, counts)
    position = np.arange(total) - starts
    halcyon = position < np.repeat(halcyon_n, counts)
    tool_no = tool_ids[np.arange(total) % len(tool_ids)]
    halcyon_at = np.flatnonzero(halcyon)
    tool_no[halcyon_at] = etch_ids[np.arange(halcyon_at.size) % len(etch_ids)]
    customer_no = rng.choice(customers, size=total)
    customer_no[halcyon_at] = HALCYON_CUSTOMER_NO
    offset = rng.integers(0, 28, size=total)
    order_date = pd.to_datetime(months[month_of_row]) + pd.to_timedelta(offset, unit="D")
    protected = np.zeros(total, dtype=bool)
    protected[halcyon_at] = True
    qty = np.where(rng.random(total) < TWO_UNIT_SHARE, 2, 1)
    # Sell price: list price with a negotiated spread, drifting up about 2.5% a year.
    years = (order_date - pd.Timestamp(HISTORY_MONTH)).days.to_numpy() / 365.25
    price_factor = (1 + TOOL_PRICE_DRIFT * years) * rng.uniform(0.95, 1.04, size=total)
    return pd.DataFrame(
        {
            "order_date": order_date,
            "customer_no": customer_no,
            "material_no": tool_no,
            "qty": qty,
            "price_factor": price_factor,
            "protected": protected,
            "bay_id": pd.Series(pd.NA, index=np.arange(total), dtype="object"),
            "kind": "tool",
        }
    )


def _overload_dates(weekly_slots: int) -> list[date]:
    start, end = OVERLOAD_WINDOW
    per_week = math.ceil(weekly_slots * OVERLOAD_RATIO - 1e-9)
    monday = start - timedelta(days=start.weekday())
    dates: list[date] = []
    while monday <= end:
        days = [
            monday + timedelta(days=offset)
            for offset in range(7)
            if start <= monday + timedelta(days=offset) <= end
        ]
        dates.extend(days[offset % len(days)] for offset in range(per_week))
        monday += timedelta(days=7)
    counts: dict[tuple[int, int], int] = {}
    for day in dates:
        counts[(day.year, day.month)] = counts.get((day.year, day.month), 0) + 1
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        key = (cursor.year, cursor.month)
        while counts.get(key, 0) < 25:
            dates.append(cursor)
            counts[key] += 1
        if counts[key] > 45:
            raise RuntimeError(f"Overload month {key} has {counts[key]} tool orders")
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return dates


def _schedule_overload(normal, master, etch_ids) -> pd.DataFrame:
    """Schedule existing bookings into the overloaded bay without inventing orders."""
    frame = normal.copy()
    slots = int(master["build_bay"].set_index("bay_id").loc[OVERLOADED_BAY, "weekly_slots"])
    available = set(frame.index[~frame["protected"]])
    for number, start in enumerate(_overload_dates(slots)):
        promised = pd.Timestamp(start) + pd.Timedelta(days=BUILD_DAYS["ETC"])
        eligible = frame.index[
            frame.index.isin(available)
            & frame["order_date"].between(
                promised - pd.Timedelta(days=365), promised - pd.Timedelta(days=90)
            )
        ]
        if len(eligible) == 0:
            raise ValueError("Insufficient booked orders for the bay schedule")
        # Prefer recent bookings so older historical deliveries stay historical.
        chosen = int(eligible[-1])
        available.remove(chosen)
        frame.loc[chosen, ["material_no", "promised_date", "bay_id", "protected"]] = [
            etch_ids[number % len(etch_ids)],
            promised,
            OVERLOADED_BAY,
            True,
        ]
    return frame


def _assign_bays(frame: pd.DataFrame, bays: pd.DataFrame) -> pd.Series:
    usable = bays[bays["bay_id"] != OVERLOADED_BAY]
    options = {
        (plant, family): sorted(group["bay_id"].tolist())
        for (plant, family), group in usable.groupby(["plant_code", "family_code"])
    }
    assigned = pd.Series(index=frame.index, dtype="object")
    for key, indexes in frame.groupby(["plant_code", "family_code"], sort=True).groups.items():
        choices = options[key]
        ordered = list(indexes)
        assigned.loc[ordered] = [choices[i % len(choices)] for i in range(len(ordered))]
    return assigned


def _spare_lines(
    rng, plant_of: pd.Series, customers: np.ndarray, prices: pd.Series
) -> pd.DataFrame:
    months = pd.date_range(HISTORY_MONTH, AS_OF_TS - pd.Timedelta(days=1), freq="MS")
    counts = rng.integers(300, 601, size=len(months))
    month_of_row = np.repeat(np.arange(len(months)), counts)
    total = len(month_of_row)
    components = np.sort(plant_of.index.to_numpy())
    material_no = rng.choice(components, size=total)
    offset = rng.integers(0, 28, size=total)
    order_date = pd.to_datetime(months[month_of_row]) + pd.to_timedelta(offset, unit="D")
    # Spares sell at a markup over the part's supplier price.
    markup = rng.uniform(1.35, 2.0, size=total)
    return pd.DataFrame(
        {
            "order_date": order_date,
            "customer_no": rng.choice(customers, size=total),
            "material_no": material_no,
            "qty": np.ones(total, dtype=int),
            "unit_price": np.round(prices.loc[material_no].to_numpy(dtype=float) * markup, 2),
            "protected": False,
            "bay_id": pd.NA,
            "kind": "spare",
            "plant_code": plant_of.loc[material_no].to_numpy(),
            "family_code": pd.NA,
        }
    )


def _apply_status(rng: np.random.Generator, lines: pd.DataFrame) -> pd.DataFrame:
    frame = lines.sort_values(["order_date", "customer_no", "material_no", "kind"]).reset_index(
        drop=True
    )
    historical = frame["order_date"] <= AS_OF_TS
    eligible = np.flatnonzero(historical.to_numpy() & ~frame["protected"].to_numpy())
    n_cancel = int(round(0.02 * len(frame)))
    chosen = rng.choice(eligible, size=min(n_cancel, int(eligible.size)), replace=False)
    cancelled = np.zeros(len(frame), dtype=bool)
    cancelled[chosen] = True
    status = np.full(len(frame), "Open", dtype=object)
    status[frame["promised_date"].le(AS_OF_TS).to_numpy()] = "Shipped"
    status[cancelled] = "Cancelled"
    frame["status"] = status
    frame["shipped_date"] = pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns]")
    shipped = frame["status"] == "Shipped"
    frame.loc[shipped, "shipped_date"] = frame.loc[shipped, "promised_date"]
    # Service tiers have different spare fulfilment reliability.
    # Delays are planned before procurement and stock movements are generated.
    spare = frame["kind"].eq("spare") & ~cancelled
    for segment, late_share in (("Foundry", 0.08), ("Logic", 0.18), ("Memory", 0.30)):
        indexes = frame.index[spare & frame["customer_no"].map(_segment).eq(segment)]
        late_at = indexes[np.arange(len(indexes)) % 100 < round(late_share * 100)]
        delivery = frame.loc[late_at, "promised_date"] + pd.to_timedelta(
            2 + np.arange(len(late_at)) % 10, unit="D"
        )
        frame.loc[late_at, "shipped_date"] = delivery.where(delivery.lt(AS_OF_TS))
        frame.loc[late_at[delivery.ge(AS_OF_TS)], "status"] = "Open"
    key = frame["customer_no"].astype(str) + "|" + frame["order_date"].dt.strftime("%Y-%m-%d")
    frame["so_no"] = _keyed_ids(key, "SO-", 7)
    frame["line_no"] = frame.groupby("so_no", sort=False).cumcount() + 1
    return frame


def _production_orders(lines: pd.DataFrame) -> pd.DataFrame:
    tools = lines[(lines["kind"] == "tool") & (lines["status"] != "Cancelled")].copy()
    tools["planned_start"] = tools["promised_date"] - pd.to_timedelta(
        tools["family_code"].map(BUILD_DAYS), unit="D"
    )
    tools["planned_finish"] = tools["promised_date"] - pd.Timedelta(days=PROMISE_GAP)
    finished = tools["planned_finish"] <= AS_OF_TS
    started = tools["planned_start"] <= AS_OF_TS
    tools["prod_status"] = np.where(finished, "Completed", np.where(started, "Released", "Planned"))
    tools["actual_finish"] = pd.Series(pd.NaT, index=tools.index, dtype="datetime64[ns]")
    tools.loc[finished, "actual_finish"] = tools.loc[finished, "planned_finish"]
    tools = tools.sort_values(["planned_start", "so_no", "line_no"]).reset_index(drop=True)
    tools["prod_order_no"] = _row_ids(len(tools), "PR-", 7)
    tools["so_line_no"] = tools["line_no"].astype(int)
    return tools


def _forecast(master: dict[str, pd.DataFrame], lines: pd.DataFrame) -> pd.DataFrame:
    tool_rows = master["material"]["material_type"].eq("TOOL")
    tools = master["material"].loc[tool_rows, "material_no"]
    history = lines[
        (lines["kind"] == "tool")
        & (lines["status"] != "Cancelled")
        & (lines["order_date"] < AS_OF_TS)
    ]
    base = history.groupby("material_no").size().reindex(tools, fill_value=0)
    base = (base / 60).round().clip(lower=1).astype(int)
    months = pd.date_range("2026-11-01", "2028-04-01", freq="MS")
    frame = pd.MultiIndex.from_product(
        [["2026-09", "2026-10"], list(tools), list(months)],
        names=["forecast_version", "material_no", "forecast_month"],
    ).to_frame(index=False)
    frame["qty"] = frame["material_no"].map(base).astype(int)
    etch = set(
        master["material"].loc[
            tool_rows & master["material"]["family_code"].eq("ETC"), "material_no"
        ]
    )
    bump = frame["forecast_version"].eq("2026-10") & frame["material_no"].isin(etch)
    frame.loc[bump, "qty"] = frame.loc[bump, "qty"] + 1
    return frame


def _component_demand(lines, production, leaf) -> tuple[pd.DataFrame, pd.DataFrame]:
    per_tool = leaf.rename(columns={"qty": "per_tool", "material_no": "component_no"})
    issued = production.merge(per_tool, left_on="material_no", right_on="root", how="inner")
    component_qty = (issued["qty"] * issued["per_tool"]).round().astype(int)
    production_demand = pd.DataFrame(
        {
            "material_no": issued["component_no"].to_numpy(),
            "plant_code": issued["plant_code"].to_numpy(),
            "qty": component_qty.to_numpy(),
            "need_date": issued["planned_start"].to_numpy(),
            "booking_date": issued["order_date"].to_numpy(),
        }
    )
    spares = lines[(lines["kind"] == "spare") & (lines["status"] != "Cancelled")]
    spare_demand = pd.DataFrame(
        {
            "material_no": spares["material_no"].to_numpy(),
            "plant_code": spares["plant_code"].to_numpy(),
            "qty": spares["qty"].to_numpy(),
            "need_date": spares["shipped_date"].fillna(spares["promised_date"]).to_numpy(),
            "booking_date": spares["order_date"].to_numpy(),
        }
    )
    production_demand["use"] = "issue"
    spare_demand["use"] = "ship"
    demand = pd.concat([production_demand, spare_demand], ignore_index=True)
    demand["need_date"] = pd.to_datetime(demand["need_date"])
    due_issue = demand["use"].eq("issue") & demand["need_date"].le(AS_OF_TS)
    issues = pd.DataFrame(
        {
            "material_no": demand.loc[due_issue, "material_no"].to_numpy(),
            "plant_code": demand.loc[due_issue, "plant_code"].to_numpy(),
            "storage_location": COMPONENT_LOCATION,
            "movement_type": "261",
            "qty": -demand.loc[due_issue, "qty"].to_numpy(),
            "posting_date": demand.loc[due_issue, "need_date"].to_numpy(),
        }
    )
    return demand, issues


def _supply(rng, master, demand: pd.DataFrame) -> dict[str, pd.DataFrame]:
    orders = _purchase_orders(rng, master, demand)
    receipts = _goods_receipts(orders["lines"])
    notifications = _quality_notifications(receipts)
    return {
        "purchase_order": orders["headers"],
        "purchase_order_item": orders["items"],
        "goods_receipt": receipts,
        "quality_notification": notifications,
        "opening": orders["opening"],
    }


def _purchase_orders(rng, master, demand: pd.DataFrame) -> dict[str, pd.DataFrame]:
    primary = (
        master["material_supplier"]
        .loc[master["material_supplier"]["is_primary"]]
        .sort_values(["material_no", "supplier_no"])
        .drop_duplicates("material_no")
    )
    rf_materials = set(
        master["material"].loc[
            master["material"]["description"].str.startswith("RF generator,"), "material_no"
        ]
    )
    planning = master["material_plant"].merge(primary, on="material_no")
    planning = planning.set_index(["material_no", "plant_code"])
    daily = demand.groupby(["material_no", "plant_code", "need_date"], as_index=False).agg(
        qty=("qty", "sum"), booking_date=("booking_date", "min")
    )
    rows = []
    for (material, plant), needs in daily.groupby(["material_no", "plant_code"], sort=True):
        parameters = planning.loc[(material, plant)]
        safety = int(parameters["safety_stock"])
        lead = int(parameters["planned_lead_time_days"])
        supplier = parameters["supplier_no"]
        stock = 0
        # Close history at the snapshot before planning the forward horizon.
        # This avoids buying forward RF demand into the snapshot's safety stock.
        for historical in (True, False):
            segment = needs[needs["need_date"].le(AS_OF_TS).eq(historical)]
            dated = [
                (day.date(), int(qty))
                for day, qty in segment[["need_date", "qty"]].itertuples(index=False, name=None)
            ]
            buffer = (
                SLIP_DAYS + RECEIPT_BUFFER_DAYS
                if supplier == CORVANE_SUPPLIER_NO
                else RECEIPT_BUFFER_DAYS
            )
            if not historical and material in rf_materials:
                buffer = RF_BUFFER_DAYS
            receipts = plan_purchases(dated, stock, safety, lead, buffer, LOT_DAYS)
            for receipt in receipts:
                need = receipt.requested_date + timedelta(days=buffer)
                booking = pd.NaT
                if receipt.order_date > AS_OF_DATE:
                    covered = segment["need_date"].ge(pd.Timestamp(need)) & segment["need_date"].lt(
                        pd.Timestamp(need + timedelta(days=LOT_DAYS))
                    )
                    booking = segment.loc[covered, "booking_date"].min()
                rows.append(
                    {
                        "material_no": material,
                        "plant_code": plant,
                        "supplier_no": supplier,
                        "unit_price": parameters["unit_price"],
                        "qty": receipt.qty,
                        "requested": receipt.requested_date,
                        "order_date": receipt.order_date,
                        "booking_date": booking,
                        "need_date": receipt.requested_date + timedelta(days=buffer),
                        "historical": historical,
                    }
                )
            stock += sum(r.qty for r in receipts) - sum(q for _, q in dated)
    grouped = pd.DataFrame(rows)
    for column in ("requested", "order_date", "need_date"):
        grouped[column] = pd.to_datetime(grouped[column])
    slip = grouped["supplier_no"].eq(CORVANE_SUPPLIER_NO) & grouped["order_date"].ge(SLIP_TS)
    grouped["confirmed"] = grouped["requested"]
    grouped.loc[slip, "confirmed"] += pd.Timedelta(days=SLIP_DAYS)
    # Forward requirements remain outstanding at the snapshot; confirmations
    # can use the remaining buffer after it, without moving any business event.
    forward = ~grouped["historical"]
    first_day = AS_OF_TS + pd.Timedelta(days=1)
    clipped = forward & grouped["confirmed"].lt(first_day)
    grouped.loc[forward, "confirmed"] = grouped.loc[forward, "confirmed"].clip(lower=first_day)
    # Spread the clipped confirmations over the first week, from the snapshot day on,
    # filling the quietest allowed day first, so no single day holds all of them.
    # A confirmation never lands after the need date, so no shortage appears.
    spread = clipped & ~grouped["supplier_no"].eq(CORVANE_SUPPLIER_NO)
    grouped.loc[spread, "confirmed"] = _level_days(
        grouped.loc[~spread, "confirmed"],
        (grouped.loc[spread, "need_date"] - AS_OF_TS).dt.days.to_numpy(),
        AS_OF_TS,
    )
    # Three lines are confirmed on 27 December 2026, 56 days after the snapshot.
    # That day is a Sunday, so it sits in the Monday-start week of 21 December,
    # earlier than the week of the neighbouring confirmations.
    # "Before" and "on or before" that week edge therefore differ.
    edge = AS_OF_TS + pd.Timedelta(days=56)
    near = grouped.index[
        forward
        & ~grouped["supplier_no"].eq(CORVANE_SUPPLIER_NO)
        & grouped["confirmed"].gt(edge)
        & grouped["confirmed"].lt(edge + pd.Timedelta(days=6))
    ]
    grouped.loc[near[:3], "confirmed"] = edge
    grouped = _set_on_time(rng, grouped)
    # Lots that would require pre-history procurement are opening inventory.
    opening = grouped.loc[grouped["order_date"] < pd.Timestamp(HISTORY_START)].copy()
    grouped = grouped.loc[grouped["order_date"] >= pd.Timestamp(HISTORY_START)].copy()
    # Earlier buyer release does not remove the supplier's existing delivery delay.
    grouped["order_date"] = _backlog_order_dates(
        rng, grouped["order_date"], grouped["booking_date"]
    )
    grouped["order_qty"] = grouped["qty"].round().astype(int)
    # Some forward lots have overdue confirmations while the buyer waits for
    # supply not yet needed by production. Existing historical supply covers history.
    eligible = grouped.index[
        ~grouped["historical"]
        & ~grouped["supplier_no"].eq(CORVANE_SUPPLIER_NO)
        & grouped["order_date"].lt(AS_OF_TS - pd.Timedelta(days=1))
    ]
    past_count = grouped["confirmed"].lt(AS_OF_TS).sum()
    overdue_count = int(round(past_count * 0.03 / 0.97))
    overdue = rng.choice(eligible, size=min(overdue_count, len(eligible)), replace=False)
    overdue_dates = AS_OF_TS - pd.to_timedelta(rng.integers(1, 61, size=len(overdue)), unit="D")
    grouped.loc[overdue, "confirmed"] = pd.Series(overdue_dates, index=overdue).clip(
        lower=grouped.loc[overdue, "order_date"] + pd.Timedelta(days=1)
    )
    grouped.loc[overdue, "requested"] = grouped.loc[overdue, "confirmed"]
    grouped.loc[overdue, "is_open"] = True
    remaining = grouped.index.difference(overdue)
    today_pool = remaining[
        grouped.loc[remaining, "confirmed"].gt(AS_OF_TS)
        & ~grouped.loc[remaining, "supplier_no"].eq(CORVANE_SUPPLIER_NO)
    ]
    due_today = today_pool[:30] if len(today_pool) >= 30 else []
    grouped.loc[due_today, "confirmed"] = AS_OF_TS
    grouped.loc[due_today, "requested"] = AS_OF_TS
    grouped["partial_qty"] = 0
    future = grouped.index[grouped["confirmed"].ge(AS_OF_TS)]
    partial_pool = future[
        grouped.loc[future, "order_qty"].ge(2)
        & ~grouped.loc[future, "supplier_no"].eq(CORVANE_SUPPLIER_NO)
        & grouped.loc[future, "order_date"].lt(AS_OF_TS)
    ]
    partial = rng.choice(
        partial_pool, size=min(round(len(future) * 0.15), len(partial_pool)), replace=False
    )
    grouped.loc[partial, "partial_qty"] = 1
    overdue_partial = grouped.loc[overdue].index[grouped.loc[overdue, "order_qty"].ge(2)][::4]
    grouped.loc[overdue_partial, "partial_qty"] = 1
    grouped["uom"] = "EA"
    # Annual price agreements drift gently; secondary quotes carry their own price.
    sources = master["material_supplier"].loc[
        ~master["material_supplier"]["is_primary"]
        & master["material_supplier"]["supplier_no"].ne(CORVANE_SUPPLIER_NO)
    ]
    # Corvane match-network commitments use its buffered primary procurement
    # plan; a spot buy must not introduce its slip after the lot was planned.
    # Use qualified, active vendors for occasional spot buys, rather than making
    # a supplier's entire delivery record a single opportunistic shipment.
    activity = grouped.loc[~grouped["is_open"], "supplier_no"].value_counts()
    sources = sources.loc[sources["supplier_no"].isin(activity.index[activity >= 20])]
    secondary = sources.drop_duplicates("material_no").set_index("material_no")
    secondary_buy = grouped["material_no"].isin(secondary.index) & (rng.random(len(grouped)) < 0.08)
    grouped.loc[secondary_buy, "supplier_no"] = grouped.loc[secondary_buy, "material_no"].map(
        secondary["supplier_no"]
    )
    if secondary_buy.any():
        grouped.loc[secondary_buy, "unit_price"] = grouped.loc[secondary_buy, "material_no"].map(
            secondary["unit_price"]
        )
    grouped["net_price"] = (
        grouped["unit_price"] * (1 + 0.018 * (grouped["order_date"].dt.year - 2021))
    ).round(2)
    header_key = (
        grouped["supplier_no"].astype(str)
        + "|"
        + grouped["plant_code"].astype(str)
        + "|"
        + grouped["order_date"].dt.strftime("%Y-%m-%d")
    )
    grouped["po_no"] = _keyed_ids(header_key, "PO-", 7)
    grouped = grouped.sort_values(["po_no", "material_no", "requested"]).reset_index(drop=True)
    grouped["line_no"] = grouped.groupby("po_no", sort=False).cumcount() + 1
    headers = grouped.groupby("po_no", as_index=False).agg(
        supplier_no=("supplier_no", "first"),
        plant_code=("plant_code", "first"),
        order_date=("order_date", "first"),
    )
    items = grouped.rename(
        columns={"requested": "requested_delivery_date", "confirmed": "confirmed_delivery_date"}
    )
    return {"headers": headers, "items": items, "lines": grouped, "opening": opening}


def _backlog_order_dates(
    rng: np.random.Generator, planned: pd.Series, booked: pd.Series
) -> pd.Series:
    """Release future backlog purchases on working days after booking processing.

    A purchase lot is initiated by the earliest customer booking it covers.
    Processing takes 3-10 calendar days, followed by a seeded working-day release
    before the snapshot; the earlier date only adds procurement lead-time slack.
    """
    actual = planned.copy()
    future = planned.gt(AS_OF_TS)
    if not future.any():
        return actual
    bookings = pd.to_datetime(booked.loc[future])
    if bookings.isna().any():
        raise ValueError("Backlog purchase is missing its customer booking date")
    booking_days = bookings.to_numpy(dtype="datetime64[D]")
    last = np.busday_offset(np.datetime64(AS_OF_DATE), 0, roll="backward")
    max_processing = np.minimum(10, (last - booking_days).astype(int))
    if (max_processing < 3).any():
        raise ValueError("Backlog booking leaves no buyer processing time before the snapshot")
    processing = rng.integers(3, max_processing + 1)
    first = np.busday_offset(booking_days + processing, 0, roll="forward")
    working_days = np.busday_count(first, last + np.timedelta64(1, "D"))
    release = np.busday_offset(first, rng.integers(0, working_days))
    actual.loc[future] = pd.to_datetime(release)
    return actual


def _level_days(settled: pd.Series, room: np.ndarray, first_day: pd.Timestamp) -> np.ndarray:
    """Give each line a day in the first week, no later than its room (days from `first_day`).

    Lines with the least room choose first. Each takes its quietest allowed day.
    """
    days = pd.date_range(first_day, periods=CONFIRM_SPREAD_DAYS)
    totals = [int(settled.eq(day).sum()) for day in days]
    last = np.clip(room, 0, CONFIRM_SPREAD_DAYS - 1)
    chosen = np.zeros(len(room), dtype=int)
    for position in np.argsort(last, kind="stable"):
        allowed = range(int(last[position]) + 1)
        quietest = min(allowed, key=lambda index: (totals[index], index))
        totals[quietest] += 1
        chosen[position] = quietest
    return (first_day + pd.to_timedelta(chosen, unit="D")).to_numpy()


def _set_on_time(rng, grouped: pd.DataFrame) -> pd.DataFrame:
    frame = grouped.copy()
    frame["receipt_date"] = frame["confirmed"]
    closed = frame["historical"]
    suppliers = np.sort(frame["supplier_no"].dropna().unique())
    code_of = pd.Series(np.arange(len(suppliers)), index=suppliers)
    codes = frame["supplier_no"].map(code_of).to_numpy()
    targets = rng.uniform(0.78, 0.90, size=len(suppliers))
    on_time = np.zeros(len(frame), dtype=bool)
    closed_np = closed.to_numpy()
    for code, target in enumerate(targets):
        if suppliers[code] == CORVANE_SUPPLIER_NO:
            continue
        # Supply crunch in 2022, recovery in 2024, then normal service.
        for year, adjustment in (
            (2021, 0),
            (2022, -0.10),
            (2023, -0.03),
            (2024, 0.07),
            (2025, 0),
            (2026, 0),
        ):
            mask = closed_np & (codes == code) & frame["confirmed"].dt.year.eq(year).to_numpy()
            _mark_rate(on_time, mask, target + adjustment, band=True)
    corvane = frame["supplier_no"].eq(CORVANE_SUPPLIER_NO).to_numpy() & closed_np
    receipt = frame["confirmed"]
    _mark_rate(on_time, corvane & receipt.lt(EARLY_END).to_numpy(), 0.95, band=False)
    late_window = (
        corvane
        & (receipt + pd.Timedelta(days=RECEIPT_BUFFER_DAYS)).ge(SLIP_TS).to_numpy()
        & receipt.le(AS_OF_TS).to_numpy()
    )
    _mark_rate(on_time, late_window, 0.30, band=False)
    middle = corvane & ~(receipt.lt(EARLY_END).to_numpy() | late_window)
    _mark_rate(on_time, middle, 0.85, band=False)
    frame["on_time"] = on_time
    late = pd.to_timedelta(np.where(on_time, 0, RECEIPT_BUFFER_DAYS), unit="D")
    frame["receipt_date"] = frame["confirmed"] + late
    # Historical supplier delays are absorbed by the planning buffer.
    if (frame.loc[closed, "receipt_date"] > frame.loc[closed, "need_date"]).any():
        raise ValueError("Historical supply misses a fixed need date")
    frame["is_open"] = frame["receipt_date"] > AS_OF_TS
    return frame


def _mark_rate(on_time: np.ndarray, mask: np.ndarray, rate: float, *, band: bool) -> None:
    indexes = np.flatnonzero(mask)
    count = int(indexes.size)
    if count == 0:
        return
    chosen = int(round(rate * count))
    if band:
        low = math.ceil(0.70 * count - 1e-9)
        high = math.floor(0.98 * count + 1e-9)
        if low <= high:
            chosen = min(max(chosen, low), high)
    chosen = min(max(chosen, 0), count)
    on_time[indexes] = False
    on_time[indexes[:chosen]] = True


def _goods_receipts(lines: pd.DataFrame) -> pd.DataFrame:
    closed = lines.loc[~lines["is_open"]].reset_index(drop=True)
    ordinal = np.arange(len(closed))
    n_rec = 1 + ordinal % 3
    n_rec = np.where(closed["supplier_no"].eq(CORVANE_SUPPLIER_NO).to_numpy(), 1, n_rec)
    n_rec = np.minimum(n_rec, closed["order_qty"].to_numpy())
    repeated = np.repeat(np.arange(len(closed)), n_rec)
    part = np.arange(repeated.size) - np.repeat(np.cumsum(n_rec) - n_rec, n_rec)
    line_qty = closed["order_qty"].to_numpy()[repeated]
    parts = n_rec[repeated]
    split = line_qty // parts + (part < (line_qty % parts)).astype(int)
    receipts = closed.iloc[repeated].copy()
    receipts["qty"] = split
    # Split deliveries arrive on separate days before the final receipt.
    spacing = np.where(receipts["on_time"].to_numpy(), 1 + ordinal[repeated] % 10, 3)
    available_days = (receipts["receipt_date"] - receipts["order_date"]).dt.days.to_numpy()
    spacing = np.minimum(spacing, available_days // np.maximum(parts - 1, 1))
    receipts["receipt_date"] = receipts["receipt_date"] - pd.to_timedelta(
        (parts - part - 1) * spacing, unit="D"
    )
    receipts["receipt_date"] = receipts["receipt_date"].clip(lower=pd.Timestamp(HISTORY_START))
    partial = lines.loc[lines["partial_qty"].gt(0)].copy()
    partial["qty"] = partial["partial_qty"]
    partial["receipt_date"] = AS_OF_TS - pd.Timedelta(days=1)
    partial["on_time"] = partial["receipt_date"].le(partial["confirmed"])
    receipts = pd.concat([receipts, partial], ignore_index=True)
    # Receipt-weighted service levels remain meaningful even for small specialist
    # suppliers and secondary buys, whose split quantities vary by PO line.
    for supplier, indexes in receipts.groupby("supplier_no", sort=True).groups.items():
        if supplier == CORVANE_SUPPLIER_NO:
            continue
        group = receipts.loc[indexes]
        rate = group["receipt_date"].le(group["confirmed"]).mean()
        if 0.70 <= rate <= 0.98:
            continue
        target = 0.78 + (int(supplier[2:]) % 13) / 100
        late_count = len(group) - int(round(target * len(group)))
        eligible = group.index[
            ~group["is_open"] & group["confirmed"].le(AS_OF_TS - pd.Timedelta(days=7))
        ]
        late_at = eligible[:late_count]
        closed_at = group.index[~group["is_open"]]
        positions = group.groupby(["po_no", "line_no"]).cumcount()
        receipts.loc[closed_at, "receipt_date"] = receipts.loc[
            closed_at, "confirmed"
        ] - pd.to_timedelta(positions.loc[closed_at] * 3, unit="D")
        receipts.loc[late_at, "receipt_date"] = receipts.loc[
            late_at, "confirmed"
        ] + pd.to_timedelta(1 + positions.loc[late_at] * 3, unit="D")
        receipts.loc[indexes, "on_time"] = receipts.loc[indexes, "receipt_date"].le(
            receipts.loc[indexes, "confirmed"]
        )
    receipts["storage_location"] = COMPONENT_LOCATION
    receipts = receipts.sort_values(["receipt_date", "po_no", "line_no"]).reset_index(drop=True)
    receipts["gr_no"] = _row_ids(len(receipts), "GR-", 7)
    return receipts


def _quality_notifications(receipts: pd.DataFrame) -> pd.DataFrame:
    rates = receipts.groupby("supplier_no")["on_time"].mean()
    score = (1 - rates).clip(lower=0.01)
    probability = score / float(score.mean()) * 0.015
    counts = receipts.groupby("supplier_no").size()
    wanted = (probability * counts).round().astype(int)
    ordered = receipts.sort_values(["supplier_no", "receipt_date", "gr_no"]).reset_index(drop=True)
    place = ordered.groupby("supplier_no", sort=False).cumcount()
    picked = place.to_numpy() < ordered["supplier_no"].map(wanted).to_numpy()
    notes = ordered.loc[picked].copy()
    notes["created_on"] = notes["receipt_date"]
    notes["defect_code"] = np.array(["DIM", "SURF", "ELEC", "PACK"])[np.arange(len(notes)) % 4]
    notes["qty_affected"] = 1
    notes = notes.sort_values(["created_on", "supplier_no", "po_no"]).reset_index(drop=True)
    notes["notif_no"] = _row_ids(len(notes), "QN-", 7)
    return notes[
        [
            "notif_no",
            "supplier_no",
            "material_no",
            "po_no",
            "created_on",
            "defect_code",
            "qty_affected",
        ]
    ]


def _stock(
    lines,
    production,
    receipts,
    issues,
    opening,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    receipt_moves = pd.DataFrame(
        {
            "material_no": receipts["material_no"].to_numpy(),
            "plant_code": receipts["plant_code"].to_numpy(),
            "storage_location": COMPONENT_LOCATION,
            "movement_type": "101",
            "qty": receipts["qty"].to_numpy(),
            "posting_date": pd.to_datetime(receipts["receipt_date"]).to_numpy(),
        }
    )
    shipped = lines[lines["status"] == "Shipped"].copy()
    location = np.where(shipped["kind"].eq("tool"), TOOL_LOCATION, COMPONENT_LOCATION)
    shipments = pd.DataFrame(
        {
            "material_no": shipped["material_no"].to_numpy(),
            "plant_code": shipped["plant_code"].to_numpy(),
            "storage_location": location,
            "movement_type": "601",
            "qty": -shipped["qty"].to_numpy(),
            "posting_date": shipped["shipped_date"].to_numpy(),
        }
    )
    finished = production[production["prod_status"] == "Completed"]
    made = pd.DataFrame(
        {
            "material_no": finished["material_no"].to_numpy(),
            "plant_code": finished["plant_code"].to_numpy(),
            "storage_location": TOOL_LOCATION,
            "movement_type": "101",
            "qty": finished["qty"].to_numpy(),
            "posting_date": pd.to_datetime(finished["actual_finish"]).to_numpy(),
        }
    )
    initial = opening[["material_no", "plant_code", "qty"]].copy()
    initial["storage_location"] = COMPONENT_LOCATION
    initial["movement_type"] = "561"
    initial["posting_date"] = pd.Timestamp(HISTORY_START)
    movements = pd.concat([initial, receipt_moves, issues, shipments, made], ignore_index=True)
    movements["posting_date"] = pd.to_datetime(movements["posting_date"])
    movements = movements.sort_values(
        ["posting_date", "movement_type", "material_no", "plant_code"]
    ).reset_index(drop=True)
    movements["movement_no"] = _row_ids(len(movements), "MV-", 7)
    posted = movements[movements["posting_date"] <= AS_OF_TS]
    inventory = (
        posted.groupby(["material_no", "plant_code", "storage_location"], as_index=False)["qty"]
        .sum()
        .rename(columns={"qty": "qty_on_hand"})
    )
    inventory = inventory[inventory["qty_on_hand"] != 0].reset_index(drop=True)
    inventory["qty_on_hand"] = inventory["qty_on_hand"].astype(int)
    return movements[
        [
            "movement_no",
            "material_no",
            "plant_code",
            "storage_location",
            "movement_type",
            "qty",
            "posting_date",
        ]
    ], inventory


def _sales_orders(lines: pd.DataFrame) -> pd.DataFrame:
    return lines.groupby("so_no", as_index=False).agg(
        customer_no=("customer_no", "first"),
        order_date=("order_date", "first"),
    )


def _sales_items(lines: pd.DataFrame) -> pd.DataFrame:
    return lines[
        [
            "so_no",
            "line_no",
            "material_no",
            "qty",
            "unit_price",
            "requested_date",
            "promised_date",
            "status",
            "shipped_date",
        ]
    ]


def _production_frame(production: pd.DataFrame) -> pd.DataFrame:
    frame = production[
        [
            "prod_order_no",
            "material_no",
            "plant_code",
            "bay_id",
            "so_no",
            "so_line_no",
            "qty",
            "planned_start",
            "planned_finish",
            "actual_finish",
            "prod_status",
        ]
    ].copy()
    return frame.rename(columns={"prod_status": "status"})


def _keyed_ids(keys: pd.Series, prefix: str, width: int) -> pd.Series:
    codes, _uniques = pd.factorize(keys, sort=True)
    numbers = pd.Series(codes + 1, index=keys.index).astype(str).str.zfill(width)
    return prefix + numbers


def _row_ids(count: int, prefix: str, width: int) -> pd.Series:
    numbers = pd.Series(np.arange(1, count + 1)).astype(str).str.zfill(width)
    return prefix + numbers
