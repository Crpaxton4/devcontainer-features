# Control Flow Structure

**Statement:** Push branching up to parents; push iteration down to helpers.

*Scope: how branching and iteration are distributed across the call hierarchy within a module. For visibility and traceability of execution paths, see: Explicit Control Flow.*

*Source: TigerStyle — "Push ifs up and fors down"*

## Rationale

Mixing branching logic and iteration logic in the same function produces functions that are hard to read, hard to test, and hard to modify. Each concern obscures the other.

When a parent function owns the branching (the "what do we do"), it can be read as a clear decision tree. When child functions own the iteration (the "how do we do it"), they can be pure, single-purpose workers with no knowledge of which branch called them. Independent testing of each unit follows naturally from this separation — see: Modularity for the general principle.

## Corollaries

- A function that contains both switch/if statements and iteration is a candidate for decomposition.
- **Parent functions**: own the control flow decisions. Call helpers to compute, not to branch.
- **Helper (leaf) functions**: stay pure where possible — they receive what they need, do their work, and return a result. They do not need to know which branch called them.
- This pattern keeps helpers reusable: a pure function that computes a value is callable from any branch that needs that value.
- State mutation belongs in the parent; helpers should return new values rather than modifying shared state where possible.

## Guards Against

- Functions whose branching and iteration logic are interleaved, making each harder to test in isolation.
- Helpers that contain their own branching, making them context-dependent and difficult to reuse.
- Testing that requires complex setup because the branching logic and the iteration logic cannot be exercised separately.
