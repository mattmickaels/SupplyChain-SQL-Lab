+++
title = "Planning analytics"
summary = "Reconcile demand, material supply and start-slot capacity, then test a supplier-delay scenario."
+++

Sales and operations planning (S&OP) reconciles a demand plan with a feasible supply plan.
Integrated business planning (IBP) connects that operational plan to financial choices and business priorities.
A demand review asks what customers have booked, what the forecast assumes, and what changed since the previous plan.
A supply review asks whether materials, supplier dates and build capacity can support those commitments.
Use the lab snapshot DATE '2026-11-01' throughout this capstone.

In Excel, this is a workbook with demand pivots, supplier lookups, a running stock balance and a capacity chart.
In SQL, give each sheet a CTE and give each CTE one explicit grain.
Aggregate two sources to the same grain before joining them.
Bookings and forecasts are alternative views of demand; adding both can double-count the same customer need.

## Demand review

Use promised dates to place open orders in delivery months, and order dates to measure bookings.
Keep forecast_version in the analysis until you deliberately select or compare versions.
A forecast waterfall shows how the plan changes between versions for the same family and month.
Forecast coverage divides booked demand by forecast; NULLIF makes a zero forecast return NULL rather than a division error.

This worked example groups the current forecast at family grain before any order join.

```sql
SELECT m.family_code, SUM(f.qty) AS forecast_qty
FROM demand_forecast f
JOIN material m ON m.material_no = f.material_no
WHERE f.forecast_version = '2026-10'
  AND f.forecast_month = DATE '2026-11-01'
  AND m.material_type = 'TOOL'
GROUP BY m.family_code
ORDER BY m.family_code
```

The checked output is recorded with this lesson's worked examples.
Use a FULL OUTER JOIN when either side may contain a family-month missing from the other side.
COALESCE supplies zero for a missing quantity without removing the family-month.

```sql
WITH booked AS (
    SELECT m.family_code, SUM(i.qty) AS qty
    FROM sales_order_item i JOIN material m ON m.material_no = i.material_no
    WHERE i.status = 'Open' AND m.material_type = 'TOOL'
      AND i.promised_date >= DATE '2026-11-01' AND i.promised_date < DATE '2026-12-01'
    GROUP BY m.family_code
), forecast AS (
    SELECT m.family_code, SUM(f.qty) AS qty
    FROM demand_forecast f JOIN material m ON m.material_no = f.material_no
    WHERE f.forecast_version = '2026-10' AND f.forecast_month = DATE '2026-11-01'
      AND m.material_type = 'TOOL'
    GROUP BY m.family_code
)
SELECT COALESCE(b.family_code, f.family_code) AS family_code,
       COALESCE(b.qty, 0) AS booked_qty, COALESCE(f.qty, 0) AS forecast_qty,
       ROUND(100.0 * COALESCE(b.qty, 0) / NULLIF(f.qty, 0), 2) AS coverage_pct
FROM booked b FULL OUTER JOIN forecast f ON f.family_code = b.family_code
ORDER BY family_code
```

Check yourself: could joining raw forecast versions to raw sales lines multiply both totals?
Name each CTE's grain before writing the join.

## Supply review and projected available balance

Projected available balance (PAB) is opening stock plus scheduled receipts minus planned requirements.
Safety stock is a planning threshold; a balance below it is a warning even if the balance is positive.
An end-of-week balance can hide a midweek shortage, so use dated demand and supply to investigate customer risk.
The projection exercises use 26 seven-day buckets starting on the snapshot Sunday.
The capacity exercises use ISO Monday weeks instead; the prompts identify the convention.

Before exploding a BOM, preserve the root tool and multiply qty_per down every path.
Use UNION ALL to retain repeated component paths, guard recursion with level < 10, and aggregate those paths before joining orders.
This example isolates a tool and counts its BOM paths by level.

```sql
WITH RECURSIVE tree AS (
    SELECT b.component_material_no AS material_no,
           CAST(b.qty_per AS DECIMAL(20,6)) AS qty, 1 AS level
    FROM bom_item b WHERE b.parent_material_no = 'M-000011'
    UNION ALL
    SELECT b.component_material_no, CAST(t.qty * b.qty_per AS DECIMAL(20,6)), t.level + 1
    FROM tree t JOIN bom_item b ON b.parent_material_no = t.material_no
    WHERE t.level < 10
)
SELECT level, COUNT(*) AS paths FROM tree GROUP BY level ORDER BY level
```

Use RM01 stock for component planning.
Count only outstanding supply, ordered quantity minus the sum of all receipts at the full PO-line key.
A missing confirmation is not scheduled supply.
GREATEST(confirmed_delivery_date, DATE '2026-11-01') places overdue outstanding supply in the snapshot bucket as an optimistic assumption, not evidence of arrival.
Escalate overdue and unconfirmed lines before acting on this projection.

