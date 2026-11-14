+++
title = "Tables, rows and keys"
summary = "A table is a named sheet, and a key matches at most one row."
+++

A SQL table holds one kind of thing, with one row per thing and one column per fact.
You read it with SELECT, name the columns you want, and name the table with FROM.

In Excel, the sheet is the table, the header row is the column names, and each data row is one record.
A key guarantees at most one matching row for a value.
A filter on a key can still return no rows, and a column that is not a key can happen to match one row.
When one column can repeat, the key is the set of columns that still guarantees at most one row.

product_family is a small table in this lab.
Each row is one tool family.

```sql
SELECT family_code, name
FROM product_family
```

That query returns four rows.
The codes are DEP, ETC, CMP, and INS.
The names are Deposition, Etch, CMP, and Inspection.
family_code is the key, because a code matches at most one family.

COUNT(*) counts rows.
On plant, that count is 3.

```sql
SELECT COUNT(*) AS row_count
FROM plant
```

COUNT(DISTINCT country) counts different values in one column.
The plants use only US and SG, so the distinct count is 2.

```sql
SELECT COUNT(DISTINCT country) AS country_count
FROM plant
```

Check yourself.
Point at a table and name the column, or the columns, that match at most one row.
If you cannot, you do not know which row a value refers to.
