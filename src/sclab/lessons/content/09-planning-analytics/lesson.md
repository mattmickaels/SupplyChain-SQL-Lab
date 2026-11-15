+++
title = "Planning analytics"
summary = "Reconcile demand, material supply and build capacity, then test what a supplier delay does to customer orders."
+++

Planning answers three questions.
What do customers want, and when?
Can supply deliver it?
Is there room in the factory to build it?
Sales and operations planning (S&OP) is the meeting that answers all three together.

In Excel you would keep one sheet for each answer: a demand pivot, a stock and purchase-order sheet with a running balance, and a capacity chart.
In SQL each sheet becomes a CTE (lesson 7), and each CTE has one grain.
The grain is what one row means, for example one family and one month, or one material and one plant and one day.
Say the grain out loud before you join anything.
Bring two sources to the same grain first, then join them, or the totals multiply.
Bookings and forecasts are two views of the same demand, so never add them together.

This lesson is the capstone.
It reuses WITH RECURSIVE from lesson 8, window functions from lesson 7, dates from lesson 5 and NULL handling from lesson 6.
The new pieces are FULL OUTER JOIN, NULLIF, VALUES, CROSS JOIN and GREATEST.
Each one gets a short example before an exercise needs it.

## Rules used in every exercise

The lab "today" is the snapshot date, DATE '2026-11-01', a Sunday.
Everything that happened before it is history.
Everything after it is the plan.

Production demand always follows one rule.
Count a production order when its status is Planned or Released and its planned_start is after the snapshot.
In this lab the orders that start after the snapshot are all Planned.
Every Released order started on or before the snapshot, so it is already running.
We assume its parts have already been issued and are no longer in the stock count.
Counting them again would double-count, so they are left out on purpose.

```sql
SELECT status,
       CASE WHEN planned_start > DATE '2026-11-01' THEN 'after snapshot'
            ELSE 'on or before snapshot' END AS start_vs_snapshot,
       COUNT(*) AS orders
FROM production_order
GROUP BY status, CASE WHEN planned_start > DATE '2026-11-01' THEN 'after snapshot'
                      ELSE 'on or before snapshot' END
ORDER BY status
```

Completed orders are 1875, all started on or before the snapshot.
Planned orders are 250, all starting after it.
Released orders are 93, all started on or before it.
That is why the rule reads "Planned or Released, after the snapshot".

## Demand review

A demand review asks what customers have booked, what the forecast says, and what changed since last month.
Use promised_date to place an open order in a delivery month.
Keep forecast_version in view until you choose which version to use.

The first example totals the current forecast for November 2026, one row per family.

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

The rows are CMP 10, DEP 10, ETC 20 and INS 10.

Coverage is booked quantity as a percent of the forecast.
If the forecast is zero, a division fails.
NULLIF(x, 0) turns a zero into NULL, and any division by NULL returns NULL instead of an error.

```sql
SELECT 100.0 * 12 / NULLIF(10, 0) AS coverage_pct,
       100.0 * 12 / NULLIF(0, 0) AS coverage_when_forecast_is_zero
```

The first column is 120.0.
The second is NULL, not an error.

To compare bookings with the forecast, you need every family-month from either side.
An ordinary join drops a month that has a forecast but no bookings.
A FULL OUTER JOIN keeps unmatched rows from both sides, with NULL in the missing columns.
COALESCE(x, 0) then turns that NULL into zero.
This example uses family DEP from March to May 2027.

```sql
WITH booked AS (
    SELECT m.family_code, CAST(date_trunc('month', i.promised_date) AS DATE) AS month,
           SUM(i.qty) AS qty
    FROM sales_order_item i JOIN material m ON m.material_no = i.material_no
    WHERE i.status = 'Open' AND m.family_code = 'DEP' AND m.material_type = 'TOOL'
      AND i.promised_date >= DATE '2027-03-01' AND i.promised_date < DATE '2027-06-01'
    GROUP BY m.family_code, CAST(date_trunc('month', i.promised_date) AS DATE)
), forecast AS (
    SELECT m.family_code, f.forecast_month AS month, SUM(f.qty) AS qty
    FROM demand_forecast f JOIN material m ON m.material_no = f.material_no
    WHERE f.forecast_version = '2026-10' AND m.family_code = 'DEP' AND m.material_type = 'TOOL'
      AND f.forecast_month >= DATE '2027-03-01' AND f.forecast_month < DATE '2027-06-01'
    GROUP BY m.family_code, f.forecast_month
)
SELECT COALESCE(b.month, f.month) AS month, COALESCE(b.qty, 0) AS booked_qty,
       COALESCE(f.qty, 0) AS forecast_qty
FROM booked b FULL OUTER JOIN forecast f ON f.family_code = b.family_code AND f.month = b.month
ORDER BY month
```

