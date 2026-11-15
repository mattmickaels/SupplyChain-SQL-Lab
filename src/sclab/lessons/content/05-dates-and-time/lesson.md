+++
title = "Dates and time"
summary = "Measure gaps between dates, then group those dates into weeks, months, and quarters."
+++

A date column is a day on the calendar, not text.
Subtracting two dates, or adding a number of days, is how you measure a promise against what happened.
In Excel this is the same job as subtracting two date cells, or using WEEKNUM and EOMONTH to bucket them.

datediff('day', start_date, end_date) counts the days from the start date to the end date.
On Databricks, datediff(end_date, start_date) is the same count.
A goods receipt on the confirmed day is not late.
Late means the receipt date is after the confirmed date.

Purchase order PO-0000010 line 1 was confirmed for 6 July 2022.
Goods receipt GR-0010740 arrived on 7 July 2022.
That receipt is 1 day late.

```sql
SELECT datediff('day', i.confirmed_delivery_date, g.receipt_date) AS days_late
FROM goods_receipt AS g
JOIN purchase_order_item AS i ON i.po_no = g.po_no AND i.line_no = g.line_no
WHERE g.gr_no = 'GR-0010740'
```

Adding a whole number of days uses INTERVAL.
confirmed_delivery_date + INTERVAL 1 DAY is the next day.
On Databricks, date_add(confirmed_delivery_date, 1) is the same day.
For this line, the confirmed date plus 1 day is 7 July 2022, the day GR-0010740 arrived.

```sql
SELECT confirmed_delivery_date + INTERVAL 1 DAY AS day_after_confirmed
FROM purchase_order_item
WHERE po_no = 'PO-0000010'
  AND line_no = 1
```

The same line was ordered on 20 May 2022.
The first receipt is the earliest receipt_date on the line.
From the order date to that first receipt is 48 days.

```sql
SELECT datediff('day', h.order_date, MIN(g.receipt_date)) AS actual_lead_time_days
FROM purchase_order_item AS i
JOIN purchase_order AS h ON h.po_no = i.po_no
JOIN goods_receipt AS g ON g.po_no = i.po_no AND g.line_no = i.line_no
WHERE i.po_no = 'PO-0000010'
  AND i.line_no = 1
GROUP BY h.order_date
```

A late rate is the late receipts divided by all receipts, times 100.
ROUND(..., 1) keeps one decimal.
Use 100.0 so the division keeps the fraction.
On this one line all 3 receipts are after the confirmed date, so the late rate is 100.0.

```sql
SELECT COUNT(*) AS receipt_count,
       ROUND(
           100.0 * SUM(CASE WHEN g.receipt_date > i.confirmed_delivery_date THEN 1 ELSE 0 END)
           / COUNT(*),
           1
       ) AS late_pct
FROM goods_receipt AS g
JOIN purchase_order_item AS i ON i.po_no = g.po_no AND i.line_no = g.line_no
WHERE g.po_no = 'PO-0000010'
  AND g.line_no = 1
```

date_trunc('month', order_date) snaps a day back to the first of its month.
On Databricks the unit is written date_trunc('MONTH', order_date).
Sales order lines placed in January 2025 number 576.
That count includes cancelled lines, because the order was still booked.

```sql
SELECT date_trunc('month', h.order_date) AS booked_month,
       COUNT(*) AS line_count
FROM sales_order_item AS i
JOIN sales_order AS h ON h.so_no = i.so_no
WHERE h.order_date >= DATE '2025-01-01'
  AND h.order_date < DATE '2025-02-01'
GROUP BY date_trunc('month', h.order_date)
```

date_trunc('week', a date) snaps the day back to the Monday of its week.
On Databricks the unit is written date_trunc('WEEK', a date).
PO-0002506 line 1 is confirmed for 1 November 2026.
That confirmed date falls in the week starting 26 October 2026.

```sql
SELECT date_trunc('week', confirmed_delivery_date) AS week_start
FROM purchase_order_item
WHERE po_no = 'PO-0002506'
  AND line_no = 1
```

EXTRACT(QUARTER FROM a date) returns 1, 2, 3, or 4.
EXTRACT(YEAR FROM a date) returns the year.
GR-0010740 is in quarter 3 of 2022.

```sql
SELECT EXTRACT(YEAR FROM receipt_date) AS receipt_year,
       EXTRACT(QUARTER FROM receipt_date) AS receipt_quarter
FROM goods_receipt
WHERE gr_no = 'GR-0010740'
```

A recency test adds a number of days to the latest date and compares that result with the lab's today, DATE '2026-11-01'.
More than N days before today means latest_date + INTERVAL N DAY is still before 1 November 2026.
On Databricks, date_add(latest_date, N) is the same comparison.
Every customer has an order on or after 22 October 2026.
The latest order of any kind is never 180 days old.
A tool order is a sales order line for a material whose material_type is TOOL.
Quiet Memory, customer C-2009, last ordered a tool on 24 June 2026.
That date plus 90 days is 22 September 2026, which is before 1 November 2026.
That date plus 180 days is 21 December 2026, which is after 1 November 2026.
No customer's latest tool order is more than 365, 270, or 180 days before 1 November 2026.

```sql
SELECT c.customer_no,
       c.name,
       MAX(h.order_date) AS last_tool_order_date,
       MAX(h.order_date) + INTERVAL 90 DAY AS plus_90_days
FROM customer AS c
JOIN sales_order AS h ON h.customer_no = c.customer_no
JOIN sales_order_item AS i ON i.so_no = h.so_no
JOIN material AS m ON m.material_no = i.material_no
WHERE c.customer_no = 'C-2009'
  AND m.material_type = 'TOOL'
GROUP BY c.customer_no, c.name
```

Corvane RF Systems is supplier V-10017.
On-time means the receipt date is on or before the confirmed date.
In the third quarter of 2026, Corvane has 28 receipts and an on-time rate of 53.6.
In the fourth quarter of 2026, Corvane has 2 receipts and an on-time rate of 0.0.
Receipts in this lab stop at the end of October 2026, so that fourth quarter is October only.

```sql
SELECT EXTRACT(QUARTER FROM g.receipt_date) AS receipt_quarter,
       COUNT(*) AS receipt_count,
       ROUND(
           100.0 * SUM(CASE WHEN g.receipt_date <= i.confirmed_delivery_date THEN 1 ELSE 0 END)
           / COUNT(*),
           1
       ) AS on_time_pct
FROM goods_receipt AS g
JOIN purchase_order_item AS i ON i.po_no = g.po_no AND i.line_no = g.line_no
JOIN purchase_order AS h ON h.po_no = g.po_no
WHERE h.supplier_no = 'V-10017'
  AND g.receipt_date >= DATE '2026-07-01'
  AND g.receipt_date < DATE '2027-01-01'
GROUP BY EXTRACT(QUARTER FROM g.receipt_date)
ORDER BY receipt_quarter
```

Check yourself.
Name the start date and the end date before you subtract, and write the lab's today as DATE '2026-11-01'.
