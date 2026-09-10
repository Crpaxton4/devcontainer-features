# Bounds

**Statement:** Put a limit on everything: loops, queues, buffers, events.

*Source: TigerStyle (adapted from NASA's Power of Ten)*

## Rationale

Unbounded operations are a safety hazard. A loop or queue without a maximum can consume unbounded resources — time, memory, connection handles — under conditions the designer did not anticipate. In production, this manifests as runaway processes, tail-latency spikes, and cascading failures that appear unrelated to their cause.

Bounding everything forces an explicit decision: what is the maximum permissible size, count, or duration? If you cannot answer this question, you do not yet understand the system well enough to implement it safely. The act of setting a bound surfaces this gap.

Bounds also simplify reasoning. A loop that is guaranteed to terminate within N iterations can be analysed statically. A queue with a maximum depth has a known worst-case memory footprint. Bounded systems are predictable; unbounded systems are not.

## Corollaries

- Every loop must have a fixed upper bound that can be argued from requirements or design constraints.
- Every queue, buffer, and collection must have a maximum size. What happens when the maximum is reached must be explicitly decided (reject, block, evict, fail).
- Retry loops must have a maximum retry count and a backoff strategy.
- Timeouts are bounds on time. Every external call must have one.
- When a bound is exceeded, apply the Repair principle — see: Repair.
- The bound is not a magic number — it should be derived from the problem domain and documented.

## Guards Against

- Runaway processes caused by loops that don't terminate under unexpected input.
- Memory exhaustion from unbounded queues or collections filling under load.
- Tail-latency spikes caused by work that grows without limit during peak conditions.
- Systems that work correctly in testing (bounded input) but fail in production (unbounded input).
