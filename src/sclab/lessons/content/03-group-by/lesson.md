+++
title = "Group by and having"
summary = "Turn detail rows into one row per group, the way a pivot table does."
+++

GROUP BY collapses many rows into one row per group.
You then count, sum, or average inside that group.
A pivot table in Excel does the same job.
The group is the row label, and the aggregate is the value.

The plant table is small enough to see the whole result.
Two plants are in the US and one is in SG.

```sql
SELECT country, count(*) AS plant_count
FROM plant
GROUP BY country
ORDER BY country
```

WHERE filters rows before the groups are built.
HAVING filters the groups after the aggregate.
A cutoff such as "more than one supplier" belongs in HAVING, not WHERE.

count(*) counts every row in the group, including rows where a column is NULL.
count(planned_lead_time_days) skips the NULL rows.
Those two counts differ when the lead time was never filled in.
On the material plant table, each plant has more than a hundred missing lead times.

Check yourself.
Name the column you group by before you write the aggregate.
