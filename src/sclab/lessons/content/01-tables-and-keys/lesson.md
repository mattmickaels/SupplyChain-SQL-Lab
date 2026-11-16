+++
title = "Tables, rows and keys"
summary = "A table is a named sheet, and a key matches at most one row."
+++

## Your first query

SQL (often pronounced "sequel") is a language for asking a database questions.
A database holds named tables of data.
A SQL table holds one kind of thing, with one row per thing and one column per fact.
You read it with SELECT, name the columns you want, and name the table with FROM.

This lab uses a made-up equipment manufacturer.
A plant is a factory location, a material is a part or product, and a supplier is a company that sells parts.
A purchase order asks a supplier for goods; each order line names one material and a quantity.
You do not need to know the business to learn SQL here.

Type this query into the SQL box under "Every plant column" below and press Run:

```sql
SELECT plant_code, name, city, country
FROM plant;
```

SELECT lists the columns to show, separated by commas.
FROM names the table to read.
The result is another table: a header row and three rows of plant data.
Run lets you inspect the result; Check tells you whether it answers that exercise.
This query answers the first exercise, so press Check after inspecting its rows.

SQL keywords such as SELECT can be uppercase or lowercase.
Spaces and line breaks make queries easier to read; they do not end the query.
A semicolon ends a statement and is optional for the single queries in this lab.
Run one statement at a time.
Use the column names from the Schema page, including underscores such as plant_code.
Text values use single quotes, as in 'US'; column names such as country do not.
If you get an error, check the spelling, commas, and quotes, then try again.
Lesson queries only read data, so experimenting cannot damage the tables.

SELECT * means "show every column".
Naming columns explicitly is usually easier to understand and keeps unwanted columns out of the result.
Without ORDER BY, row order is not guaranteed; lesson 2 shows how to choose an order.

## Tables and keys

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
AS gives a result column a name: row_count below is a label you choose, not a column already in plant.

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

WHERE keeps rows that pass a test.

```sql
SELECT plant_code, name
FROM plant
WHERE country = 'US'
```

Two rows remain.
AU01 is Austin, and SC01 is Santa Clara.

Check yourself.
Point at a table and name the column, or the columns, that match at most one row.
If you cannot, you do not know which row a value refers to.
