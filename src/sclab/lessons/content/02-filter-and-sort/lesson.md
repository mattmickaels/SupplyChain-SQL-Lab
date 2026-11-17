+++
title = "Filter and sort"
summary = "WHERE keeps the rows you want, and ORDER BY puts those rows in an order you can explain."
+++

## Business brief

You are helping purchasing and customer service turn large lists into focused review queues.
Use filters to select the right records and sorting to make follow-up easier.
For each business question, distinguish a useful screening list from a conclusion about delivery risk.

## SQL skills

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

## Writing filter conditions

Use = for equal, <> for not equal, > and < for greater and less, and >= and <= to include the boundary.
Put text in single quotes, numbers without quotes, and dates after DATE: country = 'US', planned_lead_time_days >= 120, or created_on < DATE '2026-11-01'.
Boolean columns store TRUE or FALSE: write is_long_lead = TRUE without quotes.
AND requires both conditions to be true; OR requires at least one.
Use parentheses when mixing them so the intended grouping is clear.
SQL evaluates AND before OR.

```sql
SELECT plant_code, name
FROM plant
WHERE country = 'US'
  AND (city = 'Austin' OR city = 'Santa Clara')
ORDER BY plant_code
```

Both US plants remain.
The parentheses keep the two city choices together under the country filter.

A missing value is called NULL; it is different from zero or an empty text value ('').
A NULL value does not pass an ordinary comparison.
A confirmed date that is blank is not after today, and a blank lead time is not between two numbers.
Use IS NULL to find missing values and IS NOT NULL to find filled values, never = NULL.
Lesson 6 explores missing values in more detail.

LIKE uses % for any text in that spot.
The wildcard _ matches exactly one character.
LIKE is case-sensitive in this lab, so copy the capitalization in the exercise.
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
For a repeatable top-ten result, include enough ORDER BY columns to break ties.
For example, ORDER BY net_value DESC, po_no, line_no sorts by value first, then order number, then line number when values match.
LIMIT 2 on plant codes keeps AU01 and SC01.

```sql
SELECT plant_code
FROM plant
ORDER BY plant_code
LIMIT 2
```

AS names a computed column.
Use +, -, *, and / for arithmetic: order_qty * net_price AS net_value multiplies quantity by price and labels the result net_value.
Here * multiplies two values; SELECT * uses the same symbol to request all columns.
name_length below is the number of characters in the plant name.
Santa Clara is 11 characters, Singapore is 9, and Austin is 6.

```sql
SELECT name, LENGTH(name) AS name_length
FROM plant
```

Each exercise in this lesson reads one table.

Check yourself.
Say which rows your WHERE removes.
If two kept rows could trade places without breaking the ORDER BY, add the next column that makes the order unique.
