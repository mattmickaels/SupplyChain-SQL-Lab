+++
title = "NULLs and cleaning"
summary = "Find the blank, the zero, and the near-duplicate before you trust an average."
+++

## Business brief

Planning has asked the master-data team to review missing lead times and suspiciously similar records.
The business exercises produce review reports, not automatic master-data changes.
The trimming, counting, and averaging drills focus directly on how SQL treats imperfect data.

## SQL skills

NULL means the value was never filled in.
It is not zero, and it does not equal a blank string.
A comparison with NULL is unknown, so WHERE planned_lead_time_days = NULL keeps nothing.
Use IS NULL when you mean the value is missing.
In Excel, TRIM and UPPER clean text, and AVERAGE skips blank cells.
SQL AVG does the same: it skips NULL and still counts 0.

TRIM removes spaces at both ends of text.
Two descriptions that match after TRIM are candidates, not proof that the material numbers are the same part.
Two legacy gasket kit rows, M-005402 and M-005403, match after TRIM.
Neither is a copy of a real row, because no material has the clean text.
Gas box, variant 2 is M-001344, and Gas box, variant 15 is M-001435.
The variant number makes them different parts.
No two materials share an exact description.
A planted copy differs only by spaces at the end.
A planted copy is a row whose description differs from a real material only by trailing spaces.
M-002019 is Robot servo motor, variant 15.
M-005351 is the same words with a trailing space, so its description is 30 characters and M-002019 is 29.
M-005352 is Gas box, variant 20 with a trailing space, and M-001470 is the real row.

```sql
SELECT material_no, description, LENGTH(description) AS description_length
FROM material
WHERE material_no IN ('M-002019', 'M-005351')
ORDER BY material_no
```

After TRIM, both descriptions are Robot servo motor, variant 15.

```sql
SELECT material_no, TRIM(description) AS trimmed_description
FROM material
WHERE material_no IN ('M-002019', 'M-005351')
ORDER BY material_no
```

CASE picks a value row by row.
SUM of a CASE that returns 1 or 0 counts the rows that match.
COUNT(planned_lead_time_days) cannot do this job, because it counts 0 and skips only NULL.
At plant AU01, 153 lead times are NULL, 43 are 0, and 5124 are greater than 0.

```sql
SELECT plant_code,
       SUM(CASE WHEN planned_lead_time_days IS NULL THEN 1 ELSE 0 END) AS null_count,
       SUM(CASE WHEN planned_lead_time_days = 0 THEN 1 ELSE 0 END) AS zero_count,
       SUM(CASE WHEN planned_lead_time_days > 0 THEN 1 ELSE 0 END) AS valid_count
FROM material_plant
WHERE plant_code = 'AU01'
GROUP BY plant_code
```

The same rows produce three different averages.
AVG(planned_lead_time_days) skips NULL and keeps 0.
AVG(COALESCE(planned_lead_time_days, 0)) treats NULL as 0.
AVG of a CASE that returns NULL for 0 and for NULL drops both.
At AU01 those averages are 56.9, 55.3, and 57.4 days.

```sql
SELECT ROUND(AVG(planned_lead_time_days), 1) AS avg_ignoring_null,
       ROUND(AVG(COALESCE(planned_lead_time_days, 0)), 1) AS avg_null_as_zero,
       ROUND(AVG(CASE WHEN planned_lead_time_days > 0 THEN planned_lead_time_days END), 1) AS avg_excluding_null_and_zero
FROM material_plant
WHERE plant_code = 'AU01'
```

Purchase order units were typed three ways.
EA has 60713 lines, ea has 778 lines, and EA with a trailing space has 778 lines.
UPPER(TRIM(uom)) turns all three into EA.

```sql
SELECT uom, LENGTH(uom) AS uom_length, COUNT(*) AS line_count
FROM purchase_order_item
GROUP BY uom, LENGTH(uom)
ORDER BY uom
```

Customer country has the same kind of split.
US has 2 customers and US with a trailing space has 1 customer.
TRIM makes one country, US, with 3 customers.
Customer C-2001 is the row whose country has the trailing space.

```sql
SELECT country AS country_raw,
       TRIM(country) AS country,
       COUNT(*) AS raw_count
FROM customer
WHERE TRIM(country) = 'US'
GROUP BY country, TRIM(country)
ORDER BY country_raw
```

COALESCE(first, second) returns the first value when it is not NULL, and the second value otherwise.
It does not replace 0, because 0 is a real number.
It also stays NULL when both sides are NULL.
M-001345 at AU01 has no planned lead time and a primary quote of 176, so the filled value is 176.
M-004879 at SG01 has no planned lead time and a primary quote of 0, so the filled value is 0.
M-000002 at AU01 has no planned lead time and no quote to copy, so the filled value stays blank.

```sql
SELECT p.material_no,
       p.plant_code,
       p.planned_lead_time_days,
       s.quoted_lead_time_days,
       COALESCE(p.planned_lead_time_days, s.quoted_lead_time_days) AS filled_lead_time_days
FROM material_plant AS p
LEFT JOIN material_supplier AS s
  ON s.material_no = p.material_no
 AND s.is_primary = true
WHERE (p.material_no = 'M-000002' AND p.plant_code = 'AU01')
   OR (p.material_no = 'M-004879' AND p.plant_code = 'SG01')
   OR (p.material_no = 'M-001345' AND p.plant_code = 'AU01')
ORDER BY p.material_no
```

A cleaned material list removes only those planted copies.
Keep the real row: the lower material_no whose description has no extra space.
For Robot servo motor, variant 15, the kept number is M-002019 and the removed number is M-005351.
EXISTS keeps an outer row when the inner query finds a match.
M-005351 is listed as a removed row, because M-002019 holds its trimmed description.

```sql
SELECT copy.material_no FROM material AS copy
WHERE copy.material_no = 'M-005351' AND EXISTS (SELECT 1 FROM material AS kept WHERE kept.description = TRIM(copy.description))
```

Do not drop every material that shares a trimmed description.
Doing that to the two legacy gasket kit rows would delete one of them, although no real row holds that text.

```sql
SELECT material_no
FROM material
WHERE TRIM(description) = 'Robot servo motor, variant 15'
  AND description <> TRIM(description)
```

```sql
SELECT MIN(material_no) AS first_material_no,
       COUNT(*) AS material_count
FROM material
WHERE TRIM(description) = 'Legacy gasket kit, variant 1'
```

Check yourself.
Decide what should happen to NULL and to 0 before you average.
Treat a shared description as a candidate, and delete a row only when the extra characters are trailing spaces.
