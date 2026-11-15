+++
title = "Subqueries, CTEs and window functions"
summary = "Filter with a query, name each step, and calculate across rows without collapsing them."
+++

A subquery is a query inside another query.
Excel does the same thing when a condition uses AVERAGE of a range.

This keeps bays whose weekly slots are above the average of every bay.

```sql
SELECT bay_id, weekly_slots
FROM build_bay
WHERE weekly_slots > (SELECT AVG(weekly_slots) FROM build_bay)
ORDER BY bay_id
```

Seven bays come back.
AU01-B4, AU01-B5, SC01-B1, SC01-B2, SC01-B3, SC01-B4, and SG01-B4 are above the average.

The average is computed on the whole table.
A filter in the outer query does not change that average.

A common table expression, written WITH, names a query so later steps can read it.
This is the same bay filter, with the bay list named once.

```sql
WITH bay_slots AS (
    SELECT bay_id, weekly_slots
    FROM build_bay
)
SELECT bay_id, weekly_slots
FROM bay_slots
WHERE weekly_slots > (SELECT AVG(weekly_slots) FROM bay_slots)
ORDER BY bay_id
```

The same seven bays come back.

Open purchase quantity is what is still due, not the original order quantity.
Sum goods receipts by po_no and line_no first, then subtract that total from order_qty.
Keep a line when the order date is on or before 1 November 2026 and the remaining quantity is above zero.

M-001331 is one RF generator.
Its open quantity is 32.

```sql
SELECT i.material_no,
       SUM(i.order_qty - COALESCE(r.received_qty, 0)) AS open_qty
FROM purchase_order_item AS i
JOIN purchase_order AS h ON h.po_no = i.po_no
LEFT JOIN (
    SELECT po_no, line_no, SUM(qty) AS received_qty
    FROM goods_receipt
    GROUP BY po_no, line_no
) AS r ON r.po_no = i.po_no AND r.line_no = i.line_no
WHERE h.order_date <= DATE '2026-11-01'
  AND i.material_no = 'M-001331'
  AND i.order_qty > COALESCE(r.received_qty, 0)
GROUP BY i.material_no
```

Twelve materials are described exactly as RF generator.
Spare RF generator kit, M-001561, is a different material.
It is not a long-lead part.

Compare each RF generator's open quantity with the average of those twelve totals.
Three of them are above that average.

```sql
WITH received AS (
    SELECT po_no, line_no, SUM(qty) AS received_qty
    FROM goods_receipt
    GROUP BY po_no, line_no
),
open_by_material AS (
    SELECT i.material_no,
           SUM(i.order_qty - COALESCE(r.received_qty, 0)) AS open_qty
    FROM purchase_order_item AS i
    JOIN purchase_order AS h ON h.po_no = i.po_no
    JOIN material AS m ON m.material_no = i.material_no
    LEFT JOIN received AS r ON r.po_no = i.po_no AND r.line_no = i.line_no
    WHERE h.order_date <= DATE '2026-11-01'
      AND m.description = 'RF generator'
      AND i.order_qty > COALESCE(r.received_qty, 0)
    GROUP BY i.material_no
)
SELECT material_no, open_qty
FROM open_by_material
WHERE open_qty > (SELECT AVG(open_qty) FROM open_by_material)
ORDER BY open_qty DESC, material_no
```

M-001325 has 1153 still open, M-001324 has 1020, and M-001323 has 864.

Check yourself.
Average the material totals, not the individual purchase lines.

RANK() gives the same rank to a tie and skips the next rank.
ROW_NUMBER() never repeats a rank, so a tie becomes two different numbers.
On time means receipt_date is on or before confirmed_delivery_date.
The last 12 months run from 1 November 2025 up to, and not including, 1 November 2026.

These three suppliers show the tie.
Both Harbor suppliers are rank 2.

```sql
WITH rates AS (
    SELECT s.supplier_no,
           s.name AS supplier_name,
           COUNT(*) AS receipts,
           ROUND(
               100.0 * AVG(
                   CASE
                       WHEN g.receipt_date <= i.confirmed_delivery_date THEN 1.0
                       ELSE 0.0
                   END
               ),
               1
           ) AS on_time_pct
    FROM goods_receipt AS g
    JOIN purchase_order_item AS i ON i.po_no = g.po_no AND i.line_no = g.line_no
    JOIN purchase_order AS h ON h.po_no = g.po_no
    JOIN supplier AS s ON s.supplier_no = h.supplier_no
    WHERE g.receipt_date >= DATE '2025-11-01'
      AND g.receipt_date < DATE '2026-11-01'
      AND s.supplier_no IN ('V-10064', 'V-10076', 'V-10078')
    GROUP BY s.supplier_no, s.name
)
SELECT supplier_no,
       supplier_name,
       receipts,
       on_time_pct,
       RANK() OVER (ORDER BY on_time_pct DESC) AS rank_no
FROM rates
ORDER BY rank_no, supplier_no
```

Fern Thermal Systems is rank 1 at 95.8 percent, on 24 receipts.
Harbor Thermal Systems and Harbor Gas Delivery are both rank 2 at 90.7 percent, on 43 receipts each.

A window function cannot be filtered in the same WHERE that builds the rows.
Compute ROW_NUMBER() in a named step, then keep row 1 in an outer query.
Line 1 of PO-0000104 has three receipts.
The latest date wins, and the higher gr_no wins when the date is the same.

