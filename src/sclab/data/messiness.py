"""Deliberate data-quality problems for the cleaning lessons."""

import numpy as np
import pandas as pd


def apply_messiness(
    rng: np.random.Generator,
    frames: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    """Return a copy of the frames with the lab's planted data-quality issues."""
    out = {name: frame.copy() for name, frame in frames.items()}
    out["material"] = _duplicate_materials(rng, out["material"])
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
    non_rf = out["material"].loc[~out["material"]["description"].eq("RF generator"), "material_no"]
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
    return out


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
    return pd.concat([materials, copies], ignore_index=True)


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
