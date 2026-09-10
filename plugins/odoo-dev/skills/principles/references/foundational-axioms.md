# Foundational Axioms

## Unix Philosophy — McIlroy's Four Points

1. Make each program do one thing well. To do a new job, build afresh rather than complicate old programs.
2. Expect the output of every program to become the input to another, as yet unknown, program.
3. Design and build software to be tried early, ideally within weeks.
4. Use tools in preference to unskilled help to lighten a programming task.

**Summary:** Write programs that do one thing and do it well. Write programs to work together. Write programs to handle text streams.

## Unix Philosophy — Thompson's Maxim

When in doubt, use brute force.

**Implication:** Clarity and correctness of a straightforward solution outweigh elegance of a clever one. Clever solutions introduce hidden failure modes; brute force solutions are auditable.

## Unix Philosophy — Pike's Rules on Optimization

- Do not assume where bottlenecks occur — measure.
- Measure before optimizing; guessing leads to optimizing the wrong thing.
- Simple algorithms and data structures outperform fancy ones on small datasets.
- Simplicity aids correctness and maintainability.

**Implication:** The performance ceiling of a simple, correct solution is higher than it appears. Complexity introduced for performance must be justified by evidence, not intuition.

## TigerStyle — Design Priority Order

Safety, then Performance, then Developer Experience. Every design decision should be evaluated in this order. Safety is non-negotiable; performance trade-offs come second; developer convenience is last.

**Implication:** When a design choice improves developer experience at the cost of safety, it is the wrong choice. When it improves performance at the cost of safety, it is also the wrong choice.

## TigerStyle — On Style

Style is design. "The design is how it works." Style is necessary where understanding is missing — it is a means to understanding, not an end in itself.

An hour or day of design work prevents weeks or months of production problems. Complexity introduced at design time is cheap to remove; complexity discovered in production is expensive to fix.