```sql
SELECT gr_no,
       receipt_date,
       qty,
       ROW_NUMBER() OVER (
           ORDER BY receipt_date DESC, gr_no DESC
       ) AS rn
FROM goods_receipt
WHERE po_no = 'PO-0000104'
  AND line_no = 1
ORDER BY rn
```

GR-0109573 on 23 October 2026 is row 1, for a quantity of 2.
The other two receipts stay in the list until an outer query drops them.

SUM() OVER (ORDER BY month) is a running total.
Aggregate to one row per month first, or each receipt is added on its own.
Rows from January through March 2026, for materials described exactly as RF generator, look like this.

```sql
WITH monthly AS (
    SELECT CAST(date_trunc('month', g.receipt_date) AS DATE) AS month,
           SUM(g.qty) AS month_qty
    FROM goods_receipt AS g
    JOIN purchase_order_item AS i ON i.po_no = g.po_no AND i.line_no = g.line_no
    JOIN material AS m ON m.material_no = i.material_no
    WHERE m.description = 'RF generator'
      AND g.receipt_date >= DATE '2026-01-01'
      AND g.receipt_date < DATE '2026-04-01'
    GROUP BY CAST(date_trunc('month', g.receipt_date) AS DATE)
)
SELECT month,
       month_qty,
       SUM(month_qty) OVER (
           ORDER BY month ROWS UNBOUNDED PRECEDING
       ) AS running_qty
FROM monthly
ORDER BY month
```

January is 371 and the running total is 371.
February is 385 and the running total is 756.
March is 188 and the running total is 944.

On Databricks, date_trunc('MONTH', receipt_date) matches date_trunc('month', receipt_date).

September and October 2026 have no receipts of an RF generator.
A month with no receipts does not appear when you group the receipt rows.

LAG(order_qty) reads the previous row in the order you name.
The first row has no previous row, so the change is NULL.
These are tool sales orders, material_type TOOL, for the first three months of 2026.

```sql
WITH monthly AS (
    SELECT CAST(date_trunc('month', h.order_date) AS DATE) AS month,
           SUM(i.qty) AS order_qty
    FROM sales_order_item AS i
    JOIN sales_order AS h ON h.so_no = i.so_no
    JOIN material AS m ON m.material_no = i.material_no
    WHERE m.material_type = 'TOOL'
      AND h.order_date >= DATE '2026-01-01'
      AND h.order_date < DATE '2026-04-01'
    GROUP BY CAST(date_trunc('month', h.order_date) AS DATE)
)
SELECT month,
       order_qty,
       order_qty - LAG(order_qty) OVER (ORDER BY month) AS qty_change
FROM monthly
ORDER BY month
```

Each of those months has order quantity 40.
January's change is NULL.
February and March change by 0.

On Databricks, date_trunc('MONTH', order_date) matches date_trunc('month', order_date).

SUM(open_qty) OVER () is the total of every row in the window, with no partition.
Filter after that total is computed.
A WHERE in the same query as the window would drop the other customers from the total.

Halcyon Memory, customer C-2003, has 41 open tools.
That is 13.4 percent of open tool quantity, rounded to 1 decimal.

```sql
WITH open_lines AS (
    SELECT h.customer_no,
           c.name AS customer_name,
           SUM(i.qty) AS open_qty
    FROM sales_order_item AS i
    JOIN sales_order AS h ON h.so_no = i.so_no
    JOIN customer AS c ON c.customer_no = h.customer_no
    JOIN material AS m ON m.material_no = i.material_no
    WHERE i.status = 'Open'
      AND m.material_type = 'TOOL'
    GROUP BY h.customer_no, c.name
),
shares AS (
    SELECT customer_no,
           customer_name,
           open_qty,
           ROUND(100.0 * open_qty / SUM(open_qty) OVER (), 1) AS share_pct
    FROM open_lines
)
SELECT customer_no, customer_name, open_qty, share_pct
FROM shares
WHERE customer_no = 'C-2003'
```

ROW_NUMBER() OVER (PARTITION BY country ...) restarts the count in each country.
Keep rank 1 to 3 in an outer query.
When spend ties, the lower supplier_no comes first, so the top three are a single result.
This is the largest supplier in the US for orders placed in the last 12 months.
Spend is order quantity times net price, rounded to 2 decimals.

```sql
WITH spend AS (
    SELECT s.country,
           s.supplier_no,
           s.name AS supplier_name,
           ROUND(SUM(i.order_qty * i.net_price), 2) AS spend
    FROM purchase_order_item AS i
    JOIN purchase_order AS h ON h.po_no = i.po_no
    JOIN supplier AS s ON s.supplier_no = h.supplier_no
    WHERE s.country = 'US'
      AND h.order_date >= DATE '2025-11-01'
      AND h.order_date < DATE '2026-11-01'
    GROUP BY s.country, s.supplier_no, s.name
),
ranked AS (
    SELECT country,
           supplier_no,
           supplier_name,
           spend,
           ROW_NUMBER() OVER (
               PARTITION BY country
               ORDER BY spend DESC, supplier_no
           ) AS rank_no
    FROM spend
)
SELECT country, supplier_no, supplier_name, spend, rank_no
FROM ranked
WHERE rank_no = 1
```

Kindred Quartz Ceramics, V-10113, is rank 1 in the US, with spend 26119815.52.

Check yourself.
Name the grain of each step before you rank, lag, or take a share.
