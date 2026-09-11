---
name: principles
description: "Engineering principles indexed as one-liners: safety and correctness, design and architecture, composition and interfaces, code structure, performance, visibility. Use when writing new code, reviewing a diff, or justifying a design choice."
user-invocable: false
---

## When to use

New code is about to be written, or a diff is about to be reviewed; two designs have to be chosen between; a function is growing past what one screen holds; an error path, a limit, a default, or a boundary is being decided; a name, an abbreviation, or a magic literal is in question; or someone asks why the code is shaped the way it is.

## How to Use

Scan index below to apply principles.
Pull relevant `references/` file(s) for rationale, corollaries, and guards. 
Foundational context is in `references/foundational-axioms.md`.

---

## Safety & Correctness

| Principle                 | One-liner                                                                                | Reference                             |
| ------------------------- | ---------------------------------------------------------------------------------------- | ------------------------------------- |
| **Safety Priority**       | Order: Safety → Performance → Developer Experience.                                      | `references/safety-priority.md`       |
| **Assertions**            | Assert preconditions, postconditions, and invariants; cover positive and negative space. | `references/assertions.md`            |
| **Bounds**                | Put a limit on everything: loops, queues, buffers, events.                               | `references/bounds.md`                |
| **Repair**                | Handle all errors. When you must fail, fail noisily and fast.                            | `references/repair.md`                |
| **Explicit Control Flow** | Execution paths must be visible and traceable; no implicit branching or dispatch.        | `references/explicit-control-flow.md` |

## Design & Architecture

| Principle             | One-liner                                                                                  | Reference                         |
| --------------------- | ------------------------------------------------------------------------------------------ | --------------------------------- |
| **Simplicity**        | Minimise internal structural complexity; add it only where demonstrated necessity demands. | `references/simplicity.md`        |
| **Parsimony**         | Minimise program scope and feature count; build only what demonstrated need requires.      | `references/parsimony.md`         |
| **Separation**        | Separate policy from mechanism; separate interfaces from engines.                          | `references/separation.md`        |
| **Representation**    | Fold knowledge into data so logic can be stupid and robust.                                | `references/representation.md`    |
| **Avoid Duplication** | No duplicate state; no aliases that can desynchronize.                                     | `references/avoid-duplication.md` |

## Composition & Interfaces

| Principle            | One-liner                                                                    | Reference                        |
| -------------------- | ---------------------------------------------------------------------------- | -------------------------------- |
| **Modularity**       | Simple parts, clean interfaces.                                              | `references/modularity.md`       |
| **Composition**      | Design to be connected with other programs.                                  | `references/composition.md`      |
| **Least Surprise**   | In interface design, always do the least surprising thing.                   | `references/least-surprise.md`   |
| **Extensibility**    | Design for the future, because it will be here sooner than you think.        | `references/extensibility.md`    |
| **Explicit Options** | Pass options explicitly at call sites; never rely on defaults you don't own. | `references/explicit-options.md` |

## Code Structure

| Principle                  | One-liner                                                                                                    | Reference                              |
| -------------------------- | ------------------------------------------------------------------------------------------------------------ | -------------------------------------- |
| **Layered Architecture**   | Interface (thin), Logic (side effects), Helper (pure / single effect, primitives only).                      | `references/layered-architecture.md`   |
| **Clarity**                | Clarity over cleverness.                                                                                     | `references/clarity.md`                |
| **Function Length**        | Hard limit: 25 logical lines of code, excluding comments.                                                    | `references/function-length.md`        |
| **Control Flow Structure** | Organise branching and iteration across the call hierarchy: ifs up, fors down.                               | `references/control-flow-structure.md` |
| **Scope Minimization**     | Declare variables at the smallest scope; check them close to where they're used.                             | `references/scope-minimization.md`     |
| **Naming**                 | Never abbreviate. Descriptive, unambiguous, pronounceable, searchable. No magic literals. No type encodings. | `references/naming.md`                 |

## Performance

| Principle               | One-liner                                                                         | Reference                           |
| ----------------------- | --------------------------------------------------------------------------------- | ----------------------------------- |
| **Economy**             | Programmer time is expensive; solve problems early; zero technical debt.          | `references/economy.md`             |
| **Optimization**        | Best wins come at design time. Prototype before polishing; measure before tuning. | `references/optimization.md`        |
| **Mechanical Sympathy** | Design with awareness of the actual resource characteristics of your system.      | `references/mechanical-sympathy.md` |
| **Batching**            | Amortize expensive operations by grouping them.                                   | `references/batching.md`            |

## Visibility & Process

| Principle        | One-liner                                                            | Reference                    |
| ---------------- | -------------------------------------------------------------------- | ---------------------------- |
| **Transparency** | Design for visibility to make inspection and debugging easier.       | `references/transparency.md` |
| **Silence**      | When a program has nothing surprising to say, it should say nothing. | `references/silence.md`      |
| **Generation**   | Write programs to write programs; avoid hand-hacking repetition.     | `references/generation.md`   |
| **Diversity**    | Distrust all claims for "one true way."                              | `references/diversity.md`    |
| **PR Etiquette** | Small stacked PRs (<500 best, <1000 max), plain conventional titles, task-linked minimal descriptions, draft by default. | `references/pr-etiquette.md` |
