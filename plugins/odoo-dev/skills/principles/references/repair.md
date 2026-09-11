# Repair

**Statement:** Handle all errors. When you must fail, fail noisily and as soon as possible.

*Merged from: Unix Rule of Repair + TigerStyle error handling*

## Rationale

Silent failure is the most dangerous failure mode. A system that encounters an error and continues anyway — with corrupted state, partial results, or degraded behavior — propagates the error forward. By the time the corruption surfaces, the original cause may be impossible to identify.

This is not theoretical: analysis of production distributed systems failures found that almost all (92%) of catastrophic system failures result from incorrect handling of non-fatal errors. The errors were present; the systems chose not to act on them.

Failing fast localizes the error to its source. The earlier a failure is detected and reported, the smaller the distance between cause and symptom, and the easier the diagnosis.

"Noisily" means: with enough information to diagnose the cause. A complete error report answers what failed, where, what was expected, and what was observed.

## Corollaries

- All errors must be handled. Ignoring an error is a decision that must be made explicitly, not by omission.
- Validate at boundaries (inputs, external data) immediately and fail there rather than propagating bad data inward.
- An unhandled propagating error is better than one caught and silently swallowed.
- Assertions, precondition checks, and invariant validation belong in production code, not only in tests.
- "Best effort" behavior that silently degrades is appropriate only when the degraded output is clearly marked as degraded.

## Guards Against

- Corruption propagation: bad data that silently travels through a system before causing failure far from its source.
- Debugging sessions that must reconstruct the cause of failure from distant symptoms.
- Systems that appear to work but produce subtly wrong results.
- The 92% case: catastrophic failures from incorrectly handled non-fatal errors.
