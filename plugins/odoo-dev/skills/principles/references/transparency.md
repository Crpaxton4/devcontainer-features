# Rule of Transparency

**Statement:** Design for visibility to make inspection and debugging easier.

## Rationale

A system that cannot be observed cannot be understood, and a system that cannot be understood cannot be maintained or trusted. Transparency means that the internal state and behavior of a system are accessible to inspection without requiring special instrumentation or heroic debugging effort.

Transparency operates at multiple levels: the system should be transparent about what it is doing, what state it is in, and why it made the decisions it made. Opacity is a design choice that shifts cost from the builder to every future maintainer and user.

## Corollaries

- Data formats should be human-readable where possible. Binary formats close off inspection.
- Internal state should be dumpable or queryable without modifying the system.
- When a program makes a non-obvious decision, the reasoning should be visible (log, trace, or inspectable state) — not just the result.
- Logging and observability are not afterthoughts; they are part of the design.
- A system that behaves differently when observed versus unobserved has violated transparency.

## Guards Against

- Black-box systems where the only recourse when something goes wrong is to restart them.
- Debug cycles that require adding instrumentation before any diagnosis can begin.
- Decisions made by the system that operators cannot understand or verify.
