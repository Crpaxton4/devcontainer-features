# Economy

**Statement:** Programmer time is expensive; solve problems early; carry zero technical debt.

*Merged from: Unix Rule of Economy + TigerStyle "Technical Debt" and design-upfront*

## Rationale

Machine cycles are cheap; skilled programmer time is scarce and expensive. Solutions that trade machine resources for developer clarity, maintainability, or velocity are usually correct trades.

Technical debt compounds this: a problem solved during design costs one unit of effort. The same problem solved during implementation costs more. Discovered in testing, more still. Discovered in production, many times more. Zero technical debt is not perfectionism — it is the economically rational policy. For performance specifically, see Optimization.

## Corollaries

- The readable solution is the economical default. Its long-term comprehension and maintenance cost is lower than any alternative that trades clarity for an optimisation that may not be necessary. See: Optimization.
- Solve problems immediately when discovered; deferring makes them exponentially more expensive.
- The total cost of a feature includes all future modifications, bug fixes, and comprehension time, not just initial implementation.
- Boilerplate repeated across a codebase is a programmer-time tax that compounds with every new instance.
- Code that is easy to delete and replace is more economical than code entangled with everything around it.
- Reusing an existing tool or abstraction is usually the economical choice. See: Parsimony.

## Guards Against

- Deferring known problems to "later" where they become emergencies.
- Bespoke implementations of functionality that existing tools provide.
- Accumulation of small shortcuts that each seem reasonable but collectively make the system hard to change.
