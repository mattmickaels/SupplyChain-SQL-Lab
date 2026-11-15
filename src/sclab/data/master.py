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
SPECIALTIES = (
    "RF Systems",
    "Vacuum Systems",
    "Wafer Robotics",
    "Thermal Systems",
    "Quartz Ceramics",
    "Gas Delivery",
    "Process Controls",
    "Vacuum Pumps",
    "Precision Assemblies",
    "Sealing Products",
    "Electronics",
    "Flow Controls",
)
PLATFORM_MODULES = (
    "Factory interface module",
    "Transfer chamber module",
    "Wafer robot module",
    "Gas delivery module",
    "Controls rack module",
    "Vacuum pumping module",
)
CHAMBER_FUNCTIONS = {
    "DEP": (
        "Gas distribution",
        "Chamber heater",
        "Wafer lift assembly",
        "RF electrode",
        "Throttle valve",
        "Liner and edge ring kit",
    ),
    "ETC": (
        "Showerhead",
        "ESC chuck",
        "Wafer lift assembly",
        "RF match network",
        "Throttle valve",
        "Liner and edge ring kit",
    ),
    "CMP": (
        "Polishing platen",
        "Carrier head",
        "Slurry delivery",
        "Wafer lift assembly",
        "Pressure control",
        "Pad conditioner",
    ),
    "INS": (
        "Optical head",
        "Wafer stage",
        "Illumination",
        "Image acquisition",
        "Vacuum chuck",
        "Wafer lift assembly",
    ),
}
PLATFORM_FUNCTIONS = {
    "Factory interface module": ("Load port", "Wafer aligner"),
    "Transfer chamber module": ("Vacuum manifold", "Isolation valve"),
    "Wafer robot module": ("Robot drive", "Robot arm"),
    "Gas delivery module": ("Valve manifold", "Mass flow controller"),
    "Controls rack module": ("PCB assembly", "Harness"),
    "Vacuum pumping module": ("Dry pump", "Turbo pump"),
}
# A purchased component belongs to a specific functional subassembly.
COMPONENT_RECIPES = {
    "Gas distribution": ("Gas distribution plate", "Gas inlet fitting"),
    "Chamber heater": ("Heater element", "Thermocouple"),
    "Wafer lift assembly": ("Lift pin", "Lift actuator"),
    "RF electrode": ("RF electrode plate", "Electrode insulator"),
    "Throttle valve": ("Throttle valve body", "Throttle valve seal"),
    "Liner and edge ring kit": ("Edge ring", "Liner retaining clip"),
    "Showerhead": ("Showerhead faceplate", "Showerhead seal"),
    "ESC chuck": ("ESC ceramic puck", "Chuck electrode"),
    "RF match network": ("RF match network", "Variable capacitor", "RF coaxial cable"),
    "Polishing platen": ("Platen bearing", "Platen drive belt"),
    "Carrier head": ("Carrier membrane", "Carrier retaining ring"),
    "Slurry delivery": ("Slurry dispense nozzle", "Slurry filter"),
    "Pressure control": ("Pressure transducer", "Pressure regulator"),
    "Pad conditioner": ("Conditioner disk", "Conditioner spindle"),
    "Optical head": ("Objective lens", "Optical mount"),
    "Wafer stage": ("Stage encoder", "Linear stage bearing"),
    "Illumination": ("Illumination source", "Beam splitter"),
    "Image acquisition": ("Camera sensor", "Image acquisition board"),
    "Vacuum chuck": ("Vacuum chuck plate", "Chuck O-ring kit"),
    "Load port": ("FOUP door latch", "Load port sensor"),
    "Wafer aligner": ("Aligner spindle", "Wafer edge sensor"),
    "Vacuum manifold": ("Vacuum manifold block", "Vacuum pressure gauge"),
    "Isolation valve": ("Isolation valve gate", "Isolation valve O-ring kit"),
    "Robot drive": ("Robot servo motor", "Robot drive encoder"),
    "Robot arm": ("Wafer end effector", "Robot arm bearing"),
    "Valve manifold": ("Valve manifold block", "Pneumatic valve"),
    "Mass flow controller": ("Mass flow sensor", "Proportional flow valve"),
    "PCB assembly": ("Control PCB assembly", "Power supply board"),
    "Harness": ("Cable harness", "Harness connector"),
    "Dry pump": ("Dry pump seal kit", "Pump exhaust fitting"),
    "Turbo pump": ("Turbo pump bearing kit", "Turbo pump controller"),
}
LONG_LEAD_FUNCTIONS = (
    "Dry pump",
    "Robot arm",
    "Chamber heater",
    "Liner and edge ring kit",
    "Valve manifold",
    "PCB assembly",
    "Turbo pump",
)
LONG_LEAD_OTHER = (
    "Dry vacuum pump",
    "Wafer handling robot",
    "Ceramic heater",
    "Quartz liner",
    "Gas box",
    "Process controller",
    "Turbo pump",
)
N_TOOLS = 40
N_MODULES = 280
N_SUBASSEMBLIES = 1000
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
    supplier_ids = [f"V-{10001 + i:05d}" for i in range(N_SUPPLIERS)]
    module_subs, sub_functions = _assign_subassemblies(module_ids, sub_ids)
    sub_components = _assign_components(sub_functions, comp_ids)
    edges = _bom_edges(tool_ids, module_ids, module_subs, sub_components)
    materials = _materials(rng, tool_ids, module_ids, sub_ids, comp_ids, sub_functions)
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
        "bom_item": _frame(
            "bom_item",
            _bom_rows(rng, edges) + _shared_bom_rows(sub_functions, comp_ids, edges),
        ),
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


