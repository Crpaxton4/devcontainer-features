---
name: estimate
description: "Produce an hours estimate: Low/High per leaf snapped to the Fibonacci ladder, parents carrying the raw sum with the nearest Fibonacci beside it, cited anchors, assumptions, exclusions, recorded adjustments. Use for \"estimate this\", \"how many hours\", \"re-cut the numbers\", \"size this change\"."
user-invocable: false
---
# Estimate

Hours only. Money is a human matter. The output is the template at the end, filled in.

## 1. Find the house standard

1. Search the project's records for an existing standard: a knowledge article on
   estimating, or a template seeded in sibling task descriptions (an aspect table
   under an `### Estimate details` heading).
2. Found: conform to it exactly. Map each piece of work to one of its rows. Add no row.
3. Not found: use the free-form table below.
4. Record which one you used on the `Standard:` line, and name the sources you read.

A tool error is not "no standard". Name the lost source as an open question.

## 2. Decompose into leaves

One leaf is one unit of work you can picture someone doing. Split until that is true.
Configuration, rollout, training, migration and documentation are leaves of their own,
never a share of an implementation line. On one measured task implementation was 25% of
logged effort and documentation plus training was 46%.

## 3. Anchor the ladder

Before any number, find observed effort for comparable work: logged hours on a past
task, a shipped feature of the same shape. State the anchor per rung you use:

> `1` = a trivial field addition. Observed: 0.58h on task 30210.
> `13` = an external API integration with auth. Observed: 10h on task 29877, still in testing, so 10h is a floor.

No anchor found: write `unanchored` on that line. Measure what a query can resolve
(record counts, model sizes) instead of padding for it.

## 4. Snap each leaf

Give each leaf a Low and a High. Snap each to the ladder `1 2 3 5 8 13 21 34 55 89`:
nearest rung, ties go down. `0` only for an aspect that does not apply. No other value
is a legal leaf.

## 5. Roll up

- The parent carries the raw sum of its snapped leaves. Show the nearest Fibonacci
  beside the sum, never instead of it. At the top of the ladder a whole leaf can vanish
  without moving the rounded number (64 → 55; 49 → 55).
- Compare scope options on the sum, never on the rounded total.
- Check the sum against comparable whole work, with the logged hours cited. A sum far
  above what the whole job cost means the leaves are wrong: re-cut them.
- Summing Lows and summing Highs assumes perfect correlation. Neither total is an
  expectation. A single planning figure sits above the midpoint.

## 6. Adjustments

An owner's fiat ("make that one an 8") or factor ("cut it 25%") is legitimate. Record it
as an adjustment with its basis and the pre-adjustment range. Never bake it into the
leaves. The same downward tie-break applied many times is a reduction: say so. To
shrink scope, cut line items. Never shave hours across the board.

## 7. Assumptions and exclusions, always

- Assumptions: every condition the hours depend on (version and edition, data quality,
  access, scope frozen at these items, owner availability for acceptance).
- Exclusions: what is out of scope, so creep is visible.

## Output template

```
Standard: <house standard name | free-form>
Sources: <task ids, articles, records read>

| # | Item | Low | High | Basis |
|---|------|----:|-----:|-------|
| 1 | ...  |   3 |    5 | anchor: 2.9h on task NNN |
|   | TOTAL (raw sum) | L | H | nearest Fib L–H |

## Assumptions
## Exclusions
## Adjustments            (none, or one line each with basis and pre-adjustment range)
## Known weaknesses       (each with the direction it points)
## Open questions         (decisions that block the estimate)
```

## Stop conditions

- An unknown you can measure is not a risk to buffer. Look it up.
- Never invent a number to fill a cell. An unknown is an open question.
- Never report a rounded total without the raw sum beside it.
- Never emit the free-form table when a house standard was found.
