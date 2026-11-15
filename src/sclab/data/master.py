"""Deterministic master data for plants, materials, sources, customers, and BOMs."""

from datetime import timedelta

import numpy as np
import pandas as pd

from sclab.constants import AS_OF_DATE, HISTORY_START, SEED
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
HOME_PLANT = {"DEP": "AU01", "ETC": "SC01", "CMP": "SG01", "INS": "AU01"}
# Tool list price in USD: a base plus an amount per chamber.
TOOL_PRICES = {
    "DEP": (1_400_000, 900_000),
    "ETC": (1_200_000, 850_000),
    "CMP": (1_300_000, 800_000),
    "INS": (2_200_000, 800_000),
}
RF_DELIVERY = "RF delivery"
RF_FAMILIES = ("DEP", "ETC")
TOOL_VARIANTS = {
    "DEP": ("PECVD", "PEALD"),
    "ETC": ("conductor", "dielectric"),
    "CMP": ("oxide", "copper"),
    "INS": ("optical", "e-beam"),
}
# RF generators: six source units and six bias units, as (frequency, power in kW, price in USD).
RF_GENERATORS = (
    ("13.56 MHz", "1.5 kW", 52000),
    ("13.56 MHz", "3 kW", 76000),
    ("13.56 MHz", "5 kW", 112000),
    ("27.12 MHz", "3 kW", 82000),
    ("60 MHz", "3 kW", 91000),
    ("60 MHz", "5 kW", 118000),
    ("2 MHz", "1 kW", 41000),
    ("2 MHz", "3 kW", 63000),
    ("2 MHz", "5 kW", 94000),
    ("400 kHz", "1 kW", 44000),
    ("400 kHz", "3 kW", 68000),
    ("13.56 MHz", "1 kW", 47000),
)
LONG_LEAD_PRICES = {
    "Dry vacuum pump": (18000, 32000),
    "Wafer handling robot": (55000, 95000),
    "Ceramic heater": (9000, 18000),
    "Quartz liner": (2500, 6000),
    "Gas box": (12000, 25000),
    "Process controller": (15000, 30000),
    "Turbo pump": (14000, 28000),
}
SPARE_KIT_PRICE = (1500, 2200)
# Supplier price ranges in USD by purchased part class.
COMPONENT_PRICES = {
    "Gas distribution plate": (900, 2500),
    "Gas inlet fitting": (15, 45),
    "Heater element": (400, 1200),
    "Thermocouple": (60, 180),
    "Lift pin": (25, 50),
    "Lift actuator": (350, 900),
    "RF electrode plate": (800, 2200),
    "Electrode insulator": (120, 400),
    "Throttle valve body": (1500, 3800),
    "Throttle valve seal": (12, 35),
    "Edge ring": (300, 900),
    "Liner retaining clip": (5, 18),
    "Showerhead faceplate": (2500, 6500),
    "Showerhead seal": (15, 45),
    "ESC ceramic puck": (3500, 8000),
    "Chuck electrode": (400, 1200),
    "RF match network": (6000, 14000),
    "Variable capacitor": (800, 2500),
    "RF coaxial cable": (90, 300),
    "Platen bearing": (2500, 6500),
    "Platen drive belt": (400, 1200),
    "Carrier membrane": (900, 2600),
    "Carrier retaining ring": (1500, 3800),
    "Slurry dispense nozzle": (400, 1200),
    "Slurry filter": (300, 900),
    "Pressure transducer": (1500, 4000),
    "Pressure regulator": (1200, 3200),
    "Conditioner disk": (1100, 3000),
    "Conditioner spindle": (6000, 15000),
    "Objective lens": (10000, 30000),
    "Optical mount": (1500, 4000),
    "Stage encoder": (2500, 8000),
    "Linear stage bearing": (1800, 4800),
    "Illumination source": (8000, 24000),
    "Beam splitter": (2000, 6000),
    "Camera sensor": (6000, 18000),
    "Image acquisition board": (3000, 9000),
    "Vacuum chuck plate": (2000, 6000),
    "Chuck O-ring kit": (12, 40),
    "FOUP door latch": (60, 180),
    "Load port sensor": (80, 260),
    "Aligner spindle": (700, 2000),
    "Wafer edge sensor": (120, 420),
    "Vacuum manifold block": (300, 900),
    "Vacuum pressure gauge": (250, 700),
    "Isolation valve gate": (900, 2600),
    "Isolation valve O-ring kit": (14, 45),
    "Robot servo motor": (1200, 3500),
    "Robot drive encoder": (250, 800),
    "Wafer end effector": (600, 2000),
    "Robot arm bearing": (150, 500),
    "Valve manifold block": (500, 1500),
    "Pneumatic valve": (60, 220),
    "Mass flow sensor": (250, 700),
    "Proportional flow valve": (150, 500),
    "Control PCB assembly": (400, 1500),
    "Power supply board": (250, 800),
    "Cable harness": (120, 450),
    "Harness connector": (6, 30),
    "Dry pump seal kit": (18, 50),
    "Pump exhaust fitting": (15, 45),
    "Turbo pump bearing kit": (350, 900),
    "Turbo pump controller": (900, 2400),
    "Spare RF generator kit": SPARE_KIT_PRICE,
}
N_TOOLS = 40
N_MODULES = 280
N_SUBASSEMBLIES = 1000
N_COMPONENTS = 4000
COMPONENT_START = N_TOOLS + N_MODULES + N_SUBASSEMBLIES + 1
N_LONG_LEAD = 240
N_RF = 12
N_SUPPLIERS = 150
N_CUSTOMERS = 25
# Build bays as (bay_id, plant, family, weekly start slots); each family has one home plant.
# The overloaded Etch bay keeps 6 slots, and its Etch twin keeps 4 for the rebalancing drill.
# Bays keep their earlier ids; bays for families a plant never builds were retired.
BAYS = (
    ("AU01-B1", "AU01", "DEP", 2),
    ("AU01-B4", "AU01", "INS", 2),
    ("SC01-B2", "SC01", "ETC", 4),
    (OVERLOADED_BAY, "SC01", "ETC", 6),
    ("SG01-B3", "SG01", "CMP", 2),
)
RETIRED_LAYOUT_BAYS = 5
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
    rf_parts = _rf_delivery_components(module_ids, module_subs, sub_functions, comp_ids)
    sub_components.update(rf_parts)
    edges = _bom_edges(tool_ids, module_ids, module_subs, sub_components)
    materials = _materials(rng, tool_ids, module_ids, sub_ids, comp_ids, sub_functions)
    shared = _shared_bom_rows(sub_functions, comp_ids, edges)
    structure = [
        {"parent_material_no": parent, "component_material_no": component}
        for parent, component in edges
    ] + shared
    used_plants = _used_plants(tool_ids, materials, structure)
    # The random draws below keep their original order so earlier values stay put.
    plant_rows = _material_plants(rng, materials, used_plants)
    supplier_rows = _suppliers(rng, supplier_ids)
    source_rows = _sources(rng, comp_ids, supplier_ids)
    customer_rows = _customers(rng)
    bom = _bom_rows(rng, edges, _rf_subs(sub_functions)) + shared
    return {
        "plant": _plants(),
        "product_family": _families(),
        "material": _frame("material", materials),
        "material_plant": _frame("material_plant", plant_rows),
        "supplier": _frame("supplier", supplier_rows),
        "material_supplier": _frame("material_supplier", source_rows),
        "customer": _frame("customer", customer_rows),
        "bom_item": _frame("bom_item", bom),
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
        return RF_DELIVERY
    if index < N_LONG_LEAD:
        return LONG_LEAD_FUNCTIONS[(index - N_RF) % len(LONG_LEAD_FUNCTIONS)]
    if index == N_LONG_LEAD:
        return RF_DELIVERY
    return tuple(COMPONENT_RECIPES)[(index - N_LONG_LEAD - 1) % len(COMPONENT_RECIPES)]


def _component_description(index: int) -> str:
    if index < N_RF:
        frequency, power, _price = RF_GENERATORS[index]
        return f"RF generator, {frequency}, {power}"
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
        variant = TOOL_VARIANTS[code][(index % 10) // 5]
        rows.append(
            {
                "material_no": material_no,
                "description": f"{family_name} system, 300mm, {chambers}-chamber, {variant}",
                "material_type": "TOOL",
                "family_code": code,
                "base_uom": "EA",
                "is_long_lead": False,
                "created_on": HISTORY_START - timedelta(days=1),
            }
        )
    for index, material_no in enumerate(module_ids):
        family_code, description = _module_function(index)
        tool_index = index // 7
        tool_family, tool_family_name = FAMILIES[tool_index // 10]
        chambers = 2 + (tool_index % 5)
        variant = TOOL_VARIANTS[tool_family][(tool_index % 10) // 5]
        # Each tool has its own modules, so the description names the tool they belong to.
        if family_code:
            description = f"{description}, {chambers}-chamber {variant}"
        else:
            description = f"{description}, {tool_family_name} {chambers}-chamber {variant}"
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


RF_PART_NUMBERS = frozenset(f"M-{COMPONENT_START + number:06d}" for number in range(N_RF))


def _material_plants(rng, materials: list[dict], used_plants: dict[str, set[str]]) -> list[dict]:
    rows = []
    for material in materials:
        used = used_plants.get(material["material_no"], set())
        for code, _name, _city, _country in PLANTS:
            lead = _lead_days(rng, material["is_long_lead"])
            safety = int(rng.integers(0, 25))
            if material["material_no"] in RF_PART_NUMBERS:
                # Costly RF generators are held at one or two units.
                safety = 1 + safety % 2
            rows.append(
                {
                    "material_no": material["material_no"],
                    "plant_code": code,
                    "mrp_type": "PD",
                    "planned_lead_time_days": lead,
                    # Safety stock only makes sense where a tool using the part is built.
                    "safety_stock": safety if code in used else 0,
                }
            )
    return rows


def _rf_subs(sub_functions: dict[str, str]) -> set[str]:
    return {sub for sub, function in sub_functions.items() if function == RF_DELIVERY}


def _used_plants(tool_ids, materials: list[dict], bom: list[dict]) -> dict[str, set[str]]:
    """Plants that build a tool using each material, at any BOM depth."""
    family = {row["material_no"]: row["family_code"] for row in materials}
    children: dict[str, list[str]] = {}
    for row in bom:
        children.setdefault(row["parent_material_no"], []).append(row["component_material_no"])
    used: dict[str, set[str]] = {}
    for tool in tool_ids:
        plant = HOME_PLANT[family[tool]]
        stack = [tool]
        seen: set[str] = set()
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            used.setdefault(node, set()).add(plant)
            stack.extend(children.get(node, ()))
    return used


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
    prices = np.random.default_rng(SEED + 9)
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
                if function in ("RF match network", "RF electrode", RF_DELIVERY)
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
            if _component_description(index).startswith("RF match network, "):
                # Preserve a second qualified RF specialist for every match unit.
                chosen = (
                    [CORVANE_SUPPLIER_NO, chosen[0]]
                    if ((index - N_LONG_LEAD - 1) // len(COMPONENT_RECIPES)) % 2 == 0
                    else [chosen[0], CORVANE_SUPPLIER_NO]
                )
        low, high = _price_range(index)
        base = low + (high - low) * float(prices.random())
        for position, supplier_no in enumerate(chosen):
            quoted_lead = _lead_days(rng, long_lead)
            rng.integers(500, 250000)  # keeps the shared stream in step with earlier versions
            rows.append(
                {
                    "material_no": material_no,
                    "supplier_no": supplier_no,
                    "quoted_lead_time_days": quoted_lead,
                    "unit_price": _quote(base * float(prices.uniform(0.93, 1.12)), index),
                    "is_primary": position == 0,
                }
            )
    return rows


def _quote(price: float, index: int) -> float:
    if index < N_RF:
        # RF generators stay inside the $40k-$120k band whatever the quote spread.
        price = min(max(price, 40500.0), 119000.0)
    return round(price, 2)


def _price_range(index: int) -> tuple[float, float]:
    """Supplier price range in USD for the component at this index."""
    if index < N_RF:
        price = RF_GENERATORS[index][2]
        return price * 0.97, price * 1.03
    if index < N_LONG_LEAD:
        return LONG_LEAD_PRICES[LONG_LEAD_OTHER[(index - N_RF) % len(LONG_LEAD_OTHER)]]
    if index == N_LONG_LEAD:
        return SPARE_KIT_PRICE
    function = _component_function(index)
    recipes = COMPONENT_RECIPES[function]
    variant = (index - N_LONG_LEAD - 1) // len(COMPONENT_RECIPES)
    return COMPONENT_PRICES[recipes[variant % len(recipes)]]


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
    # RF delivery subassemblies are appended so the other subassembly numbers stay in place.
    for index, module in enumerate(module_ids):
        family, _description = _module_function(index)
        if family in RF_FAMILIES:
            owners.append((module, RF_DELIVERY))
    # Every module gets its complete functional recipe before adding variants.
    # Variants are spread across every tool, never onto an RF delivery assembly.
    plain = [owner for owner in owners if owner[1] != RF_DELIVERY]
    for index, sub in enumerate(sub_ids):
        if index < len(owners):
            module, function = owners[index]
        else:
            module, function = plain[((index - len(owners)) * 17) % len(plain)]
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
        if function == RF_DELIVERY:
            continue
        choices = by_function[function]
        position = counts.get(function, 0)
        assigned[choices[position % len(choices)]].append(component)
        counts[function] = position + 1
        # Reuse qualified standard parts only within compatible assemblies.
        if N_RF <= index < N_LONG_LEAD and len(choices) > 1:
            assigned[choices[(position + 1) % len(choices)]].append(component)
    return assigned


def _rf_delivery_components(module_ids, module_subs, sub_functions, comp_ids):
    """Give each plasma tool one RF delivery subassembly with shared RF generators.

    Etch tools carry a source and a bias generator per chamber.
    Deposition tools carry one source generator per chamber, plus a bias generator on PEALD.
    Six source and six bias part numbers are shared across the etch and deposition variants.
    """
    sources, biases = comp_ids[:6], comp_ids[6:N_RF]
    spare_kit = comp_ids[N_LONG_LEAD]
    assigned: dict[str, list[str]] = {}
    for tool_index in range(N_TOOLS):
        code = FAMILIES[tool_index // 10][0]
        if code not in RF_FAMILIES:
            continue
        position = tool_index % 10
        module = module_ids[tool_index * 7]
        sub = next(item for item in module_subs[module] if sub_functions[item] == RF_DELIVERY)
        if code == "ETC":
            parts = [sources[position % 6], biases[(position + 2) % 6]]
        else:
            parts = [sources[(position + 4) % 6]]
            if position >= 5:
                parts.append(biases[position % 6])
        assigned[sub] = [*parts, spare_kit]
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


def _bom_rows(rng, edges: list[tuple[str, str]], rf_subs: set[str]) -> list[dict]:
    rows = []
    for parent, component in edges:
        if int(parent[2:]) <= N_TOOLS:
            if (int(component[2:]) - N_TOOLS - 1) % 7 == 0:
                qty = float(2 + ((int(parent[2:]) - 1) % 5))
            else:
                qty = 1.0
        else:
            # Draw for every lower-level edge so the random stream does not shift.
            draw = int(rng.integers(1, 5))
            qty = 1.0
            number = int(component[2:])
            if number >= COMPONENT_START and parent not in rf_subs:
                # Cheap hardware comes in handfuls; costly parts come one at a time.
                low, high = _price_range(number - COMPONENT_START)
                if (low + high) / 2 < 60:
                    qty = float(2 * draw)
                elif (low + high) / 2 < 400:
                    qty = float(1 + draw % 2)
        rows.append(
            {"parent_material_no": parent, "component_material_no": component, "qty_per": qty}
        )
    return rows


def _bays(rng) -> list[dict]:
    """Bays only for the families each plant builds, sized to that family's starts."""
    # The original layout drew one slot count for each of five bays per plant.
    # Keep drawing them so every later random value stays where it was.
    for _bay in range(len(PLANTS) * RETIRED_LAYOUT_BAYS):
        rng.integers(2, 7)
    return [
        {"bay_id": bay_id, "plant_code": plant, "family_code": family, "weekly_slots": slots}
        for bay_id, plant, family, slots in BAYS
    ]


def _frame(name: str, rows: list[dict]) -> pd.DataFrame:
    spec = table(name)
    columns = [column.name for column in spec.columns]
    frame = pd.DataFrame(rows, columns=columns)
    return frame.sort_values(list(spec.primary_key)).reset_index(drop=True)
