# Rule of Extensibility

**Statement:** Design for the future, because it will be here sooner than you think.

## Rationale

Systems that are not designed to be extended are extended anyway — but badly. When extension points are absent, every new requirement forces either a redesign or a hack that works around the existing structure. The hacks accumulate, and each one makes the next extension harder.

Designing for extensibility does not mean building every feature that might ever be needed. It means leaving deliberate room for growth: well-defined extension points, stable internal interfaces, and designs that can absorb new requirements without structural change.

The tension with Parsimony is real: building extension mechanisms that are never used is waste. The resolution is to design for extensibility at the interface level (stable contracts, clean seams) while remaining parsimonious at the implementation level (build only what is needed now).

## Corollaries

- Interfaces should be versioned or designed to be backward-compatible by default.
- Internal implementation details that leak into interfaces become permanent constraints.
- Data formats should include a version field and reserve space for future fields.
- Plugin points, configuration hooks, and callback interfaces are explicit acknowledgments that users will have needs the designer did not anticipate.
- Protocol design is especially sensitive to this rule: protocols that cannot be extended without breaking existing participants have a fixed lifespan.

## Guards Against

- Systems that require a rewrite to accommodate any requirement outside the original design scope.
- Brittle integrations that break when either side evolves.
- Data formats or protocols that must be versioned globally because they contain no local extension mechanism.