March shows 6 booked against 10 forecast.
April and May have no bookings at all, yet both rows are kept, with booked 0 and forecast 10.

Check yourself: can joining the raw forecast rows to the raw sales lines multiply both totals?
Total each side by family and month first, then join.

## Supply review for one RF generator

Projected available balance (PAB) is opening stock, plus receipts, minus requirements.
Safety stock is a threshold, so a balance below it is a warning even while it is still positive.

Etch and deposition tools need RF generators somewhere deep in their bill of materials.
Lesson 8 walked that bill with WITH RECURSIVE.
Here the same walk starts from every tool at once, multiplies qty_per down each path, and keeps the root tool as a column.
A component can sit under a tool by more than one path, so you add the quantities for each root and component.
Keep that sum as a CTE called leaves: one row for each root tool and component, with the total quantity per tool.
Etch tool M-000011 needs four RF generators, two each of two kinds.

```sql
WITH RECURSIVE tree AS (
    SELECT m.material_no AS root, m.family_code, b.component_material_no AS material_no,
           CAST(b.qty_per AS DECIMAL(20,6)) AS qty, 1 AS level
    FROM material m JOIN bom_item b ON b.parent_material_no = m.material_no
    WHERE m.material_type = 'TOOL'
    UNION ALL
    SELECT t.root, t.family_code, b.component_material_no,
           CAST(t.qty * b.qty_per AS DECIMAL(20,6)), t.level + 1
    FROM tree t JOIN bom_item b ON b.parent_material_no = t.material_no
    WHERE t.level < 10
)
SELECT t.material_no, m.description, SUM(t.qty) AS qty_per_tool
FROM tree t JOIN material m ON m.material_no = t.material_no
WHERE t.root = 'M-000011' AND m.description LIKE 'RF generator,%'
GROUP BY t.material_no, m.description
ORDER BY t.material_no
```

The rows are M-001321, 13.56 MHz, 1.5 kW, two per tool, and M-001329, 2 MHz, 5 kW, two per tool.

Production demand for a component is the order quantity times that per-tool quantity.
Three production orders for M-000011 start from 2027-01-31 to 2027-02-08.
The middle order builds two tools, so it needs four M-001321.

```sql
WITH RECURSIVE tree AS (
    SELECT m.material_no AS root, m.family_code, b.component_material_no AS material_no,
           CAST(b.qty_per AS DECIMAL(20,6)) AS qty, 1 AS level
    FROM material m JOIN bom_item b ON b.parent_material_no = m.material_no
    WHERE m.material_type = 'TOOL'
    UNION ALL
    SELECT t.root, t.family_code, b.component_material_no,
           CAST(t.qty * b.qty_per AS DECIMAL(20,6)), t.level + 1
    FROM tree t JOIN bom_item b ON b.parent_material_no = t.material_no
    WHERE t.level < 10
)
SELECT p.prod_order_no, p.planned_start, p.qty AS tools, p.qty * t.qty AS rf_needed
FROM production_order p JOIN tree t ON t.root = p.material_no
WHERE p.material_no = 'M-000011' AND t.material_no = 'M-001321'
  AND p.status IN ('Planned', 'Released')
  AND p.planned_start >= DATE '2027-01-31' AND p.planned_start < DATE '2027-02-10'
ORDER BY p.planned_start
```

The rows are 2 on 2027-01-31, 4 on 2027-02-03 and 2 on 2027-02-08.

