# Rule of Representation

**Statement:** Fold knowledge into data so program logic can be stupid and robust.

## Rationale

Data structures are more expressive and more maintainable than the code that processes them. A program whose behavior is determined by its data is easier to modify, extend, and reason about than one whose behavior is determined by complex branching logic.

When knowledge is encoded in data, changes in behavior require changing data — not code. Data is inherently inspectable. Logic is not. A lookup table, a configuration structure, or a declarative specification can be audited, modified, and versioned independently of the algorithm that interprets it.

"Stupid and robust" is precise: logic that merely traverses and applies data is too simple to have bugs in the interesting places. The complexity lives in the data, where it is visible.

## Corollaries

- A long chain of if/elif/else or a large match statement is often a sign that knowledge should be lifted into a data structure.
- Tables, maps, and declarative configurations are preferred over imperative control flow where the logic is rule-governed.
- When a behavior must change, prefer to change a data value over changing a code path.
- The data representation is often the most important design decision in a program; the algorithms follow from it.

## Guards Against

- Logic that must be modified to change behavior that is fundamentally data-driven.
- Systems where adding a new case requires touching multiple code paths.
- Complexity that is hidden inside control flow rather than made explicit in a data structure.
