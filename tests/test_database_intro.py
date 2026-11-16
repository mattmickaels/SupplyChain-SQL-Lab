"""The orientation is reachable and its visual examples match the lesson data."""

import re

from fastapi.testclient import TestClient

from sclab.engine.query import run_query
from sclab.web.app import create_app


def test_intro_is_first_and_connects_to_lessons_and_purchase_order(lab_home):
    with TestClient(create_app(), base_url="http://127.0.0.1") as client:
        home = client.get("/").text
        assert home.index('href="/start"') < home.index('href="/lessons/01-tables-and-keys"')
        page = client.get("/start")
        assert page.status_code == 200
        assert 'id="highlight-keys"' in page.text
        assert "po_no + line_no together" in page.text
        assert "No. Different orders can both have item 1." in page.text
        assert "http://" not in page.text and "https://" not in page.text
        for fragment in re.findall(r'href="#([^"]+)"', page.text):
            assert f'id="{fragment}"' in page.text
        for path in (
            "/lessons/01-tables-and-keys",
            "/screens/ME23N?key=PO-0006693",
            "/schema/plant",
        ):
            assert f'href="{path}"' in page.text
            assert client.get(path).status_code == 200
        assert 'href="/start"' in client.get("/lessons/01-tables-and-keys").text


def test_intro_relationship_examples_match_database(lab_home):
    assert run_query(
        "SELECT supplier_no, plant_code FROM purchase_order WHERE po_no = 'PO-0006693'"
    ).rows == [("V-10017", "SC01")]
    assert run_query("SELECT name FROM supplier WHERE supplier_no = 'V-10017'").rows == [
        ("Corvane RF Systems",)
    ]
    items = run_query(
        "SELECT line_no, material_no, order_qty FROM purchase_order_item "
        "WHERE po_no = 'PO-0006693' ORDER BY line_no"
    ).rows
    assert len(items) == 3
    assert items[:2] == [(1, "M-001332", 49), (2, "M-001570", 10)]
