+++
title = "Interview drills"
summary = "Answer six planning questions and explain the result in three useful lines."
+++
An interview answer needs a defensible query and a short explanation of what its result means.
Each drill allows 900 seconds and asks for a three-line executive summary.
State the number that matters, its planning implication, and a specific next action.
Separate what the data proves from what you infer.
Use DATE '2026-11-01' as the snapshot.
The last 12 months means DATE '2025-11-01' inclusive through DATE '2026-11-01' exclusive.

In Excel, you would bring a pivot and a short note to the planning meeting.
Here, build the SQL result first and use its numbers in the note.
Do not turn a supplier count into a shortage claim, or a family forecast into a customer forecast.

## A worked summary

This worked example counts the build bays, with an explicit population and no inference about their capacity.

```sql
SELECT plant_code, COUNT(*) AS bays
FROM build_bay GROUP BY plant_code ORDER BY plant_code
```

The result has AU01, SC01 and SG01, with 5 bays each.
A three-line explanation could read:

> Each of the three plants has 5 build bays in the master data.
> Bay counts alone cannot establish a capacity bottleneck because weekly slots and scheduled starts also matter.
> Compare weekly starts with each bay's slots before moving work.

Check yourself: does your first line give a measured result and does your action follow from that result?

## Tools for the drills

Use a total window SUM for a denominator and an ordered window SUM for a Pareto running share.
ROWS UNBOUNDED PRECEDING prevents tied values from being treated as one peer group.
Add a stable identifier to break ties in both the window and the final ordering.
This worked example measures how the master-data bay count is distributed among plants.

```sql
WITH counts AS (
    SELECT plant_code, COUNT(*) AS bays FROM build_bay GROUP BY plant_code
)
SELECT plant_code, bays,
       ROUND(100.0 * bays / SUM(bays) OVER (), 2) AS share_pct,
       ROUND(100.0 * SUM(bays) OVER (ORDER BY plant_code ROWS UNBOUNDED PRECEDING)
             / SUM(bays) OVER (), 2) AS cumulative_pct
FROM counts ORDER BY plant_code
```

For idle inventory, NOT EXISTS asks whether there is no matching usage record, without multiplying stock rows.
Production issues (261) and shipments (601) are usage; receipts and initial balances are not.
Aggregate stock before joining the one primary quote price per material.
This small example checks whether a known purchase line has no receipt records.

```sql
SELECT i.po_no, i.line_no
FROM purchase_order_item i
WHERE i.po_no = 'PO-0000003' AND i.line_no = 1
  AND NOT EXISTS (
      SELECT 1 FROM goods_receipt g
      WHERE g.po_no = i.po_no AND g.line_no = i.line_no
  )
```

This returns no rows because the purchase line has receipts.
Use the same anti-join pattern against stock_movement with the usage types and date range for the inventory drill.
Check yourself: would an unrestricted quote join duplicate stock value for materials with several suppliers?

Use the BOM path rollup from lesson 9 for dependency exposure, and count each order line once per component.
Exposure is the full open sales value dependent on a component, not the component purchase cost and not proven lost revenue.
Do not sum exposure across components because several components can support the same order.
Use the capacity grid from lesson 9 to keep empty weeks in the bottleneck denominator.
For this planning exercise, next quarter is explicitly December 2026 through February 2027.

## Six drills

For each question, agree the population, time window and row grain before joining tables.
Check one likely mistake, such as using booking dates for delivery performance or adding both forecast versions together.
Write your own summary before reviewing the model summary.
The model uses the seeded data, so its numbers should be reproducible from your result.
