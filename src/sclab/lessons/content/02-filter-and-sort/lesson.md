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

LIKE uses % for any text in that spot.
'%RF generator%' matches the phrase anywhere, including Spare RF generator kit.
'RF generator%' matches only when the description starts with that phrase.

```sql
SELECT description
FROM material
WHERE description LIKE '%RF generator%'
```

BETWEEN includes both ends.
A lead time of 120 or 180 is inside BETWEEN 120 AND 180.
A blank lead time is not.

```sql
SELECT material_no
FROM material_plant
WHERE planned_lead_time_days BETWEEN 120 AND 180
```

ORDER BY name DESC sorts Z to A.
On plant, that order is Singapore, then Santa Clara, then Austin.

```sql
SELECT name
FROM plant
ORDER BY name DESC
```

LIMIT keeps only the first rows after the sort.
LIMIT 2 on plant codes keeps AU01 and SC01.

```sql
SELECT plant_code
FROM plant
ORDER BY plant_code
LIMIT 2
```

AS names a computed column.
name_length below is the number of characters in the plant name.
Santa Clara is 12 characters, Singapore is 9, and Austin is 6.

```sql
SELECT name, LENGTH(name) AS name_length
FROM plant
```

Each exercise in this lesson reads one table.

Check yourself.
Say which rows your WHERE removes.
If two kept rows could trade places without breaking the ORDER BY, add the next column that makes the order unique.
