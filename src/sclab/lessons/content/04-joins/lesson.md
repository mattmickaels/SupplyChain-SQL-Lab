+++
title = "Joins"
summary = "Look up a matching row from another table, and do not let one match become many."
+++

An inner join keeps rows that match in both tables.
VLOOKUP does this when it looks up a name from a code.
The purchase order header holds the supplier number.
The supplier name lives on the supplier table.
Join them on supplier_no.

A left join keeps every row from the first table.
When nothing matches, the columns from the second table are NULL.
That NULL is how you find a material with no open purchase order line.

A join can multiply rows.
Line 1 of PO-0043687 orders 18 units.
That line has three goods receipts, and together they received 18.
Join the receipts first and then sum order_qty, and the 18 is added once per receipt.
The total becomes 54, which is not the quantity ordered.
Sum the receipt quantity by po_no and line_no first.
Then join that one row back to the purchase order line.

Corvane RF Systems is supplier V-10017.
On its open lines, the confirmed date sits at least 56 days after the date the buyer requested.
Those late lines are why six open Etch tool orders are at risk in this lab.
Lesson 9 is where you build that at-risk list.

Check yourself.
Name the key you join on before you add the next table.
