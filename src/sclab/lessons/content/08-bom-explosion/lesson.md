+++
title = "BOM explosion"
summary = "Walk a four-level bill of materials down and back up, and stop the walk with a depth guard."
+++

This lesson is optional and advanced.
A bill of materials lists what goes into a parent.
The lab's bill has four levels: tool, module, subassembly, and component.
There is no fifth level.

A direct child is one join, the same lookup as lesson 4.
M-000001 is Deposition system, 300mm, 2-chamber.
One of its direct children is the deposition chamber module, and that link uses 2 per tool.

```sql
SELECT b.component_material_no, m.description, b.qty_per
FROM bom_item AS b
JOIN material AS m ON m.material_no = b.component_material_no
WHERE b.parent_material_no = 'M-000001'
  AND b.component_material_no = 'M-000041'
```

The row is M-000041, Deposition chamber module, quantity 2.

A full explosion needs WITH RECURSIVE.
The anchor selects the first children and sets level to 1.
The recursive term joins the next children and adds 1 to level.
Put WHERE level < 10 inside that recursive term.
An outer filter does not stop the walk.
The guard is a safety stop.
The deepest descendant level in this lab is 3, so 10 is well past the real bill.

M-000011 is a tool, Etch system, 300mm, 2-chamber.
Its explosion has three descendant levels.

```sql
WITH RECURSIVE tree AS (
    SELECT b.component_material_no AS material_no, 1 AS level
    FROM bom_item AS b
    WHERE b.parent_material_no = 'M-000011'
    UNION ALL
    SELECT b.component_material_no, t.level + 1
    FROM tree AS t
    JOIN bom_item AS b ON b.parent_material_no = t.material_no
    WHERE t.level < 10
)
SELECT t.level, m.material_type, COUNT(*) AS links
FROM tree AS t
JOIN material AS m ON m.material_no = t.material_no
GROUP BY t.level, m.material_type
ORDER BY t.level, m.material_type
```

Level 1 is 7 modules.
Level 2 is 36 subassemblies.
Level 3 is 178 component links.
Nothing comes back at level 4.

None of those seven direct children is a long-lead part.
Long-lead parts sit further down the bill, so a one-level join cannot count them.

Extended quantity multiplies qty_per down the path.
The chamber module M-000111 uses 2 per tool.
Under that module, M-000501 uses 2 per module, so one tool needs 4.

```sql
WITH RECURSIVE tree AS (
    SELECT b.component_material_no AS material_no,
           CAST(b.qty_per AS DECIMAL(20,6)) AS extended_qty,
           1 AS level
    FROM bom_item AS b
    WHERE b.parent_material_no = 'M-000011'
      AND b.component_material_no = 'M-000111'
    UNION ALL
    SELECT b.component_material_no,
           CAST(t.extended_qty * b.qty_per AS DECIMAL(20,6)),
           t.level + 1
    FROM tree AS t
    JOIN bom_item AS b ON b.parent_material_no = t.material_no
    WHERE t.level < 10
      AND b.component_material_no = 'M-000501'
)
SELECT level, material_no, extended_qty
FROM tree
ORDER BY level
```

Level 1 is M-000111 with extended quantity 2.
Level 2 is M-000501 with extended quantity 4.

Where-used walks the other way, from a component up to every parent.
Keep the same level < 10 guard inside the recursive term.
M-001321 is an RF generator.
Its parents, from the nearest one upward, are a subassembly, a module, and one tool.

```sql
WITH RECURSIVE used AS (
    SELECT parent_material_no AS material_no, 1 AS level
    FROM bom_item
    WHERE component_material_no = 'M-001321'
    UNION ALL
    SELECT b.parent_material_no, u.level + 1
    FROM used AS u
    JOIN bom_item AS b ON b.component_material_no = u.material_no
    WHERE u.level < 10
)
SELECT u.level, t.material_no, t.description, t.material_type
FROM used AS u
JOIN material AS t ON t.material_no = u.material_no
ORDER BY u.level, t.material_no
```

Level 1 is M-000504, RF match network subassembly, variant 184.
Level 2 is M-000111, Etch chamber module.
Level 3 is M-000011, Etch system, 300mm, 2-chamber.

Use UNION ALL so a repeated path is not dropped.
Sum extended quantity by component before you multiply by an order quantity.
Otherwise one order is counted once per path and then again.

Check yourself.
The depth guard sits in the recursive term, on a column named level, as level < 10.
