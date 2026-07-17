---
name: fibonacci-estimate
description: Break work into a line-item estimate where every leaf value snaps to the Fibonacci ladder (1, 2, 3, 5, 8, 13, 21, 34, 55) measured in hours, and parents carry both the raw sum and the nearest Fibonacci. Use this whenever the user asks to estimate, size, scope, break down, split, or quote a piece of work in hours — and especially when they mention Fibonacci, story points, planning poker, or relative sizing but want hours as the unit rather than points. Also use it when re-cutting an existing estimate, applying a reduction factor or discount, splitting an estimate into subtasks, or rolling subtask numbers up to a parent, even when they never say "Fibonacci" out loud.
---

<!-- feature-managed; overwritten on container create — edit in the devcontainer-features repo (src/personal-features/skills/fibonacci-estimate/). -->

# Fibonacci estimates in hours

## Why the ladder

Fibonacci sizing normally carries story points, and it carries them for a reason: humans cannot reliably tell a 6 from a 7, and a scale that offers both invites an argument that produces no information. The gaps widen as the numbers grow because *uncertainty grows with size*. At 1–3 hours you can picture the work concretely. At 21 you are guessing at a shape. Offering "18" claims a precision you do not have.

Using hours instead of points is a deliberate hybrid. Points are for velocity; hours are for allocation and billing, and that is usually why the unit is hours. Keep the ladder's discipline — commit to a bucket, don't split the difference — while keeping hours as the unit. The argument about *which bucket* is worth having. The argument about 17 versus 18 is not.

The ladder: **1, 2, 3, 5, 8, 13, 21, 34, 55, 89**. Use `0` only for an aspect that genuinely does not apply. Nothing else is a legal leaf value.

## The shape of the work

1. **Decompose into leaves.** One leaf = one independently-estimable unit of work. Split until each leaf is something you can picture someone doing.
2. **Anchor the ladder before you use it** (see below). An unanchored Fibonacci number is a vibe with extra steps.
3. **Snap each leaf to the ladder.** Nearest rung; ties go down.
4. **Roll up: parents carry the raw sum *and* the nearest Fibonacci.** Never only the rounded value — the next section explains what that costs.

## The aggregation trap — the part people get wrong

Rounding is safe at the leaf. It is lossy at the parent, and the loss grows exactly where it hurts.

Real example. Six subtasks, raw sums totalling **64** at the high end. Nearest rung: **55**. Drop an entire optional subtask worth 5–15 hours and the total falls to **49** — whose nearest rung is *also* **55**. A whole subtask disappeared without moving the headline number. At the top of the ladder the gaps (13 → 21 → 34 → 55) are wider than the things you are adding and removing.

So:

- **Always show the raw sum beside the Fibonacci.** Two columns, not one.
- **Compare scope options on the sum, never on the rounded total.** "What if we drop X?" is a question the rounded number physically cannot answer.
- **Use the Fibonacci for the headline** — the commitment, the bucket, the thing that goes on the card.

If someone asks you to report only the rounded total, say plainly what it will hide before you do it.

## Rounding has a direction, and direction is a decision

Ties-round-down is a fine convention. But apply it across twenty leaves, or stack it with a truncation rule, or add an owner-directed cut on top, and you have a systematic reduction wearing a rounding rule as a disguise.

Track it. If the same tie-break is applied fifteen times, that is a real reduction and it belongs in the open, not in a footnote about tie-breaking. When a rounding convention and a scope cut and a discount all point the same way, say so — the reader deserves to know the number has been pushed downhill three times.

## Anchor the ladder to observed effort

A Fibonacci estimate is only as good as what the rungs mean. Before assigning any number, find real evidence of what comparable work actually cost — logged time on a similar past task, a shipped feature of the same shape. Then state the anchor:

> `1` = a trivial field addition. Observed: 0.58h of dev on a comparable past task.
> `13` = an external API integration with auth. Observed: 10h implementation on a comparable, and that task was still in testing, so 10h is a floor.

Anchors do three things: they make the numbers arguable on evidence rather than on confidence, they expose when you have no evidence at all, and they let the next person recalibrate instead of re-guessing.

If you cannot find an anchor, say the estimate is unanchored. That is information. Padding is not.

**Measure what you can cheaply measure.** An unknown you can resolve with a query is not a risk to buffer — it is a fact you have not looked up. Measuring often *shrinks* an estimate: discovering a data migration touches 215 records rather than an unknown number turns a scary line into a small one.

## Low and High, not a single number

Give each leaf a Low (best case) and a High (worst case). Two points beat one; they carry the uncertainty the single number hides.

Two things to be honest about when rolling up:

- **Summing lows and summing highs assumes perfect correlation** — every leaf hitting best case at once, then every leaf hitting worst case at once. Neither total is an expectation.
- **Software actuals are right-skewed.** If a single planning figure is needed, it sits above the midpoint of the band, not on it.

## Keep judgement overlays visible

When an owner sets a value by fiat ("make that one an 8") or applies a factor ("cut it 25%, we'll simplify"), that is legitimate — they hold context the decomposition does not. Record it as what it is:

> Adjustment: a 25% reduction applied at the estimate owner's direction, on the basis that a small team will simplify scope. Pre-adjustment: 39–76h. This is a judgement factor layered on the decomposition, not a re-derivation of it.

Baking the factor silently into the leaves launders a decision into false precision. The next reader cannot tell evidence from instruction, and cannot undo the adjustment when the basis for it changes.

**If scope will genuinely shrink, cut line items — don't shave hours.** Dropping a leaf is true and legible: you know exactly what you are not getting. Reducing every leaf by a quarter while keeping the full scope on paper is how estimates become fiction.

## Don't hide the overhead

Configuration, rollout, training, and migration are real work and they are the first things to vanish into a "documentation" line. They deserve their own leaves.

This matters more than it sounds. On one real task, implementation was **25%** of logged effort and documentation plus training was **46%** — the exact inverse of how it had been estimated. If your breakdown is two-thirds implementation, check that assumption against what comparable work actually cost, and say so if you cannot.

## Output format

Leaf tables carry Fibonacci values. Parent tables carry both:

```
| # | Item              | Sum     | Fib      |
|---|-------------------|:-------:|:--------:|
| 1 | Central bot       | 7 – 17  | 8 – 13   |
| 2 | Configuration     | 3 – 8   | 3 – 8    |
| 3 | Rollout           | 2 – 4   | 2 – 3    |
|   | TOTAL             | 12 – 29 | 13 – 34  |
```

Follow with:

- **Basis** — the anchors, cited. Which lines rest on evidence and which on judgement.
- **Known weaknesses** — and *which direction they point*. "These three all point toward the estimate being low" is worth more than a list.
- **Open questions** — decisions that block estimation, kept separate from decisions that block the work.

If the organisation already has a house estimate format, conform to it and put the Fibonacci discipline inside it. A correct estimate in a shape nobody recognises gets ignored.

## Anti-patterns

- **A rounded parent total with no raw sum.** Silently unanswerable scope questions.
- **Unanchored rungs.** Confidence dressed as arithmetic.
- **A silent factor.** Any adjustment the reader cannot see and cannot reverse.
- **Non-Fibonacci leaves.** A 6 or a 12 means someone declined to commit to a bucket.
- **Padding an unknown you could have measured.** Look it up first.
- **Shaving instead of cutting.** Full scope at three-quarters the hours is a promise you have not costed.
