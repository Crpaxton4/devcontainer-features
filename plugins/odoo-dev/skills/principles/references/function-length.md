# Function Length

**Statement:** Functions must not exceed 25 logical lines of code (lloc), excluding comments.

*Sources: TigerStyle (generalized) + personal standard*

## Rationale

There is a sharp discontinuity between code that fits on a single screen and code that requires scrolling to read. When a function fits on screen, the programmer can hold the entire function in working memory simultaneously. When it requires scrolling, they cannot — they must navigate back and forth, assembling a mental model from fragments. See: Scope Minimization for the parallel constraint on live variables.

25 lloc is the concrete threshold. "Logical lines" (one statement = one lloc, regardless of physical line breaks) is the meaningful unit because it counts executable steps, not formatting choices. Comments are excluded because they explain intent, not execution.

## Corollaries

- 25 lloc is a hard limit, not a target. Treat reaching it as a signal to decompose, not a goal to approach.
- When a function approaches the limit, extract a helper at the natural join point between distinct responsibilities.
- The limit applies equally to all three layers. Logic functions that coordinate many steps should delegate to named helpers rather than inlining the steps.
- The natural shape of a well-structured function: few parameters going in, a simple return type coming out, the work done through named helpers that capture what each step is.

## Guards Against

- Bugs that escape code review because reviewers stop reading before reaching the defective section.
- Debugging that requires tracing state across dozens of statements in a single function.
- The "it's all related" justification: related logic should be decomposed into named helpers that make the relationship explicit, not inlined into one long function.
