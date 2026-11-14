+++
title = "Joins"
summary = "Look up a matching row from another table, and do not let one match become many."
+++

An inner join keeps rows that match in both tables.
VLOOKUP does this when it looks up a name from a code.

This query looks up the supplier name for one purchase order.

```sql
SELECT h.po_no, s.name
FROM purchase_order AS h
JOIN supplier AS s ON s.supplier_no = h.supplier_no
WHERE h.po_no = 'PO-0043687'
```

One row comes back.
The supplier name is Drift Circuit.

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

A join can multiply rows.
Line 1 of PO-0043687 orders 18 units and has three goods receipts.

```sql
SELECT i.po_no, i.line_no, SUM(i.order_qty) AS summed_order_qty
FROM purchase_order_item AS i
JOIN goods_receipt AS g
  ON g.po_no = i.po_no AND g.line_no = i.line_no
WHERE i.po_no = 'PO-0043687' AND i.line_no = 1
GROUP BY i.po_no, i.line_no
```

summed_order_qty is 54, because 18 was added once per receipt.
Sum the receipts first, then join that one row back.

```sql
SELECT i.po_no, i.line_no, i.order_qty, r.received_qty
FROM purchase_order_item AS i
JOIN (
    SELECT po_no, line_no, SUM(qty) AS received_qty
    FROM goods_receipt
    GROUP BY po_no, line_no
) AS r ON r.po_no = i.po_no AND r.line_no = i.line_no
WHERE i.po_no = 'PO-0043687' AND i.line_no = 1
```

order_qty stays 18.
received_qty is 18.

Subtracting two dates gives the number of days between them.
Line 1 of PO-0012389 was requested for 18 January 2027 and confirmed for 15 March 2027.

```sql
SELECT i.confirmed_delivery_date - i.requested_delivery_date AS slip_days
FROM purchase_order_item AS i
WHERE i.po_no = 'PO-0012389' AND i.line_no = 1
```

The gap is 56 days.
Corvane RF Systems, supplier V-10017, has open lines with a gap at least that long.
Those late lines are why six open Etch tool orders are at risk in this lab.
Lesson 9 is where you build that at-risk list.

Check yourself.
Name the key you join on before you add the next table.
