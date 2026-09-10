# Batching

**Statement:** Amortize expensive operations by grouping them.

*Source: TigerStyle*

## Rationale

Many operations have a fixed overhead that is paid regardless of the amount of work done: a network round-trip, a disk seek, a lock acquisition, a system call. When these operations are performed one unit of work at a time, the fixed overhead is paid once per unit. When they are batched, the fixed overhead is paid once for the entire batch, and the per-unit cost approaches the marginal cost.

This is the fundamental argument for batching: it converts per-unit fixed costs into per-batch fixed costs. The larger the batch, the more of the fixed cost is amortized across units.

Batching also improves predictability. A system that processes work in discrete batches has a clearer cost model than one that processes units on demand. This makes it easier to reason about throughput, latency, and resource utilization.

## Corollaries

- Identify the expensive operations in your system (network calls, disk I/O, database queries, inter-process communication) and batch them wherever the workload allows.
- The boundary between the control plane (decisions about what to do) and the data plane (doing the work) is a natural batching point: accumulate decisions, then execute them in bulk.
- Batching enables assertions about the amount of work done per unit time, which supports the bounded execution principle (see: Bounds).
- There is a trade-off between batch size and latency. Larger batches amortize more fixed cost but delay processing. The right batch size is a function of the specific fixed cost, the throughput requirements, and the acceptable latency.
- In request-driven systems, batching often means collecting multiple requests before issuing a single downstream call rather than issuing one downstream call per upstream request.

## Guards Against

- N+1 query patterns: issuing one expensive operation per item in a collection when a single batched operation would suffice.
- Systems whose throughput is dominated by per-operation fixed costs rather than the actual work per operation.
- Unpredictable latency caused by unbounded numbers of downstream calls per upstream request.
