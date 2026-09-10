# Parsimony

**Statement:** Write a big program only when it is clear by demonstration that nothing else will do.

*Scope: program scope and feature count — not internal structural complexity. See: Simplicity for that.*

## Rationale

The default should be to accomplish a goal with the smallest program that suffices. Every feature, every abstraction, every dependency adds to the surface area of things that can go wrong, require understanding, and need to change when requirements shift.

The critical phrase is *clear by demonstration*. Parsimony is not satisfied by reasoning that a big program will eventually be necessary — it requires evidence that the smaller approach has been tried and found wanting. Anticipating future scale or complexity is not sufficient justification.

## Corollaries

- Start with the minimal implementation. Extend it only when the minimal version demonstrably fails.
- A program that does less but does it reliably is more valuable than one that does more unreliably.
- The most parsimonious solution is often the one that solves the problem with existing tools rather than writing new ones. See: Economy.

## Guards Against

- Building general solutions to specific problems before the general case has been encountered.
- Programs whose scope grows without a corresponding growth in demonstrated need.
- Gold-plating: adding capability to individual features beyond what the specific requirement demands.
- The sunk-cost trap: continuing to invest in a large program because of prior investment.
