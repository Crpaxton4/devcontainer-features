# Explicit Control Flow

**Statement:** Execution paths must be visible and traceable; avoid implicit branching or dispatch.

*Scope: visibility and traceability of execution paths — whether the reader can follow where control goes. For how to organise branching and iteration across functions, see: Control Flow Structure.*

*Source: TigerStyle*

## Rationale

Control flow that is hard to trace is control flow that is hard to reason about. When execution can jump to distant locations implicitly — through callbacks buried in framework internals, through exceptions that skip intermediate frames, through event-driven state machines with no clear flow — the programmer cannot hold the execution path in mind. Bugs hide in the gaps.

Simple, explicit control flow means reading the code top-to-bottom gives a reliable picture of the order of operations. The programmer should be able to answer "what happens if X?" by reading the code, not by running a debugger.

## Corollaries

- Prefer explicit conditionals over language features that hide branching (magic dispatch, implicit coercions, error-as-control-flow).
- Deeply nested conditionals are a sign that the control flow has not been organised into its natural structure. See: Control Flow Structure for how to reorganise it.
- State machines should be explicit and inspectable, not implicit in scattered flag variables.
- Avoid recursion unless the problem is naturally recursive and the depth is provably bounded. Iteration is easier to reason about and bound.
- When a function reacts to external events, it should process them at a controlled point rather than being interrupted mid-execution.
- Compound conditions (`if a and b and c`) should be split into named intermediate assertions or sequential checks so each step is visible.

## Guards Against

- Bugs that only appear under specific orderings of events that are impossible to trace from reading the code.
- Control flow that depends on implicit framework behaviour the developer does not control.
- Debugging sessions where the only tool that works is a step-through debugger because execution paths cannot be read statically.
