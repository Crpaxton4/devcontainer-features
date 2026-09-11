# Simplicity

**Statement:** Design for simplicity; add complexity only where you must.

*Scope: internal structural complexity within a component — not program scope or feature count. See: Parsimony for those.*

*Merged from: Unix Rule of Simplicity + TigerStyle "On Simplicity and Elegance"*

## Rationale

Complexity is the primary source of defects, maintenance burden, and cognitive load. It compounds: a system with N interacting parts has O(N²) possible interactions, most of which the designer did not anticipate and cannot test.

Simplicity is not laziness — it is the result of discipline and hard work. A simple design emerges through multiple passes and revisions. "Only where you must" is a strong constraint: complexity is justified only when a simpler approach demonstrably fails to meet a real requirement, not a hypothetical one. The cost of deferring this discipline compounds throughout implementation, testing, and maintenance — see Economy for the cost curve.

## Corollaries

- The simplest solution that correctly handles all real requirements is the right solution.
- When two solutions solve the same problem, the simpler one is correct by definition unless proved otherwise.
- Simplicity requires multiple passes. Expect to revise. A first draft that is already simple is a coincidence.
- Refactoring toward simplicity is a first-class engineering activity, not cleanup.
- The cost of complexity is paid repeatedly by every developer who has to understand the system. Invest the upfront design cost instead.
- Robustness cannot be directly engineered — it emerges from simplicity (fewer failure modes) combined with transparency (visible failure modes). See: Transparency.

## Guards Against

- Accretion: systems that grow more complex over time without any single decision being obviously wrong.
- The compounding cost of each new developer having to understand an ever-larger system before making changes.
