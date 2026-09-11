# Assertions

**Statement:** Assert preconditions, postconditions, and invariants throughout; cover both positive and negative space.

*Source: TigerStyle*

## Rationale

Assertions distinct from error handling. 

Error handling manages expected conditions:
- external inputs
- network failures
- missing files

Assertions detect programmer errors:
- Impossible states given invariants.
- When an assertion fires, the appropriate response is not recovery, it is investigation

High assertion density complements testing. When invariants are asserted throughout functional code, a test that exercises a code path implicitly verifies all assertions on that path. 
Assertions also function as executable documentation: 
- they encode what must be true
- makes implicit assumptions explicit and machine-checked.

## Corollaries

- Assert at function entry (preconditions), at function exit (postconditions), and at key internal checkpoints (invariants).
- **Positive space**: assert what must be true ("this value is in range").
- **Negative space**: assert what must never be true ("this value is not null"). Both are necessary; positive alone misses edge cases.
- **Pairing**: find at least two code paths where each invariant can be asserted — e.g., assert validity before persisting, then assert again after retrieving. Paired assertions catch corruption that single assertions miss.
- Split compound assertions into separate statements. `assert(a); assert(b)` is better than `assert(a and b)` because the failure message identifies which condition failed.
- Assert the relationships of constants and configuration values, not just runtime state.
- Assertions are not a substitute for understanding. Build a precise mental model first; encode it in assertions; then use testing as the final defence.

## Guards Against

- Implicit assumptions that are never checked and silently violated.
- Test suites that pass while the code's invariants are wrong.
- The "it can't happen" failure: states that are believed impossible but never verified.
