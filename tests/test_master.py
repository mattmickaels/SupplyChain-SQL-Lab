import numpy as np
import pandas as pd
import pytest

from sclab.constants import SEED
from sclab.data.master import generate_master
from sclab.data.schema import table
from sclab.data.story import CORVANE_SUPPLIER_NO, HALCYON_CUSTOMER_NO, OVERLOADED_BAY

MASTER_TABLES = (
    "plant",
    "product_family",
    "material",
    "material_plant",
    "supplier",
    "material_supplier",
    "customer",
    "bom_item",
    "build_bay",
)
LONG_LEAD = {
    "RF generator",
    "dry vacuum pump",
    "wafer handling robot",
    "ceramic heater",
    "quartz liner",
    "gas box",
    "process controller",
    "turbo pump",
}
COUNTRIES = {"US", "JP", "DE", "KR", "TW", "SG", "NL", "CN", "IL"}
DENYLIST = (
    "Applied",
    "TSMC",
    "Samsung",
    "Intel",
    "Micron",
    "SK hynix",
    "Lam",
    "KLA",
    "Tokyo Electron",
    "ASML",
    "GlobalFoundries",
    "Texas Instruments",
    "MKS",
    "Advanced Energy",
    "Edwards",
    "Brooks",
)


@pytest.fixture(scope="module")
def frames() -> dict[str, pd.DataFrame]:
    return generate_master(np.random.default_rng(SEED))


def test_volumes_within_targets(frames: dict[str, pd.DataFrame]) -> None:
    plants = frames["plant"]
    assert set(plants["plant_code"]) == {"SC01", "AU01", "SG01"}
    by_plant = plants.set_index("plant_code")
    assert by_plant.loc["SC01", "city"] == "Santa Clara"
    assert by_plant.loc["SC01", "country"] == "US"
    assert by_plant.loc["AU01", "city"] == "Austin"
    assert by_plant.loc["AU01", "country"] == "US"
    assert by_plant.loc["SG01", "city"] == "Singapore"
    assert by_plant.loc["SG01", "country"] == "SG"

    families = frames["product_family"].set_index("family_code")["name"]
    assert families.to_dict() == {
        "DEP": "Deposition",
        "ETC": "Etch",
        "CMP": "CMP",
        "INS": "Inspection",
    }

    materials = frames["material"]
    tools = materials[materials["material_type"] == "TOOL"]
    modules = materials[materials["material_type"] == "MODULE"]
    subassemblies = materials[materials["material_type"] == "SUBASSY"]
    components = materials[materials["material_type"] == "COMPONENT"]
    assert len(tools) == 40
    assert tools.groupby("family_code").size().to_dict() == {
        "DEP": 10,
        "ETC": 10,
        "CMP": 10,
        "INS": 10,
    }
    tool_pattern = r"^(Deposition|Etch|CMP|Inspection) system, 300mm, [1-9]\d*-chamber$"
    assert tools["description"].str.match(tool_pattern).all()
    assert 250 <= len(modules) <= 350
    assert 800 <= len(subassemblies) <= 1200
    assert 3000 <= len(components) <= 4500
    assert materials["material_no"].str.match(r"^M-\d{6}$").all()

    long_lead = components[components["is_long_lead"]]
    assert 0.05 <= len(long_lead) / len(components) <= 0.08
    assert set(long_lead["description"]) <= LONG_LEAD
    assert not materials.loc[materials["material_type"] != "COMPONENT", "is_long_lead"].any()

    planning = frames["material_plant"].merge(
        materials[["material_no", "is_long_lead"]],
        on="material_no",
    )
    long_days = planning.loc[planning["is_long_lead"], "planned_lead_time_days"]
    other_days = planning.loc[~planning["is_long_lead"], "planned_lead_time_days"]
    assert long_days.between(120, 240).all()
    assert other_days.between(14, 90).all()

    suppliers = frames["supplier"]
    assert len(suppliers) == 150
    assert suppliers["supplier_no"].str.match(r"^V-\d{5}$").all()
    assert set(suppliers["country"]) <= COUNTRIES
    corvane = suppliers.loc[suppliers["supplier_no"] == CORVANE_SUPPLIER_NO].iloc[0]
    assert corvane["name"] == "Corvane RF Systems"

    sources = frames["material_supplier"]
    quotes = sources.merge(materials[["material_no", "is_long_lead"]], on="material_no")
    long_quotes = quotes.loc[quotes["is_long_lead"], "quoted_lead_time_days"]
    other_quotes = quotes.loc[~quotes["is_long_lead"], "quoted_lead_time_days"]
    assert long_quotes.between(120, 240).all()
    assert other_quotes.between(14, 90).all()
    source_counts = sources.groupby("material_no").size()
    assert source_counts.reindex(components["material_no"]).between(1, 3).all()
    made = materials.loc[materials["material_type"] != "COMPONENT", "material_no"]
    assert not sources["material_no"].isin(made).any()
    long_counts = source_counts.reindex(long_lead["material_no"])
    single = int((long_counts == 1).sum())
    assert 0.03 <= single / len(long_lead) <= 0.06

    customers = frames["customer"]
    assert len(customers) == 25
    assert customers["customer_no"].str.match(r"^C-\d{4}$").all()
    assert customers["segment"].value_counts().to_dict() == {
        "Foundry": 10,
        "Memory": 7,
        "Logic": 8,
    }
    halcyon = customers.loc[customers["customer_no"] == HALCYON_CUSTOMER_NO].iloc[0]
    assert halcyon["name"] == "Halcyon Memory"
    assert halcyon["segment"] == "Memory"

    bays = frames["build_bay"]
    for plant_code, group in bays.groupby("plant_code"):
        assert 4 <= len(group) <= 6
        assert group["bay_id"].str.match(rf"^{plant_code}-B\d+$").all()
        assert group["weekly_slots"].between(2, 6).all()
    overloaded = bays.loc[bays["bay_id"] == OVERLOADED_BAY].iloc[0]
    assert overloaded["plant_code"] == "SC01"
    assert overloaded["family_code"] == "ETC"


