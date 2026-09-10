# Safety Priority

**Statement:** Order priorities: Safety, then Performance, then Developer Experience.

*Source: TigerStyle*

## Rationale

When design trade-offs arise, having an explicit priority order resolves ambiguity without committee deliberation. The order reflects the asymmetry of consequences: safety failures can be catastrophic and irreversible; performance failures are recoverable; developer experience trade-offs are the least severe.

This ordering does not mean performance and developer experience are unimportant — it means they are pursued within the constraint that safety is not compromised. A fast but unsafe system is not a better system; it is a more dangerous one.

The ordering also guides what to invest in first. Safety invariants must be established before performance can be measured. A system that is not correct has no meaningful performance.

## Corollaries

- A design that improves developer experience at the cost of correctness is the wrong design.
- A design that improves performance at the cost of safety is also the wrong design.
- When safety and performance conflict, safety wins. Document the trade-off explicitly.
- Optimisations that obscure correctness properties (making assertions harder to write, making state harder to inspect) pay a hidden safety cost.
- "It's safe enough" is not a stopping point for safety analysis; it is where performance and DX analysis begins.

## Guards Against

- Performance optimisations that introduce correctness risks without explicit acknowledgment.
- Designs that sacrifice auditability for ergonomics, making behaviour impossible to verify without tracing through implementation details.
- Letting the urgency of performance goals override the discipline of safety constraints.
