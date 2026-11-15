+++
title = "Joins"
summary = "Look up a matching row from another table, and do not let one match become many."
+++

An inner join keeps rows that match in both tables.
VLOOKUP does this when it looks up a name from a code.

AS h is a short name for a table in one query.
This query uses h for the purchase order and s for the supplier.
h.po_no means the po_no column from that purchase order.

```sql
SELECT h.po_no, s.name
FROM purchase_order AS h
JOIN supplier AS s ON s.supplier_no = h.supplier_no
WHERE h.po_no = 'PO-0043687'
```

One row comes back.
The supplier name is Kindred Precision Assemblies.

A left join keeps every row from the first table.
When nothing matches, the columns from the second table are NULL.

This query looks for bay SG01-B9, which is not in the lab.

```sql
SELECT p.plant_code, b.bay_id
FROM plant AS p
LEFT JOIN build_bay AS b
  ON b.plant_code = p.plant_code
 AND b.bay_id = 'SG01-B9'
WHERE p.plant_code = 'SG01'
```

The plant row stays.
bay_id is NULL, because that bay does not exist.

A test written as = NULL does not find those blank values.
The comparison is unknown, so the row is thrown away.

```sql
SELECT p.plant_code, b.bay_id
FROM plant AS p
LEFT JOIN build_bay AS b
  ON b.plant_code = p.plant_code
 AND b.bay_id = 'SG01-B9'
WHERE p.plant_code = 'SG01'
  AND b.bay_id = NULL
```

No rows come back.

IS NULL is the test that keeps a blank value.

```sql
SELECT p.plant_code, b.bay_id
FROM plant AS p
LEFT JOIN build_bay AS b
  ON b.plant_code = p.plant_code
 AND b.bay_id = 'SG01-B9'
WHERE p.plant_code = 'SG01'
  AND b.bay_id IS NULL
```

One row comes back.
plant_code is SG01, and bay_id is NULL.

That pattern is an anti-join.
A left join plus WHERE the right-hand key IS NULL keeps left rows that had no match.

A condition on the right-hand table goes in ON when you want to keep left rows that have no match.
Put that condition in WHERE only when you mean to drop those rows.
In WHERE, the blank match is thrown away, so the left join behaves like an inner join.

The query above keeps the condition b.bay_id = 'SG01-B9' in ON, and the plant row stays.
Move that test to WHERE and the plant row disappears.

```sql
SELECT p.plant_code, b.bay_id
FROM plant AS p
LEFT JOIN build_bay AS b
  ON b.plant_code = p.plant_code
WHERE p.plant_code = 'SG01'
  AND b.bay_id = 'SG01-B9'
```

No rows come back.

COALESCE replaces a NULL with a value you choose.
It does not change a real bay id.

```sql
SELECT p.plant_code, COALESCE(b.bay_id, 'none') AS bay_id
FROM plant AS p
LEFT JOIN build_bay AS b
  ON b.plant_code = p.plant_code
 AND b.bay_id = 'SG01-B9'
WHERE p.plant_code = 'SG01'
```

The same plant row now shows none instead of NULL.

A derived table is a query in FROM, with an alias.
The outer query reads it like any other table.

```sql
SELECT plant_code, bay_count
FROM (
    SELECT plant_code, COUNT(*) AS bay_count
    FROM build_bay
    GROUP BY plant_code
) AS bay_totals
ORDER BY plant_code
```

Three rows come back.
AU01, SC01, and SG01 each have 5 bays.

A join can multiply rows.
Line 1 of PO-0000003 orders 25 and has three goods receipts of 9, 8, and 8.

```sql
SELECT i.po_no, i.line_no, SUM(i.order_qty) AS summed_order_qty
FROM purchase_order_item AS i
JOIN goods_receipt AS g
  ON g.po_no = i.po_no AND g.line_no = i.line_no
WHERE i.po_no = 'PO-0000003' AND i.line_no = 1
GROUP BY i.po_no, i.line_no
```

summed_order_qty is 75, because 25 was added once per receipt.
Sum the receipts first, then join that one row back.

```sql
SELECT i.po_no, i.line_no, i.order_qty, r.received_qty
FROM purchase_order_item AS i
JOIN (
    SELECT po_no, line_no, SUM(qty) AS received_qty
    FROM goods_receipt
    GROUP BY po_no, line_no
) AS r ON r.po_no = i.po_no AND r.line_no = i.line_no
WHERE i.po_no = 'PO-0000003' AND i.line_no = 1
```

order_qty stays 25.
received_qty is 25.

When a group has exactly one supplier, MIN(supplier_no) returns that supplier.
supplier_no has to sit inside an aggregate when it is not in the GROUP BY.
HAVING COUNT(*) = 1 is what makes the minimum the only supplier.

Write the test as confirmed_delivery_date >= requested_delivery_date + INTERVAL 56 DAY, because that sum is a date-time (timestamp) on both DuckDB and Databricks.
A date-time still compares correctly with a date.
Date minus date is not used here, because those engines do not return the same kind of value.

Line 1 of PO-0006802 was requested for 28 December 2026 and confirmed for 22 February 2027.
The confirmed date is the requested date plus 56 days.

```sql
SELECT i.po_no, i.line_no
FROM purchase_order_item AS i
WHERE i.po_no = 'PO-0006802'
  AND i.line_no = 1
  AND i.confirmed_delivery_date >= i.requested_delivery_date + INTERVAL 56 DAY
```

That filter keeps the line.
Corvane RF Systems, supplier V-10017, has open lines that pass the same test.
The RF generator lines among those late lines are why a handful of open Etch tool orders are at risk.
Other late lines from Corvane are not that shortage.
Lesson 9 is where you build that at-risk list.

Check yourself.
Name the key you join on before you add the next table.
