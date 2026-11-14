"""Deterministic master data for plants, materials, sources, customers, and BOMs."""

from datetime import timedelta

import numpy as np
import pandas as pd

from sclab.constants import AS_OF_DATE, HISTORY_START
from sclab.data.schema import table
from sclab.data.story import CORVANE_SUPPLIER_NO, HALCYON_CUSTOMER_NO, OVERLOADED_BAY

PLANTS = (
    ("SC01", "Santa Clara", "Santa Clara", "US"),
    ("AU01", "Austin", "Austin", "US"),
    ("SG01", "Singapore", "Singapore", "SG"),
)
FAMILIES = (
    ("DEP", "Deposition"),
    ("ETC", "Etch"),
    ("CMP", "CMP"),
    ("INS", "Inspection"),
)
COUNTRIES = ("US", "JP", "DE", "KR", "TW", "SG", "NL", "CN", "IL")
LONG_LEAD_OTHER = (
    "dry vacuum pump",
    "wafer handling robot",
    "ceramic heater",
    "quartz liner",
    "gas box",
    "process controller",
    "turbo pump",
)
N_TOOLS = 40
N_MODULES = 300
N_PRIMARY_MODULES = 200
N_SUBASSEMBLIES = 1000
N_PARENT_SUBS = 800
N_COMPONENTS = 4000
N_LONG_LEAD = 240
N_RF = 12
N_SUPPLIERS = 150
N_CUSTOMERS = 25
N_BAYS = 5
_ADJECTIVES = (
    "Amber",
    "Bright",
    "Cedar",
    "Drift",
    "Ember",
    "Fern",
    "Harbor",
    "Ivory",
    "Juniper",
    "Kindred",
    "Maple",
    "North",
    "Opal",
    "Quiet",
    "River",
    "Silver",
    "Timber",
    "Violet",
    "Willow",
    "Cobalt",
    "Dusky",
    "Granite",
    "Heather",
    "Indigo",
    "Jasper",
    "Meadow",
    "Nimbus",
    "Orchard",
    "Pebble",
    "Rowan",
    "Summit",
    "Thistle",
    "Woven",
    "Azure",
    "Bramble",
    "Cinder",
    "Dorset",
    "Fable",
    "Gilder",
    "Hearth",
)
_NOUNS = (
    "Alloy",
    "Bearing",
    "Circuit",
    "Dynamics",
    "Forge",
    "Gauge",
    "Holdings",
    "Intake",
    "Junction",
    "Kinetics",
    "Metrics",
    "Optics",
    "Parts",
    "Relay",
    "Systems",
    "Devices",
    "Works",
    "Sensors",
    "Valves",
    "Controls",
)
_BANNED = (
    "applied",
    "tsmc",
    "samsung",
    "intel",
    "micron",
    "sk hynix",
    "lam",
    "kla",
    "tokyo electron",
    "asml",
    "globalfoundries",
    "texas instruments",
    "mks",
    "advanced energy",
    "edwards",
    "brooks",
)


def generate_master(rng: np.random.Generator) -> dict[str, pd.DataFrame]:
    tool_ids = [_material_no(i) for i in range(1, N_TOOLS + 1)]
    module_ids = [_material_no(i) for i in range(N_TOOLS + 1, N_TOOLS + N_MODULES + 1)]
    sub_start = N_TOOLS + N_MODULES + 1
    sub_ids = [_material_no(i) for i in range(sub_start, sub_start + N_SUBASSEMBLIES)]
    comp_start = sub_start + N_SUBASSEMBLIES
    comp_ids = [_material_no(i) for i in range(comp_start, comp_start + N_COMPONENTS)]
    primary_modules = module_ids[:N_PRIMARY_MODULES]
    child_modules = module_ids[N_PRIMARY_MODULES:]
    parent_subs = sub_ids[:N_PARENT_SUBS]
    child_subs = sub_ids[N_PARENT_SUBS:]
    supplier_ids = [f"V-{10001 + i:05d}" for i in range(N_SUPPLIERS)]

    module_subs = _assign_subassemblies(module_ids, parent_subs)
    sub_components = _assign_components(parent_subs + child_subs, comp_ids)
    edges = _bom_edges(
        tool_ids,
        primary_modules,
        child_modules,
        parent_subs,
        child_subs,
        module_subs,
        sub_components,
    )

    materials = _materials(rng, tool_ids, module_ids, sub_ids, comp_ids)
    return {
        "plant": _plants(),
        "product_family": _families(),
        "material": _frame("material", materials),
        "material_plant": _frame("material_plant", _material_plants(rng, materials)),
        "supplier": _frame("supplier", _suppliers(rng, supplier_ids)),
        "material_supplier": _frame(
            "material_supplier",
            _sources(rng, comp_ids, supplier_ids),
        ),
        "customer": _frame("customer", _customers(rng)),
        "bom_item": _frame("bom_item", _bom_rows(rng, edges)),
        "build_bay": _frame("build_bay", _bays(rng)),
    }