```sql
SELECT i.po_no, i.line_no, i.order_qty - COALESCE(SUM(g.qty), 0) AS outstanding_qty
FROM purchase_order_item i
LEFT JOIN goods_receipt g ON g.po_no = i.po_no AND g.line_no = i.line_no
WHERE i.po_no = 'PO-0000003' AND i.line_no = 1
GROUP BY i.po_no, i.line_no, i.order_qty
```

To retain quiet weeks, create a calendar and CROSS JOIN it to the planning keys.
VALUES supplies a small inline table, and INTERVAL converts a bucket offset into a date.
This shortened worked example shows three buckets and the RF generator's opening stock with no future events applied.

```sql
WITH periods AS (
    SELECT CAST(DATE '2026-11-01' + n * INTERVAL 7 DAY AS DATE) AS week_start
    FROM (VALUES (0), (1), (2)) AS offsets(n)
), stock AS (
    SELECT COALESCE(SUM(qty_on_hand), 0) AS qty
    FROM inventory WHERE material_no = 'M-001324' AND plant_code = 'SC01'
      AND storage_location = 'RM01'
)
SELECT w.week_start, s.qty AS opening_qty,
       SUM(0) OVER (ORDER BY w.week_start ROWS UNBOUNDED PRECEDING) + s.qty AS balance
FROM periods w CROSS JOIN stock s ORDER BY w.week_start
```

Replace zero with receipts minus demand for a running PAB.
Partition the running SUM by material and plant so one plant cannot fund another plant's requirements.
COALESCE an empty weekly total to zero.
The exercise names M-001324 as its RF generator; confirm its master record before planning it.

```sql
SELECT material_no, description FROM material WHERE material_no = 'M-001324'
```

Check yourself: did you subtract receipts already reflected in opening stock, preserve every BOM path, and include quiet weeks?

## From a shortage to an order at risk

A PO arriving after a production start is a warning, but earlier stock can still cover that start.
Accumulate requirements by material, plant and need date, including spare RF sales demand when investigating order risk.
For this lab, spare demand uses the lowest where-used plant code after mapping ETC to SC01, CMP to SG01 and other families to AU01.
This is a stated allocation convention because spare sales lines have no plant key.
All demand on the same day shares a cumulative requirement total.
The first date cumulative stock and confirmed receipts meet that total is the coverage date.
Compare it with planned_start and take the largest positive delay at sales-line grain.
Do not add a sales line's revenue once for each component path.
If no confirmed date covers the requirement, flag it for a separate unconfirmed-supply review.

This real-table example uses a correlated subquery to ask how much confirmed outstanding supply covers one date.

```sql
WITH outstanding AS (
    SELECT i.material_no, h.plant_code, i.confirmed_delivery_date,
           i.order_qty - COALESCE(SUM(g.qty), 0) AS qty
    FROM purchase_order_item i JOIN purchase_order h ON h.po_no = i.po_no
    LEFT JOIN goods_receipt g ON g.po_no = i.po_no AND g.line_no = i.line_no
    WHERE h.order_date <= DATE '2026-11-01'
    GROUP BY i.po_no, i.line_no, i.material_no, h.plant_code,
             i.confirmed_delivery_date, i.order_qty
    HAVING i.order_qty > COALESCE(SUM(g.qty), 0)
)
SELECT m.material_no,
       COALESCE((SELECT SUM(o.qty) FROM outstanding o
                 WHERE o.material_no = m.material_no AND o.plant_code = 'SC01'
                   AND o.confirmed_delivery_date <= DATE '2026-12-01'), 0) AS due_qty
FROM material m WHERE m.material_no = 'M-001324'
```

Check yourself: a correlated subquery refers to a key from the outer row; does yours also restrict plant?
The what-if exercise reruns this allocation with a changeable slip_days parameter and counts newly affected sales lines.
It does not promise that customer shipment dates slip by the same number of days as the supplier.

## Capacity review

weekly_slots is capacity for production starts, not simultaneous occupied bays.
Count one production order as one start slot in this lab.
Create a complete bay-week grid before dividing starts by capacity, so quiet weeks stay visible.
Boundary weeks retain full capacity, while only starts inside the prompt's date range count.
This is an explicit convention, not a detailed finite-capacity schedule.

```sql
SELECT b.bay_id, b.weekly_slots,
       COUNT(p.prod_order_no) AS december_starts
FROM build_bay b LEFT JOIN production_order p ON p.bay_id = b.bay_id
 AND p.planned_start >= DATE '2026-12-01' AND p.planned_start < DATE '2027-01-01'
GROUP BY b.bay_id, b.weekly_slots ORDER BY b.bay_id
```

Check yourself: can spare slots in one week cancel an overload in another?
Keep a separate sum of positive weekly overload when recommending which starts to move.

Databricks uses date_trunc('MONTH', d) and date_trunc('WEEK', d) with the same arguments.
Day intervals such as d + INTERVAL 7 DAY are supported.
For day differences, replace datediff('day', start_date, end_date) with datediff(end_date, start_date).

## Capstone exercises

Work through demand review, supply review, customer risk and capacity before proposing a response to the supplier slip.
The prompts specify the grain, horizon and assumptions that a planner would otherwise have to agree with the business.