def test_frames_match_schema_columns(frames: dict[str, pd.DataFrame]) -> None:
    assert set(frames) == set(MASTER_TABLES)
    for name in MASTER_TABLES:
        expected = [column.name for column in table(name).columns]
        assert list(frames[name].columns) == expected


def test_material_numbers_unique(frames: dict[str, pd.DataFrame]) -> None:
    numbers = frames["material"]["material_no"]
    assert numbers.is_unique
    assert numbers.str.match(r"^M-\d{6}$").all()


def test_bom_has_no_cycles_and_depth_4_to_6(frames: dict[str, pd.DataFrame]) -> None:
    materials = frames["material"]
    bom = frames["bom_item"]
    assert bom["parent_material_no"].ne(bom["component_material_no"]).all()
    children: dict[str, list[str]] = {}
    for parent, component in zip(
        bom["parent_material_no"],
        bom["component_material_no"],
        strict=True,
    ):
        children.setdefault(parent, []).append(component)

    non_tools = set(materials.loc[materials["material_type"] != "TOOL", "material_no"])
    assert non_tools <= set(bom["component_material_no"])
    assert bom["component_material_no"].value_counts().max() > 1

    types = materials.set_index("material_no")["material_type"]
    gray: set[str] = set()
    done: dict[str, tuple[int, int]] = {}

    def walk(node: str) -> tuple[int, int]:
        if node in gray:
            raise AssertionError(f"cycle at {node}")
        if node in done:
            return done[node]
        gray.add(node)
        kids = children.get(node, [])
        if not kids:
            span = (1, 1)
        else:
            nested = [walk(kid) for kid in kids]
            span = (1 + min(item[0] for item in nested), 1 + max(item[1] for item in nested))
        gray.remove(node)
        done[node] = span
        return span

    # Every node, including parts no tool reaches.
    for node in sorted(set(children) | set(bom["component_material_no"])):
        walk(node)

    tools = materials.loc[materials["material_type"] == "TOOL", "material_no"]
    for tool_no in tools:
        low, high = walk(tool_no)
        assert 4 <= low <= high <= 6
        module_children = [kid for kid in children.get(tool_no, []) if types[kid] == "MODULE"]
        assert 3 <= len(module_children) <= 6


def test_every_rf_generator_single_sourced_from_corvane(
    frames: dict[str, pd.DataFrame],
) -> None:
    materials = frames["material"]
    rf = materials[materials["description"] == "RF generator"]
    assert not rf.empty
    assert set(rf["material_type"]) == {"COMPONENT"}
    assert bool(rf["is_long_lead"].all())
    sources = frames["material_supplier"]
    rf_sources = sources[sources["material_no"].isin(rf["material_no"])]
    counts = rf_sources.groupby("material_no").size()
    assert counts.reindex(rf["material_no"]).eq(1).all()
    assert set(rf_sources["supplier_no"]) == {CORVANE_SUPPLIER_NO}


def test_deterministic() -> None:
    first = generate_master(np.random.default_rng(SEED))
    second = generate_master(np.random.default_rng(SEED))
    assert set(first) == set(second)
    for name in first:
        pd.testing.assert_frame_equal(first[name], second[name])


def test_no_real_company_names(frames: dict[str, pd.DataFrame]) -> None:
    names = pd.concat(
        [frames["supplier"]["name"], frames["customer"]["name"]],
        ignore_index=True,
    )
    for banned in DENYLIST:
        hits = names[names.str.contains(banned, case=False, regex=False)]
        assert hits.empty, banned
