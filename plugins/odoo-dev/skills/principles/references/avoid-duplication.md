# Avoid Duplication

**Statement:** No duplicate state; no aliases that can desynchronize.

*Source: TigerStyle "Cache Invalidation"*

## Rationale

Every time the same piece of information exists in two places, there is a synchronization obligation. That obligation is easy to satisfy when both copies are updated atomically and in the same code path. It becomes a latent defect when the update paths diverge — when one copy is updated and the other is forgotten, or when the copies are updated at different times and can be observed in an inconsistent state.

The root cause of many hard-to-reproduce bugs is state that is duplicated: one copy becomes stale, and the code that reads the stale copy behaves incorrectly. The fix is never "update both copies more carefully" — it is to eliminate one copy.

This applies to aliases as well as full duplicates. An alias that provides a second name for the same mutable value creates the same synchronization risk: code that holds the alias may observe different state than code that holds the original if the original is mutated through a path the alias holder did not expect.

## Corollaries

- The canonical source of a piece of state should be a single location. Derived values should be computed from the canonical source rather than stored separately.
- If two variables must be kept in sync, ask whether they should be one variable.
- Computed/derived state that is cached for performance must have a clear invalidation mechanism. Without one, the cache becomes a source of stale state.
- Passing a copy of a value into a function is fine; passing a reference that the function might modify unexpectedly is an alias.
- Documentation that duplicates code (comments that describe what the code does) desynchronizes the same way: the code changes, the comment doesn't.

## Guards Against

- State desynchronization bugs: two copies of the same data that diverge and produce inconsistent behavior.
- Caches that serve stale data because they were never invalidated.
- Variables that must be kept in sync through careful discipline rather than by design.
