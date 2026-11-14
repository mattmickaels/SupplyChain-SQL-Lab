"""Sales, production, purchasing, stock, and quality for the lab database."""

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

from sclab.constants import AS_OF_DATE
from sclab.data.mrp import plan_purchases
from sclab.data.story import (
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
RF_BUFFER_DAYS = 49
RECEIPT_BUFFER_DAYS = 7
COMPONENT_LOCATION = "RM01"
TOOL_LOCATION = "FG01"
BUILD_DAYS = {"DEP": 56, "ETC": 70, "CMP": 84, "INS": 42}
PROMISE_GAP = 7
HOME_PLANT = {"DEP": "AU01", "ETC": "SC01", "CMP": "SG01", "INS": "AU01"}


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
    movements, inventory = _stock(lines, production, supply["goods_receipt"], issues)
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
    counts, halcyon_n = _month_counts(months)
    normal = _normal_tool_lines(rng, months, counts, halcyon_n, tool_ids, etch_ids, customers)
    normal["promised_date"] = normal["order_date"] + pd.to_timedelta(
        rng.integers(90, 366, size=len(normal)), unit="D"
    )
    tools_frame = _schedule_overload(normal, master, etch_ids)
    tools_frame["family_code"] = tools_frame["material_no"].map(family_of)
    tools_frame["plant_code"] = tools_frame["family_code"].map(HOME_PLANT)
    missing_bay = tools_frame["bay_id"].isna()
    tools_frame.loc[missing_bay, "bay_id"] = _assign_bays(
        tools_frame.loc[missing_bay, ["plant_code", "family_code"]],
        master["build_bay"],
    )
    spares = _spare_lines(rng, plant_of, customers)
    spares["promised_date"] = spares["order_date"] + pd.Timedelta(days=21)
    lines = pd.concat([tools_frame, spares], ignore_index=True)
    lines["order_date"] = pd.to_datetime(lines["order_date"])
    lines["requested_date"] = lines["promised_date"]
    return lines


def _month_counts(months: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray]:
    index = np.arange(len(months))
    counts = np.rint(28 + 14 * index / (len(months) - 1)).astype(int)
    counts[months.year == 2023] = np.maximum(counts[months.year == 2023] - 8, 25)
    counts = np.clip(counts, 25, 45)
    halcyon = np.zeros(len(months), dtype=int)
    halcyon[(months.year == 2025) & months.month.isin([7, 8, 9, 10])] = 4
    halcyon[(months.year == 2026) & months.month.isin([7, 8, 9, 10])] = 8
    return counts, halcyon


def _normal_tool_lines(rng, months, counts, halcyon_n, tool_ids, etch_ids, customers):
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
    return pd.DataFrame(
        {
            "order_date": order_date,
            "customer_no": customer_no,
            "material_no": tool_no,
            "qty": np.ones(total, dtype=int),
            "unit_price": rng.integers(500_000, 1_500_000, size=total),
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


def _spare_lines(rng, plant_of: pd.Series, customers: np.ndarray) -> pd.DataFrame:
    months = pd.date_range(HISTORY_MONTH, AS_OF_TS - pd.Timedelta(days=1), freq="MS")
    counts = rng.integers(300, 601, size=len(months))
    month_of_row = np.repeat(np.arange(len(months)), counts)
    total = len(month_of_row)
    components = np.sort(plant_of.index.to_numpy())
    material_no = rng.choice(components, size=total)
    offset = rng.integers(0, 28, size=total)
    order_date = pd.to_datetime(months[month_of_row]) + pd.to_timedelta(offset, unit="D")
    return pd.DataFrame(
        {
            "order_date": order_date,
            "customer_no": rng.choice(customers, size=total),
            "material_no": material_no,
            "qty": np.ones(total, dtype=int),
            "unit_price": rng.integers(50, 5000, size=total),
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
        }
    )
    spares = lines[(lines["kind"] == "spare") & (lines["status"] != "Cancelled")]
    spare_demand = pd.DataFrame(
        {
            "material_no": spares["material_no"].to_numpy(),
            "plant_code": spares["plant_code"].to_numpy(),
            "qty": spares["qty"].to_numpy(),
            "need_date": spares["shipped_date"].fillna(spares["promised_date"]).to_numpy(),
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
    }


def _purchase_orders(rng, master, demand: pd.DataFrame) -> dict[str, pd.DataFrame]:
    primary = (
        master["material_supplier"]
        .loc[master["material_supplier"]["is_primary"]]
        .sort_values(["material_no", "supplier_no"])
        .drop_duplicates("material_no")
    )
    rf_materials = set(
        master["material"].loc[master["material"]["description"].eq("RF generator"), "material_no"]
    )
    planning = master["material_plant"].merge(primary, on="material_no")
    planning = planning.set_index(["material_no", "plant_code"])
    daily = demand.groupby(["material_no", "plant_code", "need_date"], as_index=False)["qty"].sum()
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
                rows.append(
                    {
                        "material_no": material,
                        "plant_code": plant,
                        "supplier_no": supplier,
                        "unit_price": parameters["unit_price"],
                        "qty": receipt.qty,
                        "requested": receipt.requested_date,
                        "order_date": min(receipt.order_date, AS_OF_DATE),
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
    grouped.loc[forward, "confirmed"] = grouped.loc[forward, "confirmed"].clip(
        lower=AS_OF_TS + pd.Timedelta(days=1)
    )
    grouped = _set_on_time(rng, grouped)
    grouped["order_qty"] = grouped["qty"].round().astype(int)
    grouped["uom"] = "EA"
    grouped["net_price"] = grouped["unit_price"].round(2)
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
    return {"headers": headers, "items": items, "lines": grouped}


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
        _mark_rate(on_time, closed_np & (codes == code), target, band=True)
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
    supplier_ordinal = pd.factorize(closed["supplier_no"], sort=True)[0]
    min_qty = closed.groupby("supplier_no")["order_qty"].transform("min").to_numpy()
    n_rec = np.where(min_qty >= 3, 1 + (supplier_ordinal % 3), 1)
    n_rec = np.where(closed["supplier_no"].eq(CORVANE_SUPPLIER_NO).to_numpy(), 1, n_rec)
    n_rec = np.minimum(n_rec, closed["order_qty"].to_numpy())
    repeated = np.repeat(np.arange(len(closed)), n_rec)
    part = np.arange(repeated.size) - np.repeat(np.cumsum(n_rec) - n_rec, n_rec)
    line_qty = closed["order_qty"].to_numpy()[repeated]
    parts = n_rec[repeated]
    split = line_qty // parts + (part < (line_qty % parts)).astype(int)
    receipts = closed.iloc[repeated].copy()
    receipts["qty"] = split
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
    movements = pd.concat([receipt_moves, issues, shipments, made], ignore_index=True)
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
