# Composition

**Statement:** Design programs to be connected with other programs.

## Rationale

No program author can anticipate all future uses. A program designed for composition lets users combine it in ways never imagined at design time, multiplying its utility without any additional development effort.

The canonical mechanism in Unix is text streams over pipes: a universal interface that any program can produce or consume without needing to know what's on the other side. The power is not in any individual program but in the combinatorial space of possible pipelines.

Composability requires discipline: programs must behave predictably in isolation so that behaviour in composition is predictable. A program that has side effects, unpredictable output, or context-dependent behaviour resists composition.

## Corollaries

- Functions, modules, and services are also composable units — pipes are the model, not the only instance.
- A program (or function) that reads from a clear input and writes to a clear output is inherently composable.
- Global state, implicit context, and hidden side effects are the enemies of composition.
- Design outputs to be machine-readable first; human-readable formatting is a presentation layer.

## Guards Against

- Outputs formatted or structured for a specific consumer, making them unusable in any other context without transformation.
- Tools that require bespoke integration work to use alongside anything else.
- Coupling through shared mutable state or ambient context.