def _component_function(index: int) -> str:
    if index < N_RF:
        return "RF match network"
    if index < N_LONG_LEAD:
        return LONG_LEAD_FUNCTIONS[(index - N_RF) % len(LONG_LEAD_FUNCTIONS)]
    if index == N_LONG_LEAD:
        return "RF match network"
    return tuple(COMPONENT_RECIPES)[(index - N_LONG_LEAD - 1) % len(COMPONENT_RECIPES)]


def _component_description(index: int) -> str:
    if index < N_RF:
        return "RF generator"
    if index < N_LONG_LEAD:
        kind = LONG_LEAD_OTHER[(index - N_RF) % len(LONG_LEAD_OTHER)]
        variant = (index - N_RF) // len(LONG_LEAD_OTHER) + 1
        return f"{kind}, variant {variant}"
    if index == N_LONG_LEAD:
        return "Spare RF generator kit"
    function = _component_function(index)
    recipes = COMPONENT_RECIPES[function]
    variant = (index - N_LONG_LEAD - 1) // len(COMPONENT_RECIPES)
    name = recipes[variant % len(recipes)]
    if name == "RF match network":
        return name
    return f"{name}, variant {variant + 1}"


def _module_function(index: int) -> tuple[str | None, str]:
    tool_index, slot = divmod(index, 7)
    if slot == 0:
        code, name = FAMILIES[tool_index // 10]
        return code, f"{name} chamber module"
    return None, PLATFORM_MODULES[slot - 1]


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


def _materials(rng, tool_ids, module_ids, sub_ids, comp_ids, sub_functions) -> list[dict]:
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
                "created_on": HISTORY_START - timedelta(days=1),
            }
        )
    for index, material_no in enumerate(module_ids):
        family_code, description = _module_function(index)
        rows.append(
            {
                "material_no": material_no,
                "description": description,
                "material_type": "MODULE",
                "family_code": family_code,
                "base_uom": "EA",
                "is_long_lead": False,
                "created_on": HISTORY_START - timedelta(days=1),
            }
        )
    for index, material_no in enumerate(sub_ids):
        rows.append(
            {
                "material_no": material_no,
                "description": f"{sub_functions[material_no]} subassembly, variant {index + 1}",
                "material_type": "SUBASSY",
                "family_code": None,
                "base_uom": "EA",
                "is_long_lead": False,
                "created_on": HISTORY_START - timedelta(days=1),
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
                "created_on": HISTORY_START - timedelta(days=1),
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
            name = (
                f"{_ADJECTIVES[index // len(SPECIALTIES)]} {SPECIALTIES[index % len(SPECIALTIES)]}"
            )
        rows.append(
            {
                "supplier_no": supplier_no,
                "name": _safe_name(name),
                "country": COUNTRIES[index % 9]
                if index < 9
                else str(
                    rng.choice(
                        COUNTRIES, p=[0.30, 0.18, 0.16, 0.08, 0.15, 0.06, 0.025, 0.025, 0.02]
                    )
                ),
                "created_on": _day(rng),
            }
        )
    return rows


def _pick_suppliers(rng, pool: np.ndarray, count: int) -> list[str]:
    picked = rng.choice(pool, size=count, replace=False)
    return [str(item) for item in picked]


def _sources(rng, comp_ids: list[str], supplier_ids: list[str]) -> list[dict]:
    pool = np.array([s for s in supplier_ids if s != CORVANE_SUPPLIER_NO])
    rows = []
    for index, material_no in enumerate(comp_ids):
        long_lead = index < N_LONG_LEAD
        if index < N_RF:
            chosen = [CORVANE_SUPPLIER_NO]
        elif long_lead:
            specialty = 1 + (index - N_RF) % len(LONG_LEAD_OTHER)
            specialists = np.array([s for s in pool if (int(s[2:]) - 10001) % 12 == specialty])
            chosen = _pick_suppliers(rng, specialists, 2)
        else:
            function = _component_function(index)
            specialty = (
                0
                if function in ("RF match network", "RF electrode")
                else 10
                if function in ("PCB assembly", "Harness", "Image acquisition", "Illumination")
                else 11
                if function
                in (
                    "Valve manifold",
                    "Mass flow controller",
                    "Pressure control",
                    "Throttle valve",
                    "Slurry delivery",
                )
                else 1
                if function in ("Vacuum manifold", "Isolation valve", "Vacuum chuck")
                else 2
                if function
                in ("Robot arm", "Robot drive", "Load port", "Wafer aligner", "Wafer stage")
                else 3
                if function == "Chamber heater"
                else 4
                if function in ("Liner and edge ring kit", "ESC chuck", "Showerhead")
                else 5
                if function == "Gas distribution"
                else 7
                if function in ("Dry pump", "Turbo pump")
                else 8
            )
            specialists = np.array([s for s in pool if (int(s[2:]) - 10001) % 12 == specialty])
            chosen = _pick_suppliers(rng, specialists, int(rng.integers(1, 4)))
            if _component_description(index) == "RF match network":
                # Preserve a second qualified RF specialist for every match unit.
                chosen = (
                    [CORVANE_SUPPLIER_NO, chosen[0]]
                    if ((index - N_LONG_LEAD - 1) // len(COMPONENT_RECIPES)) % 2 == 0
                    else [chosen[0], CORVANE_SUPPLIER_NO]
                )
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
            segment = _segment(customer_no)
            suffix = {"Foundry": "Foundry", "Memory": "Memory", "Logic": "Semiconductors"}[segment]
            name = _safe_name(f"{_ADJECTIVES[(index + 5) % len(_ADJECTIVES)]} {suffix}")
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


def _assign_subassemblies(module_ids, sub_ids):
    assigned = {module: [] for module in module_ids}
    functions = {}
    owners = []
    for index, module in enumerate(module_ids):
        family, description = _module_function(index)
        recipe = CHAMBER_FUNCTIONS[family] if family else PLATFORM_FUNCTIONS[description]
        owners.extend((module, function) for function in recipe)
    # Every module gets its complete functional recipe before adding variants.
    for index, sub in enumerate(sub_ids):
        module, function = owners[index % len(owners)]
        assigned[module].append(sub)
        functions[sub] = function
    return assigned, functions


def _assign_components(sub_functions, comp_ids):
    assigned = {sub: [] for sub in sub_functions}
    by_function = {}
    for sub, function in sub_functions.items():
        by_function.setdefault(function, []).append(sub)
    counts = {}
    for index, component in enumerate(comp_ids):
        function = _component_function(index)
        choices = by_function[function]
        position = counts.get(function, 0)
        assigned[choices[position % len(choices)]].append(component)
        counts[function] = position + 1
        # Reuse qualified standard parts only within compatible assemblies.
        if N_RF <= index < N_LONG_LEAD and len(choices) > 1:
            assigned[choices[(position + 1) % len(choices)]].append(component)
    return assigned


def _shared_bom_rows(sub_functions, comp_ids, edges):
    # Four qualified standard variants per platform function fit every tool.
    # Keep each part under its compatible functional subassembly.
    # Standard kits use one each and do not consume variant-quantity randomness.
    existing = set(edges)
    rows = []
    for function in (
        "Load port",
        "Isolation valve",
        "Vacuum manifold",
        "Valve manifold",
        "Mass flow controller",
        "Harness",
    ):
        common = [
            component
            for index, component in enumerate(comp_ids)
            if index > N_LONG_LEAD and _component_function(index) == function
        ][:4]
        for sub, sub_function in sub_functions.items():
            if sub_function != function:
                continue
            for part in common:
                if (sub, part) not in existing:
                    rows.append(
                        {
                            "parent_material_no": sub,
                            "component_material_no": part,
                            "qty_per": 1.0,
                        }
                    )
    return rows


def _bom_edges(tool_ids, module_ids, module_subs, sub_components):
    edges = []
    for index, tool in enumerate(tool_ids):
        edges.extend((tool, module) for module in module_ids[index * 7 : (index + 1) * 7])
    for module, subs in module_subs.items():
        edges.extend((module, sub) for sub in subs)
    for sub, components in sub_components.items():
        edges.extend((sub, component) for component in components)
    return edges


def _bom_rows(rng, edges: list[tuple[str, str]]) -> list[dict]:
    return [
        {
            "parent_material_no": parent,
            "component_material_no": component,
            "qty_per": (
                float(2 + ((int(parent[2:]) - 1) % 5))
                if int(parent[2:]) <= N_TOOLS and (int(component[2:]) - N_TOOLS - 1) % 7 == 0
                else 1.0
                if int(parent[2:]) <= N_TOOLS
                else float(int(rng.integers(1, 5)))
            ),
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
