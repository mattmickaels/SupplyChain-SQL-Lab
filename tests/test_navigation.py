"""Top navigation, lesson cross-links, and BOM material links."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sclab.engine.query import run_query
from sclab.lessons.model import load_lessons
from sclab.screens.framework import all_screens
from sclab.web.app import create_app

_ROOT = Path(__file__).resolve().parents[1]
_CONTENT = _ROOT / "src" / "sclab" / "lessons" / "content"
_NAV = [
    ("/", "Lessons"),
    ("/screens", "Screens"),
    ("/notebooks", "Notebook"),
    ("/schema", "Schema"),
    ("/playground", "Playground"),
]
_BLURBS = {
    "CM01": "Weekly load of each build bay.",
    "CS03": "Bill of materials, one level at a time.",
    "CS15": "Every parent that uses a component.",
    "MD04": "Stock, receipts, requirements, and the projected balance.",
    "ME23N": "Purchase order header, lines, and goods receipts.",
    "MM03": "Material master, plant data, stock, and open orders.",
    "VA03": "Sales order header and lines.",
    "XK03": "Supplier master, lead times, and on-time history.",
}
_LINKED_SCREEN = re.compile(r"/screens/([A-Z][A-Z0-9]+)")
_SAP_LINKS = {
    "04-joins": ["ekbe", "ekko", "ekpo", "lfa1"],
    "09-planning-analytics": ["afko", "ekbe", "ekko", "ekpo", "mara", "mard", "stpo", "vbap"],
}


@pytest.fixture
def client(lab_home):
    test_client = TestClient(create_app(), base_url="http://127.0.0.1")
    test_client.headers.update({"HX-Request": "true"})
    return test_client


def test_nav_links_return_200(client) -> None:
    home = client.get("/")
    assert home.status_code == 200
    nav = re.search(r"<nav[^>]*>(.*?)</nav>", home.text, re.DOTALL)
    assert nav is not None
    hrefs = re.findall(r'href="([^"]+)"', nav.group(1))
    labels = re.findall(r">([^<]+)</a>", nav.group(1))
    assert list(zip(hrefs, labels, strict=True)) == _NAV
    for href, _label in _NAV:
        assert client.get(href).status_code == 200


def test_transaction_code_box_is_on_every_page(client) -> None:
    lesson = next(item.id for item in load_lessons() if item.number == 1)
    paths = (
        "/",
        "/screens",
        "/notebooks",
        "/schema",
        "/playground",
        f"/lessons/{lesson}",
        "/screens/MM03",
    )
    for path in paths:
        page = client.get(path)
        assert page.status_code == 200, path
        assert 'action="/screens/go"' in page.text, path
        assert 'name="tcode"' in page.text, path


def test_home_lists_each_screen(client) -> None:
    import sclab.screens  # noqa: F401  # registers every screen

    page = client.get("/")
    assert "Kestrel ERP" in page.text
    screens = all_screens()
    assert {screen.code for screen in screens} == set(_BLURBS)
    for screen in screens:
        assert screen.summary.strip(), screen.code
        assert screen.summary == _BLURBS[screen.code]
        assert f"/screens/{screen.code}" in page.text
        assert screen.summary in page.text


def test_lesson_screen_codes_exist() -> None:
    import sclab.screens  # noqa: F401  # registers every screen

    known = {screen.code for screen in all_screens()}
    mentioned: set[str] = set()
    for path in _CONTENT.glob("*/lesson.md"):
        text = path.read_text(encoding="utf-8")
        mentioned.update(_LINKED_SCREEN.findall(text))
        mentioned.update(code for code in known if re.search(rf"\b{code}\b", text))
    assert mentioned
    assert mentioned <= known
    joins = (_CONTENT / "04-joins" / "lesson.md").read_text(encoding="utf-8")
    bom = (_CONTENT / "08-bom-explosion" / "lesson.md").read_text(encoding="utf-8")
    planning = (_CONTENT / "09-planning-analytics" / "lesson.md").read_text(encoding="utf-8")
    assert "See this join as a screen." in joins
    assert "ME23N" in joins and "MM03" in joins
    assert "CS03" in bom and "CS15" in bom
    assert "MD04" in planning and "CM01" in planning


def test_lesson_pages_link_screens_and_sap_views(client) -> None:
    joins = client.get("/lessons/04-joins")
    assert joins.status_code == 200
    assert 'href="/screens/ME23N?key=PO-0043687"' in joins.text
    assert 'href="/screens/MM03?key=M-001501&amp;plant=AU01"' in joins.text
    bom = client.get("/lessons/08-bom-explosion")
    assert 'href="/screens/CS03?key=M-000011"' in bom.text
    assert 'href="/screens/CS15?key=M-001321"' in bom.text
    planning = client.get("/lessons/09-planning-analytics")
    assert "MD04?key=M-001324" in planning.text
    assert "plant_code=SC01" in planning.text
    assert 'href="/screens/CM01?key=SC01"' in planning.text
    for lesson_id, expected in _SAP_LINKS.items():
        page = client.get(f"/lessons/{lesson_id}")
        assert page.status_code == 200
        assert re.findall(r'href="/schema/([a-z0-9_]+)"', page.text) == expected


def test_schema_view_page_lists_the_base_table(client) -> None:
    index = client.get("/schema")
    assert 'href="/schema/ekko"' in index.text
    page = client.get("/schema/ekko")
    assert page.status_code == 200
    assert "purchase_order" in page.text
    assert "ebeln" in page.text
    material = client.get("/schema/material")
    assert 'href="/schema/mara"' in material.text


def test_bom_material_link_uses_first_plant_or_plain_text(client) -> None:
    linked = run_query(
        """
        SELECT b.parent_material_no, b.component_material_no, MIN(p.plant_code) AS plant_code
        FROM bom_item AS b
        JOIN material_plant AS p ON p.material_no = b.component_material_no
        GROUP BY b.parent_material_no, b.component_material_no
        ORDER BY b.parent_material_no, b.component_material_no
        LIMIT 1
        """
    ).rows[0]
    parent, component, plant = linked
    page = client.get("/screens/CS03", params={"key": parent})
    assert page.status_code == 200
    assert f"/screens/MM03?key={component}&amp;plant={plant}" in page.text
    assert f"/screens/CS15?key={parent}" in page.text
    opened = client.get("/screens/MM03", params={"key": component, "plant": plant})
    assert "Enter a plant." not in opened.text
    assert "was not found" not in opened.text

    bare = run_query(
        """
        SELECT m.material_no
        FROM material AS m
        LEFT JOIN material_plant AS p ON p.material_no = m.material_no
        WHERE p.material_no IS NULL
        ORDER BY m.material_no
        LIMIT 1
        """
    ).rows[0][0]
    plain = client.get("/screens/CS03", params={"key": bare})
    assert f"/screens/MM03?key={bare}" not in plain.text
    assert bare in plain.text

    where_used = client.get("/screens/CS15", params={"key": component})
    assert f"/screens/CS03?key={component}" in where_used.text
    assert f"/screens/MM03?key={component}&amp;plant={plant}" in where_used.text


def test_md04_links_documents_and_cm01_links_materials(client) -> None:
    page = client.get("/screens/MD04", params={"key": "M-001341", "plant_code": "AU01"})
    assert page.status_code == 200
    assert "/screens/ME23N?key=PO-" in page.text
    assert "/screens/CO03" not in page.text
    assert "Prod order" in page.text
    capacity = client.get("/screens/CM01", params={"key": "SC01"})
    assert "/screens/MM03?key=" in capacity.text
    assert "plant=SC01" in capacity.text


def test_lesson_5_drops_day_interval_date_add() -> None:
    text = (_CONTENT / "05-dates-and-time" / "lesson.md").read_text(encoding="utf-8")
    assert "date_add" not in text


def test_rf_generator_prompt_reads_as_one_sentence() -> None:
    text = (_CONTENT / "04-joins" / "exercises.toml").read_text(encoding="utf-8")
    assert "with the comma, so the spare kit" not in text
    assert "including the comma so the spare kit stays out" in text


def test_december_edge_comment_names_the_sunday() -> None:
    text = (_ROOT / "src" / "sclab" / "data" / "transactions.py").read_text(encoding="utf-8")
    assert "same weekly bucket" not in text
    assert "27 December 2026" in text
    assert "Sunday" in text
