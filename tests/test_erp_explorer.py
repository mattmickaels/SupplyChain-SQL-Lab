"""ERP browsing searches real records and preserves learning context."""

from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from sclab.engine.query import run_query
from sclab.screens.explorer import AREA_BY_SLUG, AREAS, PAGE_SIZE, browse
from sclab.web.app import create_app


@pytest.fixture
def client(lab_home):
    return TestClient(create_app(), base_url="http://127.0.0.1")


@pytest.mark.parametrize("area", AREAS, ids=lambda area: area.slug)
def test_each_area_lists_real_records_and_reproducible_sql(client, area):
    data = browse(area, "", "", 1)
    assert data["records"]
    assert len(data["records"]) <= PAGE_SIZE
    actual = run_query(data["display_sql"])
    assert actual.rows == [
        (row["key"], row["description"], row["plant"], row["detail"]) for row in data["records"]
    ]
    page = client.get("/erp", params={"area": area.slug})
    assert page.status_code == 200
    assert "Show the SQL: this record search" in page.text
    assert "No matching records" not in page.text
    first = next(row for row in data["records"] if row["href"])
    opened = client.get(first["href"])
    assert opened.status_code == 200
    assert "was not found" not in opened.text
    assert "Enter a plant." not in opened.text


def test_material_search_filters_plant_and_keeps_it_in_screen_links(client):
    area = AREA_BY_SLUG["materials"]
    data = browse(area, "RF generator", "SC01", 1)
    assert data["records"]
    for row in data["records"]:
        assert "rf generator" in row["description"].lower()
        assert row["plant"] == "SC01"
        params = parse_qs(urlsplit(row["href"]).query)
        assert params == {"key": [row["key"]], "plant": ["SC01"]}
    page = client.get("/erp", params={"area": "materials", "q": "RF generator", "plant": "sc01"})
    assert 'value="SC01" selected' in page.text
    assert "Stock/requirements" in page.text


def test_pagination_is_stable_and_search_values_are_literal(client):
    area = AREA_BY_SLUG["purchases"]
    first = browse(area, "Corvane", "SC01", 1)
    second = browse(area, "Corvane", "SC01", 2)
    assert first["total"] == second["total"] > PAGE_SIZE
    assert {row["key"] for row in first["records"]}.isdisjoint(
        row["key"] for row in second["records"]
    )
    last = browse(area, "Corvane", "SC01", 100000)
    assert last["page"] == last["pages"]
    assert last["last"] == last["total"]
    for query in ("%' OR 1=1 --", "%", "<script>alert(1)</script>"):
        page = client.get("/erp", params={"q": query})
        assert page.status_code == 200
        assert "No matching records" in page.text
        assert "<script>alert(1)</script>" not in page.text


def test_orphan_material_does_not_offer_broken_plant_screen(client):
    data = browse(AREA_BY_SLUG["materials"], "M-005416", "", 1)
    assert data["records"]
    assert all(row["href"] is None for row in data["records"])
    page = client.get("/erp", params={"q": "M-005416"})
    assert "No plant data" in page.text
    assert "/screens/MM03?key=M-005416" not in page.text
    assert "/screens/CS15?key=M-005416" in page.text


def test_navigation_opens_explorer_without_replacing_lesson(client):
    lesson = client.get("/lessons/01-tables-and-keys").text
    assert 'href="/erp" target="_blank" rel="noopener"' in lesson
    assert 'href="/erp">ERP Explorer</a>' in lesson
    page = client.get("/erp").text
    assert 'href="/erp" aria-current="page">ERP Explorer</a>' in page
    intro = client.get("/start").text
    assert 'id="company"' in intro
    assert intro.index('id="company"') < intro.index('id="big-picture"')
    assert "Corvane RF Systems" in intro
    assert "five years of history" in intro


def test_invalid_area_and_pagination_are_rejected(client):
    assert client.get("/erp", params={"area": "missing"}).status_code == 404
    assert client.get("/erp", params={"page": "-1"}).status_code == 422
    assert client.get("/erp", params={"q": "x" * 201}).status_code == 422
