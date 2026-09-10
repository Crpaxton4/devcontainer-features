# Optimization

**Statement:** The best wins come at design time. Prototype before polishing; measure before tuning.

*Merged from: Unix Rule of Optimization + TigerStyle performance timing*

## Rationale

There are two distinct optimization opportunities, with very different leverage:

**At design time**, choices about algorithms, data structures, protocols, and architecture can yield 10x–1000x improvements. These decisions are cheap to change before implementation begins and expensive or impossible to change afterward.

**After profiling**, targeted tuning of identified bottlenecks yields modest gains. This is valuable but bounded — and only correct when preceded by measurement.

Optimization before correctness produces optimized-but-wrong code. Optimization before profiling produces optimized-in-the-wrong-place code. Both add complexity without delivering the intended benefit.

## Corollaries

- The correct sequence: correct → measurable → measured → optimized.
- A working prototype reveals actual performance characteristics, which almost always differ from predicted ones.
- Profiling is mandatory before tuning. Guessing where the bottleneck is is almost always wrong.
- Algorithmic improvements (better asymptotic complexity) are almost always more impactful than constant-factor tuning.
- Micro-optimizations in non-bottleneck code have no measurable effect and cost clarity.
- Early back-of-envelope sketches across key resources (network, disk, memory, CPU) cost little and can rule out entire design classes before any code is written.

## Guards Against

- Complexity introduced to optimize code paths that are not performance bottlenecks.
- Correct-but-slow code never written because an optimized version was attempted first.
- Premature design decisions made in the name of future performance that constrain the actual solution.
- Missing the large design-time wins by jumping to implementation before the architecture is settled.
