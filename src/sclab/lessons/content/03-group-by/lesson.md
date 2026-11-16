+++
title = "Group by and having"
summary = "Turn detail rows into one row per group, the way a pivot table does."
+++

GROUP BY collapses many rows into one row per group.
You then count, sum, or average inside that group.
A pivot table in Excel does the same job.
The group is the row label, and the aggregate is the value.
Without Excel experience, think of sorting rows into buckets and calculating one answer for each bucket.
Every selected column must either identify a group (and appear in GROUP BY) or be inside an aggregate such as COUNT, SUM, or AVG.
For example, selecting an individual plant name while grouping only by country would leave SQL without a single plant name to return for the US group.

Write clauses in this order: SELECT, FROM, WHERE, GROUP BY, HAVING, ORDER BY, LIMIT.
Skip any clauses you do not need.
Conceptually, SQL reads the table, filters rows, builds groups, filters groups, chooses result columns, sorts, and limits the result.

This query counts plants by country.

```sql
SELECT country, COUNT(*) AS plant_count
FROM plant
GROUP BY country
ORDER BY country
```

Two rows come back.
SG has 1 plant.
US has 2 plants.

WHERE filters rows before the groups are built.
HAVING filters the groups after the aggregate.

This query keeps only countries with more than one plant.

```sql
SELECT country, COUNT(*) AS plant_count
FROM plant
GROUP BY country
HAVING COUNT(*) > 1
ORDER BY country
```

SG drops out, because its count is 1.
The only remaining row is US with 2 plants.

SUM adds a number down the group.
Tool M-000011 has seven direct components on bom_item.
Ordered by component number, the chamber module has qty_per 2 and the six platform modules have qty_per 1.
Those values add up to 8.

```sql
SELECT SUM(qty_per) AS total_qty_per
FROM bom_item
WHERE parent_material_no = 'M-000011'
```

AVG averages a number inside the group.
ROUND keeps a fixed number of decimal places.
Supplier V-10001 has an average quoted lead time of 54.1 days.

```sql
SELECT supplier_no, ROUND(AVG(quoted_lead_time_days), 1) AS avg_lead_time_days
FROM material_supplier
WHERE supplier_no = 'V-10001'
GROUP BY supplier_no
```

IN (...) keeps a row when the value is one of the listed items.

```sql
SELECT material_no, created_on
FROM material
WHERE material_no IN ('M-000001', 'M-000011')
```

Both materials were created on 31 October 2021.
The list names two materials, so two rows come back.

EXTRACT(YEAR FROM a date) pulls out the year as a number you can group by.
M-000001 and M-000011 were both created in 2021.

```sql
SELECT EXTRACT(YEAR FROM created_on) AS created_year, COUNT(*) AS material_count
FROM material
WHERE material_no IN ('M-000001', 'M-000011')
GROUP BY EXTRACT(YEAR FROM created_on)
ORDER BY created_year
```

One row comes back.
2021 has 2 materials.

COUNT(*) counts every row in the group, including rows where a column is NULL.
COUNT(planned_lead_time_days) skips the NULL rows.
At plant AU01, 153 rows have no planned lead time and 5167 rows have one.

```sql
SELECT plant_code,
       COUNT(*) - COUNT(planned_lead_time_days) AS missing_lead_time,
       COUNT(planned_lead_time_days) AS filled_lead_time
FROM material_plant
WHERE plant_code = 'AU01'
GROUP BY plant_code
```

Check yourself.
Name the column you group by before you write the aggregate.