def _material_no(number: int) -> str:
    return f"M-{number:06d}"


def _day(rng: np.random.Generator):
    span = (AS_OF_DATE - HISTORY_START).days
    return HISTORY_START + timedelta(days=int(rng.integers(0, span)))


def _lead_days(rng: np.random.Generator, long_lead: bool) -> int:
    if long_lead:
        return int(rng.integers(120, 241))
    return int(rng.integers(14, 91))


def _safe_name(name: str) -> str:
    lowered = name.lower()
    for banned in _BANNED:
        if banned in lowered:
            raise ValueError(f"Name contains a real company marker: {name}")
    return name


def _pair_name(index: int) -> str:
    adjective = _ADJECTIVES[index % len(_ADJECTIVES)]
    noun = _NOUNS[(index // len(_ADJECTIVES)) % len(_NOUNS)]
    return _safe_name(f"{adjective} {noun}")


def _component_description(index: int) -> str:
    if index < N_RF:
        return "RF generator"
    if index < N_LONG_LEAD:
        return LONG_LEAD_OTHER[(index - N_RF) % len(LONG_LEAD_OTHER)]
    return f"Process fitting {index + 1}"


def _segment(customer_no: str) -> str:
    number = int(customer_no.split("-")[1])
    if 2003 <= number <= 2009:
        return "Memory"
    if number <= 2002 or 2010 <= number <= 2017:
        return "Foundry"
    return "Logic"


def _plants() -> pd.DataFrame:
    rows = [
        {"plant_code": code, "name": name, "city": city, "country": country}
        for code, name, city, country in PLANTS
    ]
    return _frame("plant", rows)


def _families() -> pd.DataFrame:
    rows = [{"family_code": code, "name": name} for code, name in FAMILIES]
    return _frame("product_family", rows)


def _materials(rng, tool_ids, module_ids, sub_ids, comp_ids) -> list[dict]:
    rows: list[dict] = []
    for index, material_no in enumerate(tool_ids):
        code, family_name = FAMILIES[index // 10]
        chambers = 2 + (index % 5)
        rows.append(
            {
                "material_no": material_no,
                "description": f"{family_name} system, 300mm, {chambers}-chamber",
                "material_type": "TOOL",
                "family_code": code,
                "base_uom": "EA",
                "is_long_lead": False,
                "created_on": _day(rng),
            }
        )
    for index, material_no in enumerate(module_ids):
        _code, family_name = FAMILIES[index % 4]
        rows.append(
            {
                "material_no": material_no,
                "description": f"{family_name} chamber module",
                "material_type": "MODULE",
                "family_code": FAMILIES[index % 4][0],
                "base_uom": "EA",
                "is_long_lead": False,
                "created_on": _day(rng),
            }
        )
    for index, material_no in enumerate(sub_ids):
        rows.append(
            {
                "material_no": material_no,
                "description": f"Valve stack subassembly {index + 1}",
                "material_type": "SUBASSY",
                "family_code": None,
                "base_uom": "EA",
                "is_long_lead": False,
                "created_on": _day(rng),
            }
        )
    for index, material_no in enumerate(comp_ids):
        rows.append(
            {
                "material_no": material_no,
                "description": _component_description(index),
                "material_type": "COMPONENT",
                "family_code": None,
                "base_uom": "EA",
                "is_long_lead": index < N_LONG_LEAD,
                "created_on": _day(rng),
            }
        )
    return rows


def _material_plants(rng, materials: list[dict]) -> list[dict]:
    rows = []
    for material in materials:
        for code, _name, _city, _country in PLANTS:
            rows.append(
                {
                    "material_no": material["material_no"],
                    "plant_code": code,
                    "mrp_type": "PD",
                    "planned_lead_time_days": _lead_days(rng, material["is_long_lead"]),
                    "safety_stock": int(rng.integers(0, 25)),
                }
            )
    return rows


def _suppliers(rng, supplier_ids: list[str]) -> list[dict]:
    rows = []
    for index, supplier_no in enumerate(supplier_ids):
        if supplier_no == CORVANE_SUPPLIER_NO:
            name = "Corvane RF Systems"
        else:
            name = _pair_name(index)
        rows.append(
            {
                "supplier_no": supplier_no,
                "name": _safe_name(name),
                "country": COUNTRIES[index % len(COUNTRIES)],
                "created_on": _day(rng),
            }
        )
    return rows


def _pick_suppliers(rng, pool: np.ndarray, count: int) -> list[str]:
    picked = rng.choice(pool, size=count, replace=False)
    return sorted(str(item) for item in picked)


def _sources(rng, comp_ids: list[str], supplier_ids: list[str]) -> list[dict]:
    pool = np.array(supplier_ids)
    rows = []
    for index, material_no in enumerate(comp_ids):
        long_lead = index < N_LONG_LEAD
        if index < N_RF:
            chosen = [CORVANE_SUPPLIER_NO]
        elif long_lead:
            chosen = _pick_suppliers(rng, pool, 2)
        else:
            chosen = _pick_suppliers(rng, pool, int(rng.integers(1, 4)))
        for position, supplier_no in enumerate(chosen):
            rows.append(
                {
                    "material_no": material_no,
                    "supplier_no": supplier_no,
                    "quoted_lead_time_days": _lead_days(rng, long_lead),
                    "unit_price": round(int(rng.integers(500, 250000)) / 100, 2),
                    "is_primary": position == 0,
                }
            )
    return rows


def _customers(rng) -> list[dict]:
    rows = []
    for index in range(N_CUSTOMERS):
        customer_no = f"C-{2001 + index:04d}"
        if customer_no == HALCYON_CUSTOMER_NO:
            name = "Halcyon Memory"
            segment = "Memory"
        else:
            name = _pair_name(200 + index)
            segment = _segment(customer_no)
        rows.append(
            {
                "customer_no": customer_no,
                "name": _safe_name(name),
                "segment": segment,
                "country": COUNTRIES[index % len(COUNTRIES)],
                "created_on": _day(rng),
            }
        )
    return rows


def _assign_subassemblies(module_ids: list[str], parent_subs: list[str]) -> dict[str, list[str]]:
    assigned = {module_id: [] for module_id in module_ids}
    for index, sub_id in enumerate(parent_subs):
        if index < 600:
            owner = module_ids[index % len(module_ids)]
        else:
            owner = module_ids[index - 600]
        assigned[owner].append(sub_id)
    for index in range(40):
        extra = module_ids[(index + 11) % len(module_ids)]
        assigned[extra].append(parent_subs[index])
    return assigned


def _assign_components(sub_ids: list[str], comp_ids: list[str]) -> dict[str, list[str]]:
    assigned = {sub_id: [] for sub_id in sub_ids}
    for index, comp_id in enumerate(comp_ids):
        assigned[sub_ids[index % len(sub_ids)]].append(comp_id)
    for index, comp_id in enumerate(comp_ids[:60]):
        for step in range(8):
            target = sub_ids[(index * 8 + step + 23) % len(sub_ids)]
            if comp_id not in assigned[target]:
                assigned[target].append(comp_id)
    return assigned


def _bom_edges(
    tool_ids,
    primary_modules,
    child_modules,
    parent_subs,
    child_subs,
    module_subs,
    sub_components,
) -> list[tuple[str, str]]:
    edges: list[tuple[str, str]] = []
    for index, tool_id in enumerate(tool_ids):
        for module_id in primary_modules[index * 5 : (index + 1) * 5]:
            edges.append((tool_id, module_id))
    for index, module_id in enumerate(child_modules):
        edges.append((primary_modules[index % len(primary_modules)], module_id))
    for module_id, sub_ids in module_subs.items():
        edges.extend((module_id, sub_id) for sub_id in sub_ids)
    for index, sub_id in enumerate(child_subs):
        edges.append((parent_subs[index], sub_id))
    for sub_id, comp_ids in sub_components.items():
        edges.extend((sub_id, comp_id) for comp_id in comp_ids)
    return edges


def _bom_rows(rng, edges: list[tuple[str, str]]) -> list[dict]:
    return [
        {
            "parent_material_no": parent,
            "component_material_no": component,
            "qty_per": float(int(rng.integers(1, 5))),
        }
        for parent, component in edges
    ]


def _bays(rng) -> list[dict]:
    rows = []
    for code, _name, _city, _country in PLANTS:
        for number in range(1, N_BAYS + 1):
            bay_id = f"{code}-B{number}"
            if bay_id == OVERLOADED_BAY:
                family_code = "ETC"
            else:
                family_code = FAMILIES[(number - 1) % len(FAMILIES)][0]
            rows.append(
                {
                    "bay_id": bay_id,
                    "plant_code": code,
                    "family_code": family_code,
                    "weekly_slots": int(rng.integers(2, 7)),
                }
            )
    return rows


def _frame(name: str, rows: list[dict]) -> pd.DataFrame:
    spec = table(name)
    columns = [column.name for column in spec.columns]
    frame = pd.DataFrame(rows, columns=columns)
    return frame.sort_values(list(spec.primary_key)).reset_index(drop=True)
