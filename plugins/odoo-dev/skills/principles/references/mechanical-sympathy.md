# Mechanical Sympathy

**Statement:** Design with awareness of the actual resource characteristics of your system.

*Source: TigerStyle*

## Rationale

Every system runs on real hardware with real resource constraints: network latency, disk bandwidth, memory bandwidth, CPU throughput. These resources have different speeds, different cost profiles, and different access patterns that affect performance by orders of magnitude. Code that ignores this reality treats the system as an abstract machine and is surprised when performance differs from expectation.

Mechanical sympathy means working with the grain of the system: understanding which resources are slow and which are fast, where the bottlenecks actually live, and designing data flows and access patterns to align with how the underlying hardware and operating environment actually work.

The name comes from motorsport: a driver with mechanical sympathy understands the machine well enough to work with it rather than against it.

## Corollaries

- Before optimising, understand the resource landscape: network bandwidth and latency, disk read/write speeds, memory bandwidth, CPU throughput. Back-of-envelope sketches with real numbers reveal what is possible.
- Optimise for the slowest resource first. Improving throughput on a fast resource when the bottleneck is a slow one produces no observable gain.
- Access patterns matter as much as raw throughput. Sequential access is faster than random access for most storage media. Grouping accesses reduces per-unit fixed cost — see: Batching.
- The frequency of access to each resource must be factored in: a slow resource accessed rarely may be less of a bottleneck than a fast resource accessed in a tight loop.
- "Roughly right" is the goal: landing within 90% of the theoretical maximum through good design is more achievable and more valuable than chasing the last percentage through micro-optimisation.

## Guards Against

- Designs that are algorithmically correct but structurally mismatched with the hardware they run on.
- Optimising CPU-bound code when the bottleneck is I/O, or vice versa.
- Surprises in production when the system behaves differently than expected under real load with real resource constraints.
