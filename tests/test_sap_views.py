"""SAP-named views over the English tables."""

import pytest
from fastapi.testclient import TestClient

from sclab.data.sap_views import SAP_VIEWS
from sclab.web.app import create_app


@pytest.fixture
def client(lab_home):
    test_client = TestClient(create_app(), base_url="http://127.0.0.1")
    return test_client


def _column_names(conn, relation: str) -> list[str]:
    return [row[0] for row in conn.execute(f"DESCRIBE {relation}").fetchall()]


def _view_names(conn) -> set[str]:
    rows = conn.execute(
        """
        SELECT view_name
        FROM duckdb_views()
        WHERE schema_name = 'main' AND NOT internal
        """
    ).fetchall()
    return {row[0] for row in rows}


def test_every_sap_view_exists(lab_conn) -> None:
    assert set(SAP_VIEWS) == {
        "mara",
        "marc",
        "mard",
        "lfa1",
        "kna1",
        "stpo",
        "ekko",
        "ekpo",
        "ekbe",
        "vbak",
        "vbap",
        "afko",
        "mseg",
        "qmel",
    }
    assert _view_names(lab_conn) >= set(SAP_VIEWS)


def test_view_row_counts_match_base_tables(lab_conn) -> None:
    for view_name, base_name in SAP_VIEWS.items():
        view_count = lab_conn.execute(f"SELECT count(*) FROM {view_name}").fetchone()[0]
        base_count = lab_conn.execute(f"SELECT count(*) FROM {base_name}").fetchone()[0]
        assert view_count == base_count
        assert view_count > 0


def test_view_columns_use_sap_names(lab_conn) -> None:
    mara = _column_names(lab_conn, "mara")
    assert "matnr" in mara
    assert "material_no" not in mara
    assert "family_code" in mara
    assert "is_long_lead" in mara

    ekpo = _column_names(lab_conn, "ekpo")
    for name in ("ebeln", "ebelp", "matnr", "menge"):
        assert name in ekpo
    assert "po_no" not in ekpo
    assert "requested_delivery_date" in ekpo


def test_lesson_query_on_view_matches_base(lab_conn) -> None:
    english = lab_conn.execute(
        """
        SELECT material.material_no, purchase_order_item.po_no,
               purchase_order_item.line_no, purchase_order_item.order_qty
        FROM material
        JOIN purchase_order_item
          ON material.material_no = purchase_order_item.material_no
        ORDER BY 1, 2, 3
        """
    ).fetchall()
    sap = lab_conn.execute(
        """
        SELECT mara.matnr, ekpo.ebeln, ekpo.ebelp, ekpo.menge
        FROM mara
        JOIN ekpo ON mara.matnr = ekpo.matnr
        ORDER BY 1, 2, 3
        """
    ).fetchall()
    assert english
    assert sap == english


def test_schema_page_lists_sap_views(client) -> None:
    response = client.get("/schema")
    assert response.status_code == 200
    assert "<h2>SAP views</h2>" in response.text
    for view_name, base_name in SAP_VIEWS.items():
        assert f">{view_name}<" in response.text
        assert f">{base_name}<" in response.text
