# Layered Architecture

**Statement:** All code falls into one of three layers — Interface, Logic, or Helper — each with a distinct responsibility and strict constraints.

*Source: personal standard*

## The Three Layers

### Interface
The entry points through which external systems, callers, and other modules interact with this code. Interface functions are the public-facing boundary of a module or unit.

**Responsibilities:**
- Receive external input
- Validate and coerce input into the forms that Logic expects
- Delegate to exactly one Logic function
- Return the Logic function's result

**Constraints:**
- Strictly thin. No branching on business logic, no side effects of their own.
- Any branching on input (e.g., routing different input shapes to different Logic functions) lives in Logic, not here.
- A reader should be able to understand what an Interface function does in one glance: validate, then delegate.

### Logic
Where the work happens. Logic functions coordinate the execution of a feature or operation.

**Responsibilities:**
- Orchestrate the steps of an operation
- Own side effects (state changes, writes, external calls)
- Call Helper functions to compute or transform
- Call the Interface functions of other modules when crossing a module boundary

**Constraints:**
- Logic functions do not expose themselves as Interface. They are internal.
- They may call Helpers and other modules' Interface functions, but not other modules' Logic functions directly.
- Side effects are concentrated here, making them easy to find and reason about.

### Helper
Pure functions that transform data, or functions that cause exactly one specific side effect (a network call, a file read, a system call). Helpers are the lowest layer.

**Responsibilities:**
- Transform inputs into outputs without side effects (pure helpers), OR
- Perform exactly one side effect with no additional logic (effect helpers)

**Constraints:**
- Arguments must be primitives or standard library collections of primitives (scalars, lists, dicts, tuples, sets). No domain-specific objects.
- No branching that belongs to business logic — if a helper branches, it is doing too much.
- Helpers may call other Helpers. They do not call Logic or Interface functions.
- Pure helpers are the most reusable and the easiest to test: given the same inputs, they always return the same output.

## Call Flow

```
External caller
    → Interface (validate, delegate)
        → Logic (orchestrate, side effects)
            → Helper (transform / single side effect)
            → [other module's] Interface
```

## Why This Works

Concentrating side effects in Logic makes them locatable. When something has an unexpected effect, Logic is where to look. Interface functions being thin means callers can predict what they receive without understanding internals. Helpers being primitive-argument-only makes them trivially testable and reusable across Logic functions without creating coupling through shared domain types.

The constraint that Logic calls other modules' Interface (not Logic) functions maintains the boundary between modules: each module's Logic is an implementation detail; its Interface is the contract.

## Corollaries

- If a function has side effects and also does complex data transformation, it is doing two things and should be split: a Helper transforms, Logic applies.
- If an Interface function contains a switch statement routing to different Logic functions, that switch belongs inside one Logic function.
- If a Helper requires a domain object as an argument, either the Helper's boundary is wrong or the domain object should be decomposed into its primitive fields at the call site.
- The three-layer constraint pairs naturally with Function Length: Logic functions that orchestrate many steps should extract Helpers rather than inlining the transformations.

## Guards Against

- Side effects scattered through all layers, making them impossible to locate or reason about.
- Business logic embedded in the outermost layer, where it cannot be tested independently of the interface.
- Helpers that accumulate domain knowledge and become re-implementations of Logic.
- Cross-module coupling at the Logic layer — the mechanism by which the tangled-codebase failure described in Modularity manifests in layered systems. See: Modularity.
