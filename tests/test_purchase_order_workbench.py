"""Classic purchase-order navigation and its connection to the real SQL."""

import html
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from sclab.screens.framework import render_screen
from sclab.web.app import create_app


@pytest.fixture
def client(lab_home):
    return TestClient(create_app(), base_url="http://127.0.0.1")


def test_entry_examples_open_real_orders(client):
    page = client.get("/screens/ME23N")
    assert "Open a purchase order" in page.text
    assert "Purchase order loaded" not in page.text
    for number in ("PO-0006693", "PO-0000003"):
        assert f"/screens/ME23N?key={number}" in page.text
        order = client.get("/screens/ME23N", params={"key": number})
        assert order.status_code == 200
        assert "Purchase order loaded" in order.text


def test_item_selection_and_history_keep_document_context(client):
    page = client.get("/screens/ME23N", params={"key": "PO-0006693", "line": "2"})
    assert "Item 2 details" in page.text
    selected = re.search(r'<tr class="po-selected">(.*?)</tr>', page.text, re.S)
    assert selected is not None
    assert 'aria-label="Select item 2" aria-current="true"' in selected.group(1)
    assert "key=PO-0006693&amp;view=history&amp;line=2" in page.text
    history = client.get(
        "/screens/ME23N", params={"key": "PO-0006693", "line": "2", "view": "history"}
    )
    assert "Item 2 details" in history.text
    assert 'aria-current="page">Purchase order history' in history.text
    assert "All items on this order" in history.text
    assert "key=PO-0006693&amp;line=2" in history.text


def test_receipt_history_and_playground_use_the_actual_query(client):
    number = "PO-0000003"
    page = client.get("/screens/ME23N", params={"key": number, "view": "history"})
    data = render_screen("ME23N", number, {})
    for receipt in data.runs["receipts"].result.rows:
        assert receipt[3] in page.text
    links = re.findall(r'href="(/playground\?sql=[^"]+)"', page.text)
    queries = [parse_qs(urlsplit(html.unescape(link)).query)["sql"][0] for link in links]
    assert queries == [run.display_sql for run in data.runs.values()]
    for query in queries:
        assert "$1" not in query
        assert number in query


def test_unknown_item_falls_back_and_unknown_order_stays_unloaded(client):
    page = client.get("/screens/ME23N", params={"key": "PO-0006693", "line": "999"})
    assert "Item 1 details" in page.text
    missing = client.get("/screens/ME23N", params={"key": "<script>bad</script>"})
    assert "was not found" in missing.text
    assert "<script>bad</script>" not in missing.text
    assert "Purchase order loaded" not in missing.text