Spare parts are also demand.
A spare RF generator is sold on a sales line, and that line has no plant.
Supply is planned by plant, so the lab gives each spare to one plant by a fixed convention.
Look at which tool families use the generator.
Turn each family into its plant: Etch (ETC) to SC01, CMP to SG01, any other family to AU01.
Then take the alphabetically first plant.
Alphabetical order puts AU01 first, then SC01, then SG01.
RF generator M-001324 is used by DEP and ETC tools.

```sql
WITH RECURSIVE tree AS (
    SELECT m.material_no AS root, m.family_code, b.component_material_no AS material_no,
           CAST(b.qty_per AS DECIMAL(20,6)) AS qty, 1 AS level
    FROM material m JOIN bom_item b ON b.parent_material_no = m.material_no
    WHERE m.material_type = 'TOOL'
    UNION ALL
    SELECT t.root, t.family_code, b.component_material_no,
           CAST(t.qty * b.qty_per AS DECIMAL(20,6)), t.level + 1
    FROM tree t JOIN bom_item b ON b.parent_material_no = t.material_no
    WHERE t.level < 10
)
SELECT family_code,
       CASE family_code WHEN 'ETC' THEN 'SC01' WHEN 'CMP' THEN 'SG01'
                        ELSE 'AU01' END AS plant_code
FROM tree
WHERE material_no = 'M-001324'
GROUP BY family_code
ORDER BY family_code
```

DEP maps to AU01 and ETC maps to SC01, so the spare M-001324 is planned at AU01.

## Supply side: stock, purchase orders and dates

Use RM01 stock for component planning.
RF generator M-001324 has 1 on hand at AU01 and 2 on hand at SC01.
See a shortage on [MD04](/screens/MD04?key=M-001330&plant=SC01) for RF generator M-001330 at SC01, where the Etch orders run out.
See the bay load on [CM01](/screens/CM01?key=SC01).

```sql
SELECT plant_code, qty_on_hand
FROM inventory
WHERE material_no = 'M-001324' AND storage_location = 'RM01'
ORDER BY plant_code
```

Supply from a purchase order line is the outstanding quantity.
That is the ordered quantity minus the sum of all receipts on the same PO line.
A line is identified by po_no and line_no together.
The plant and the supplier live on the PO header, so join purchase_order.
Count only POs ordered on or before the snapshot.
HAVING keeps only the lines that still have something outstanding.
PO-0000003 line 1 was fully received, so HAVING drops it.
PO-0007027 line 1 has nothing received yet, so all 139 are outstanding, at plant SC01 from supplier V-10017.
In the exercises this query is a CTE called outstanding.

```sql
SELECT i.po_no, i.line_no, h.plant_code, h.supplier_no,
       i.order_qty - COALESCE(SUM(g.qty), 0) AS outstanding_qty
FROM purchase_order_item i
JOIN purchase_order h ON h.po_no = i.po_no
LEFT JOIN goods_receipt g ON g.po_no = i.po_no AND g.line_no = i.line_no
WHERE h.order_date <= DATE '2026-11-01'
  AND i.po_no IN ('PO-0000003', 'PO-0007027') AND i.line_no = 1
GROUP BY i.po_no, i.line_no, h.plant_code, h.supplier_no, i.order_qty
HAVING i.order_qty > COALESCE(SUM(g.qty), 0)
ORDER BY i.po_no
```

A line without a confirmed date is not scheduled supply, so leave it out.
Lines whose confirmed date has already passed and that are still outstanding are overdue.
Planning needs them somewhere, so put an overdue line on the snapshot date.
This is an optimistic assumption, not proof the goods will arrive, so overdue and unconfirmed lines still need a call to the supplier.
GREATEST(a, b) returns the later of two dates, which does exactly that.

```sql
SELECT GREATEST(DATE '2026-10-20', DATE '2026-11-01') AS overdue_line_lands_on,
       GREATEST(DATE '2026-11-20', DATE '2026-11-01') AS on_time_line_stays_on
```

The overdue date moves to 2026-11-01.
The on-time date stays on 2026-11-20.

## A weekly projection

