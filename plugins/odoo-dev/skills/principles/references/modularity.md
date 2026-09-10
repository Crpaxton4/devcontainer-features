# Rule of Modularity

**Statement:** Write simple parts connected by clean interfaces.

## Rationale

Bugs live at interfaces as much as in implementations. When interfaces are clean and contracts are explicit, defects become localized — they cannot silently spread across module boundaries. Simple parts are independently testable and replaceable.

The emphasis is equally on *simple parts* and *clean interfaces*. A clean interface between complex parts provides weaker guarantees than a clean interface between simple ones: complex parts have more internal state, more edge cases, more coupling to conceal.

## Corollaries

- A module that can be understood in isolation is a module that can be trusted in composition.
- The interface is a contract. Anything not in the contract is an implementation detail that can change without notice.
- Modules that expose their internals through their interface are not modular — they are just partitioned.
- Complexity that cannot fit inside a single module belongs at the interface, made explicit.

## Guards Against

- Tangled codebases where changes in one area break unrelated functionality.
- Debugging sessions that require holding the entire system in working memory.
- Systems where no part can be replaced or upgraded independently.
