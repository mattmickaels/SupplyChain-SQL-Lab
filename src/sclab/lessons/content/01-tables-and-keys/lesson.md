+++
title = "Tables, rows and keys"
summary = "A table is a named sheet, and a key is what makes one row unique."
+++

A SQL table holds one kind of thing, with one row per thing and one column per fact.
You read it with SELECT, name the columns you want, and name the table with FROM.

In Excel, the sheet is the table, the header row is the column names, and each data row is one record.
A filter on one column returns one row only when that column is a key.
When a column can repeat, the key is the set of columns that still points at one row.

product_family is a small table in this lab.
Each row is one tool family.

```sql
SELECT family_code, name
FROM product_family
```

That query returns four rows.
The codes are DEP, ETC, CMP, and INS.
The names are Deposition, Etch, CMP, and Inspection.
family_code is the key, because no two families share a code.

Check yourself.
Point at a table and name the column, or the columns, that cannot repeat.
If you cannot, you do not know which row a value refers to.
