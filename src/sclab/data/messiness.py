"""Deliberate data-quality problems for the cleaning lessons."""

import numpy as np
import pandas as pd

from sclab.constants import AS_OF_DATE
from sclab.data.story import CORVANE_SUPPLIER_NO, SUPPLIER_WARNING_RAISES
from sclab.data.transactions import _tool_components


def apply_messiness(
    rng: np.random.Generator,
    frames: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    """Return a copy of the frames with the lab's planted data-quality issues."""
    out = {name: frame.copy() for name, frame in frames.items()}
    out["material"] = _duplicate_materials(rng, out["material"])
    # Retired configuration parts have no active BOM or spare demand.
    # Opening movements retain the inventory audit trail without snapshot receipts.
    idle = (
        frames["material"]
        .loc[
            frames["material"]["material_type"].eq("COMPONENT")
            & ~frames["material"]["is_long_lead"]
        ]
        .tail(30)
        .copy()
    )
    # Retired variants retain a qualified historical source from their active design.
    # Prefer its primary source, excluding Corvane's active RF-only catalogue.
    qualified = (
        frames["material_supplier"]
        .loc[frames["material_supplier"]["supplier_no"].ne(CORVANE_SUPPLIER_NO)]
        .sort_values(["material_no", "is_primary", "supplier_no"], ascending=[True, False, True])
        .drop_duplicates("material_no")
        .set_index("material_no")
    )
    historical = qualified.loc[idle["material_no"]].reset_index()
    next_no = int(out["material"]["material_no"].str[2:].astype(int).max()) + 1
    idle["material_no"] = [f"M-{number:06d}" for number in range(next_no, next_no + len(idle))]
    historical["material_no"] = idle["material_no"].to_numpy()
    historical["is_primary"] = True
    idle["description"] = idle["description"] + ", retired configuration"
    out["material"] = pd.concat([out["material"], idle], ignore_index=True)
    balances = pd.DataFrame(
        {
            "material_no": idle["material_no"].to_numpy(),
            "plant_code": "SC01",
            "storage_location": "RM01",
            "qty_on_hand": 12 + np.arange(len(idle)),
        }
    )
    movements = balances.rename(columns={"qty_on_hand": "qty"}).copy()
    start = len(out["stock_movement"]) + 1
    movements["movement_no"] = [f"MV-{number:07d}" for number in range(start, start + len(idle))]
    movements["movement_type"] = "561"
    movements["posting_date"] = pd.Timestamp("2021-11-01")
    out["stock_movement"] = pd.concat([out["stock_movement"], movements], ignore_index=True)
    out["inventory"] = pd.concat([out["inventory"], balances], ignore_index=True)
    out["material_plant"] = _raise_idle_safety_stock(frames, out["material_plant"])
    out["material_plant"] = _mess_lead_times(rng, out["material_plant"])
    out["purchase_order_item"] = _mess_uom(rng, out["purchase_order_item"])
    out["customer"] = _mess_country(rng, out["customer"])
    # Plant blanks only after planning has used the complete operational values.
    sources = out["material_supplier"]
    quotes = sources["quoted_lead_time_days"].astype("Int64")
    picked = rng.choice(sources.index, size=max(2, round(len(sources) * 0.01)), replace=False)
    quotes.loc[picked[: len(picked) // 2]] = pd.NA
    quotes.loc[picked[len(picked) // 2 :]] = 0
    sources["quoted_lead_time_days"] = quotes
    items = out["purchase_order_item"]
    non_rf = out["material"].loc[
        ~out["material"]["description"].str.startswith("RF generator,"), "material_no"
    ]
    pool = items.index[
        items["confirmed_delivery_date"].gt(pd.Timestamp("2026-11-01"))
        & items["material_no"].isin(non_rf)
    ]
    picked = rng.choice(pool, size=max(1, round(len(pool) * 0.01)), replace=False)
    # Unconfirmed contingency buys are not netted against committed demand.
    # Keep the dated covering lots intact so unknown dates cannot cause shortages.
    contingency = items.loc[picked].copy()
    contingency["order_qty"] = 1
    contingency["confirmed_delivery_date"] = pd.NaT
    last_line = items.groupby("po_no")["line_no"].max()
    contingency["line_no"] = (
        contingency["po_no"].map(last_line) + contingency.groupby("po_no").cumcount() + 1
    )
    out["purchase_order_item"] = pd.concat([items, contingency], ignore_index=True)
    sales = out["sales_order_item"]
    # Cancelled bookings retain the requested date but may have no promise.
    pool = sales.index[sales["status"].eq("Cancelled")]
    picked = rng.choice(pool, size=max(1, round(len(pool) * 0.1)), replace=False)
    sales.loc[picked, "promised_date"] = pd.NaT
    # Append historical quotes after planting blanks to preserve the seeded draws.
    out["material_supplier"] = pd.concat([out["material_supplier"], historical], ignore_index=True)
    return out


def _raise_idle_safety_stock(frames: dict[str, pd.DataFrame], planning: pd.DataFrame):
    """Plant the supplier-warning scenario behind the lesson 9 shortage trap.

    After a supplier warning, buyers raised the safety stock of idle long-lead parts.
    Idle means built into a tool at the plant, with no open production order using
    the part in the next 26 weeks, so nothing replenishes it.
    The first idle pairs in material number order are raised above on-hand stock by
    SUPPLIER_WARNING_RAISES units, which leaves them below safety stock with no demand.
    """
    start = pd.Timestamp(AS_OF_DATE)
    end = start + pd.Timedelta(weeks=26)
    production = frames["production_order"]
    window = production[
        production["status"].isin(["Planned", "Released"])
        & (production["planned_start"] > start)
        & (production["planned_start"] < end)
    ]
    leaf = _tool_components(frames)[["root", "material_no"]]
    demand = window[["material_no", "plant_code"]].rename(columns={"material_no": "root"})
    demand = demand.merge(leaf, on="root")[["material_no", "plant_code"]].drop_duplicates()
    material = frames["material"].set_index("material_no")
    long_lead = material["is_long_lead"] & ~material["description"].str.startswith("RF generator,")
    stock = (
        frames["inventory"]
        .loc[frames["inventory"]["storage_location"].eq("RM01")]
        .groupby(["material_no", "plant_code"])["qty_on_hand"]
        .sum()
    )
    frame = planning.copy()
    idle = (
        frame["material_no"].map(long_lead).fillna(False).astype(bool)
        & frame["safety_stock"].gt(0)
        & ~pd.MultiIndex.from_frame(frame[["material_no", "plant_code"]]).isin(
            pd.MultiIndex.from_frame(demand)
        )
    )
    chosen = (
        frame.loc[idle]
        .sort_values(["material_no", "plant_code"])
        .index[: len(SUPPLIER_WARNING_RAISES)]
    )
    if len(chosen) == 0:
        raise RuntimeError("No idle long-lead part is available for the supplier-warning scenario")
    for index, raise_by in zip(chosen, SUPPLIER_WARNING_RAISES, strict=False):
        key = (frame.at[index, "material_no"], frame.at[index, "plant_code"])
        frame.at[index, "safety_stock"] = int(stock.get(key, 0)) + raise_by
    return frame


def _count_for_ratio(total: int, target: float, low: float, high: float) -> int:
    count = max(1, int(round(target * total)))
    while count / total < low:
        count += 1
    while count > 1 and count / total > high:
        count -= 1
    return count


def _duplicate_materials(rng: np.random.Generator, materials: pd.DataFrame) -> pd.DataFrame:
    original = len(materials)
    duplicates = max(1, int(round(0.015 * original / 0.985)))
    while not 0.01 <= duplicates / (original + duplicates) <= 0.02:
        ratio = duplicates / (original + duplicates)
        duplicates += 1 if ratio < 0.01 else -1
    pool = materials.loc[materials["material_type"] == "COMPONENT", "material_no"].to_numpy()
    picked = rng.choice(pool, size=duplicates, replace=False)
    copies = materials.set_index("material_no").loc[picked].reset_index()
    start = int(materials["material_no"].str[2:].astype(int).max()) + 1
    copies["material_no"] = [f"M-{number:06d}" for number in range(start, start + duplicates)]
    copies["description"] = copies["description"].astype(str) + " "
    # A legacy part keyed twice with different trailing spaces, and never with the clean text.
    # It matches after TRIM but is not a copy of any real row, so it must not be removed.
    stray = copies.iloc[:2].copy()
    stray["material_no"] = [
        f"M-{number:06d}" for number in range(start + duplicates, start + duplicates + 2)
    ]
    stray["description"] = ["Legacy gasket kit, variant 1 ", "Legacy gasket kit, variant 1  "]
    stray["is_long_lead"] = False
    return pd.concat([materials, copies, stray], ignore_index=True)


def _mess_lead_times(rng: np.random.Generator, planning: pd.DataFrame) -> pd.DataFrame:
    frame = planning.copy()
    total = len(frame)
    nulls = _count_for_ratio(total, 0.03, 0.02, 0.04)
    zeros = _count_for_ratio(total, 0.0075, 0.005, 0.01)
    indexes = np.arange(total)
    null_at = rng.choice(indexes, size=nulls, replace=False)
    remaining = np.setdiff1d(indexes, null_at, assume_unique=True)
    zero_at = rng.choice(remaining, size=zeros, replace=False)
    lead = frame["planned_lead_time_days"].astype("Int64")
    lead.iloc[null_at] = pd.NA
    lead.iloc[zero_at] = 0
    frame["planned_lead_time_days"] = lead
    return frame


def _mess_uom(rng: np.random.Generator, items: pd.DataFrame) -> pd.DataFrame:
    frame = items.copy()
    total = len(frame)
    dirty = _count_for_ratio(total, 0.025, 0.02, 0.03)
    picked = np.sort(rng.choice(total, size=dirty, replace=False))
    uom = np.full(total, "EA", dtype=object)
    half = dirty // 2
    uom[picked[:half]] = "ea"
    uom[picked[half:]] = "EA "
    frame["uom"] = uom
    return frame


def _mess_country(rng: np.random.Generator, customers: pd.DataFrame) -> pd.DataFrame:
    frame = customers.copy()
    dirty = max(1, int(round(0.01 * len(frame))))
    picked = rng.choice(len(frame), size=dirty, replace=False)
    country = frame["country"].astype(str).to_numpy()
    country[picked] = np.char.add(country[picked], " ")
    frame["country"] = country
    return frame
