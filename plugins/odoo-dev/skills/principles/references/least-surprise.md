# Rule of Least Surprise

**Statement:** In interface design, always do the least surprising thing.

## Rationale

Every interface creates expectations. Users form a mental model of how the interface behaves based on its name, its context, its documentation, and by analogy with other interfaces they know. When an interface violates these expectations, the user's mental model is wrong, and errors follow.

Surprise is not just an aesthetic problem — it is a defect. A function that returns `None` instead of raising on error, a flag that does the opposite of what its name implies, or an API that mutates its inputs when callers expect immutability: all of these create real bugs in real programs.

The principle applies at every scale: function names, argument order, return types, error handling, CLI flags, configuration keys, network protocol behavior.

## Corollaries

- Follow the conventions of the domain, language, and ecosystem. Deviation requires justification.
- If an interface surprises you as its author, it will surprise callers more.
- Names are part of the interface contract. A name that accurately describes behavior reduces surprise.
- Consistency within a system reduces surprise even when the convention is unconventional: users learn the local idiom.
- Surprising behavior that is documented is still surprising — documentation is not a substitute for expected behavior.

## Guards Against

- APIs that require callers to know internal implementation details to use correctly.
- Subtle bugs caused by mismatched expectations at function call sites.
- The maintenance cost of interfaces that require every caller to remember their quirks.