The projection uses 26 weekly buckets that start on the snapshot Sunday.
The last bucket ends before 2027-05-02.
You need a bucket even in a week with no movement, so build the calendar yourself.
VALUES writes a small inline table, and n * INTERVAL 7 DAY turns each offset into a date.

```sql
SELECT CAST(DATE '2026-11-01' + n * INTERVAL 7 DAY AS DATE) AS week_start
FROM (VALUES (0), (1), (2)) AS offsets(n)
ORDER BY week_start
```

The rows are 2026-11-01, 2026-11-08 and 2026-11-15.

CROSS JOIN pairs every row on the left with every row on the right.
Use it when you want every key in every week, for example every plant in every bucket.

```sql
SELECT p.plant_code, COUNT(*) AS week_rows
FROM plant p CROSS JOIN (VALUES (0), (1), (2)) AS offsets(n)
GROUP BY p.plant_code
ORDER BY p.plant_code
```

Each of the 3 plants appears 3 times, so the calendar gives every plant the same weeks.

To sum a quantity into its week, LEFT JOIN the calendar to the events and put the date range in the ON condition.
Group by the week.
Weeks with no events stay in the result, and COALESCE turns their empty total into 0.
This example puts PO-0007027 line 1 into three weeks.

```sql
WITH periods AS (
    SELECT CAST(DATE '2026-11-01' + n * INTERVAL 7 DAY AS DATE) AS week_start
    FROM (VALUES (0), (1), (2)) AS offsets(n)
)
SELECT w.week_start, COALESCE(SUM(i.order_qty), 0) AS receipts
FROM periods w
LEFT JOIN purchase_order_item i
  ON i.po_no = 'PO-0007027' AND i.line_no = 1
 AND i.confirmed_delivery_date >= w.week_start
 AND i.confirmed_delivery_date < w.week_start + INTERVAL 7 DAY
GROUP BY w.week_start
ORDER BY w.week_start
```

The 139 lands in the first week because its confirmed date is 2026-11-02.
The next two weeks are 0.

Finally a running balance is a running SUM.
Add receipts minus demand over the weeks in order, starting from the opening stock.
With 2 on hand, the example below ends at -2 because three weeks of demand outrun the receipts.

```sql
SELECT week_start, receipts, demand_qty,
       2 + SUM(receipts - demand_qty) OVER (ORDER BY week_start
                                            ROWS UNBOUNDED PRECEDING) AS balance
FROM (VALUES (DATE '2026-11-01', 5, 3), (DATE '2026-11-08', 0, 4),
             (DATE '2026-11-15', 0, 2)) AS weeks(week_start, receipts, demand_qty)
ORDER BY week_start
```

The balances are 4, 0 and -2.
In the exercise, keep the balance separate for each material and plant with PARTITION BY, so one plant cannot fund another.

Check yourself: did you start from opening stock, count each PO line once, keep quiet weeks, and multiply quantities down every BOM path?

## From a shortage to an order at risk

A late purchase order is only a warning.
Earlier stock can still cover a production start that comes before the delivery.
The real question is when the supply total first reaches the need total.

UNION ALL stacks one list under another when both lists have the same columns.
This example stacks the opening stock and one purchase order line for RF generator M-001327 at AU01.

```sql
SELECT 'stock' AS source, DATE '2026-11-01' AS supply_date, qty_on_hand AS qty
FROM inventory
WHERE material_no = 'M-001327' AND plant_code = 'AU01' AND storage_location = 'RM01'
UNION ALL
SELECT po_no, confirmed_delivery_date, order_qty
FROM purchase_order_item
WHERE po_no = 'PO-0006656' AND line_no = 1
```

The two rows are the opening stock of 2 on 2026-11-01 and a PO line of 1 on 2026-11-02.

Build two ledgers for each material and plant, both in date order.
The need ledger adds up production demand and spare sales, and keeps a running total.
The supply ledger adds the opening stock on the snapshot date and every confirmed outstanding line, and keeps a running total.
Several events on the same day are added together first.

A production start is covered on the first supply date whose running supply total is at least the running need total on the start day.
Find that date with a LEFT JOIN on supply_total >= need_total, then GROUP BY and MIN.
This is a join, not a subquery.
A subquery that compares an outer column with a range condition is rejected by Databricks, and the join works everywhere.

