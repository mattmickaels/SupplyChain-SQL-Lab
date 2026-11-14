import duckdb

from sclab.data.schema import TABLES, create_all


def test_every_table_and_column_has_description() -> None:
    assert TABLES
    names = [item.name for item in TABLES]
    assert len(names) == len(set(names))
    for item in TABLES:
        assert item.description.strip()
        assert item.columns
        column_names = [column.name for column in item.columns]
        assert len(column_names) == len(set(column_names))
        for column in item.columns:
            assert column.description.strip()


def test_primary_key_columns_exist() -> None:
    for item in TABLES:
        assert item.primary_key
        column_names = {column.name for column in item.columns}
        for key in item.primary_key:
            assert key in column_names


def test_foreign_keys_reference_existing_columns() -> None:
    by_name = {item.name: item for item in TABLES}
    for item in TABLES:
        column_names = {column.name for column in item.columns}
        for column, ref_table, ref_column in item.foreign_keys:
            assert column in column_names
            assert ref_table in by_name
            ref_columns = {col.name for col in by_name[ref_table].columns}
            assert ref_column in ref_columns


def test_create_all_builds_tables_with_comments() -> None:
    conn = duckdb.connect()
    try:
        create_all(conn)
        tables = conn.execute(
            """
            SELECT table_name, comment, has_primary_key, sql
            FROM duckdb_tables()
            WHERE schema_name = 'main' AND NOT internal
            """
        ).fetchall()
        found = {name: (comment, has_pk, sql) for name, comment, has_pk, sql in tables}
        expected = {item.name for item in TABLES} | {"lab_meta"}
        assert set(found) == expected
        for item in TABLES:
            comment, has_pk, sql = found[item.name]
            assert comment == item.description
            assert has_pk
            assert "FOREIGN KEY" not in sql.upper()
        assert found["lab_meta"][1]
        columns = conn.execute(
            """
            SELECT table_name, column_name, comment
            FROM duckdb_columns()
            WHERE schema_name = 'main' AND NOT internal
            """
        ).fetchall()
        comment_by_column = {(table, column): comment for table, column, comment in columns}
        for item in TABLES:
            for column in item.columns:
                assert comment_by_column[(item.name, column.name)] == column.description
    finally:
        conn.close()
