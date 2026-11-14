+++
title = "Filter and sort"
summary = "WHERE keeps the rows you want, and ORDER BY puts those rows in an order you can explain."
+++

WHERE throws away rows that fail a test.
ORDER BY sorts the rows that remain.
Write the lab's today as DATE '2026-11-01'.
That date is 1 November 2026.

In Excel, WHERE is a column filter.
ORDER BY is the sort button, A to Z, or Z to A when you want the largest values first.

This query keeps the US plants and sorts them by plant code.

```sql
SELECT plant_code, name
FROM plant
WHERE country = 'US'
ORDER BY plant_code
```

Two rows remain.
AU01 is Austin, and SC01 is Santa Clara.
SG01 is Singapore, so the filter removes it.
Austin comes before Santa Clara because AU01 comes before SC01.

A blank value does not pass a comparison.
A confirmed date that is blank is not after today, and a blank lead time is not between two numbers.

Each exercise in this lesson reads one table, except the open lines for customer C-2003.
The customer number is on sales_order, and the line is on sales_order_item, so that exercise reads both.

Check yourself.
Say which rows your WHERE removes.
If two kept rows could trade places without breaking the ORDER BY, add the next column that makes the order unique.