The example has three starts needing a total of 4, 9 and 12.
Supply is 2 on the snapshot and 11 in total from 2026-11-26.

```sql
WITH need(need_date, cumulative_qty) AS (
    VALUES (DATE '2027-01-08', 4), (DATE '2027-01-24', 9), (DATE '2027-02-10', 12)
), supply(supply_date, cumulative_qty) AS (
    VALUES (DATE '2026-11-01', 2), (DATE '2026-11-26', 11)
)
SELECT n.need_date, n.cumulative_qty AS needed, MIN(s.supply_date) AS covered_on
FROM need n
LEFT JOIN supply s ON s.cumulative_qty >= n.cumulative_qty
GROUP BY n.need_date, n.cumulative_qty
ORDER BY n.need_date
```

The first two starts are covered on 2026-11-26.
The third need of 12 is never covered, so MIN returns NULL.
A start that nothing ever covers is left out of the risk list and goes to a separate unconfirmed-supply review.

The delay is the covering date minus planned_start.
datediff('day', start, end) counts the days from start to end.
A positive number is late and a negative number is early.

```sql
SELECT datediff('day', DATE '2027-02-23', DATE '2027-03-03') AS days_late,
       datediff('day', DATE '2027-02-23', DATE '2027-02-10') AS days_early
```

The result is 8, which is 8 days late, and -13, which is 13 days early.
Only positive delays count as at risk.

Do not add a sales line's revenue once for each component path.
Group the delays back to one row per sales line and take the largest delay.

The at-risk exercise is built in steps.
First give each RF generator its spare plant.
Then build the need ledger and the supply ledger for one generator, M-001327.
It is the only RF generator with an open spare sales line, so its need ledger shows the spare convention at work.
Then run all generators together and find the late Etch orders.

The what-if has the same shape.
It compares the plan as it stands with the plan after a supplier slips every confirmed date by 28 days.
It does not claim a customer shipment date moves by the same number of days.
The steps build the exposure, then a line-by-line comparison, then the counts.

## Capacity review

weekly_slots is the number of production starts a bay can take in a week.
It is not the number of tools standing in the bay.
Count one production order as one start slot.

Build the calendar first, with no reference to production orders.
Otherwise a week with no starts anywhere would disappear.
This example makes the 13 Monday weeks that touch December 2026 through February 2027, including the partial first week.
The anchor row has level 0, and each step adds one until level 12.

```sql
WITH RECURSIVE weeks(level) AS (SELECT 0 UNION ALL SELECT level + 1 FROM weeks WHERE level < 12)
SELECT CAST(DATE '2026-11-30' + level * INTERVAL 7 DAY AS DATE) AS week_start FROM weeks ORDER BY week_start
```

The first Monday is 2026-11-30 and the last is 2027-02-22.

CROSS JOIN this calendar to build_bay, then LEFT JOIN the starts, then COALESCE missing counts to 0.
Put the date range for the starts in the ON condition, not WHERE, so a bay with no starts stays in the result.

```sql
SELECT b.bay_id, b.weekly_slots,
       COUNT(p.prod_order_no) AS february_starts
FROM build_bay b LEFT JOIN production_order p ON p.bay_id = b.bay_id
 AND p.planned_start >= DATE '2027-02-01' AND p.planned_start < DATE '2027-03-01'
WHERE b.bay_id IN ('AU01-B1', 'SC01-B3')
GROUP BY b.bay_id, b.weekly_slots ORDER BY b.bay_id
```

Bay AU01-B1 has 3 slots a week and 0 February starts, and it still appears.
Bay SC01-B3 has 6 slots a week and 32 February starts.

Boundary weeks keep the full weekly_slots.
Only starts inside the date range count.
That is a simple rule for this lab, not a finite-capacity schedule.
Check yourself: does the calendar still return every week when production_order has no rows?
Spare slots in one week cannot cancel an overload in another.

## Exercises

Work in order.
The prompts say what to count, the horizon, and the assumptions a planner would otherwise have to agree with the business.
