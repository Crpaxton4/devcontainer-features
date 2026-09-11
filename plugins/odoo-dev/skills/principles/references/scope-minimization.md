# Scope Minimization

**Statement:** Declare variables at the smallest possible scope; check and use them close together.

*Source: TigerStyle*

## Rationale

Like Function Length — which limits how much code a programmer must hold in memory — this principle addresses cognitive load from live variables: more simultaneously in scope means more potential for error. The specific failure class this guards against is POCPOU (place-of-check to place-of-use) bugs: a value is checked or computed at one point, state changes between that point and the point of use, and the check no longer accurately describes the state at the point of use. Minimising the distance eliminates the window for state change to invalidate the check.

## Corollaries

- Declare variables immediately before they are needed, not at the top of a function.
- Variables should go out of scope as soon as their purpose is fulfilled. Avoid keeping them alive past their use.
- Compute derived values immediately before they are consumed, not earlier. The longer a derived value sits unused, the greater the risk the inputs it was derived from have changed.
- The fewer variables simultaneously in scope, the easier the function is to reason about — there are fewer things that could be wrong.
- A function that requires many long-lived variables is a candidate for decomposition. See: Function Length.
- Reduce the dimensionality of function signatures: simpler return types (returning a value rather than a flag-plus-output-parameter) reduce the state the caller must track.

## Guards Against

- POCPOU bugs: state that changes between the check and the use.
- Misuse of still-in-scope variables that are no longer relevant to the current logic.
- Long-lived variables that make functions hard to read because the reader must track their state throughout.
