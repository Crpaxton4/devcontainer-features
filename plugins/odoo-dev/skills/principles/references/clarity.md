# Clarity

**Statement:** Clarity is better than cleverness.

## Rationale

Code is read far more often than it is written. A clever solution imposes a cognitive tax on every future reader, including the author. Clarity reduces this tax to near zero.

Clever code defers its cost: it appears to save time at the point of writing and spends it at the point of reading, debugging, or modifying — at an interest rate. Clear code pays upfront and compounds nothing.

Cleverness also conceals assumptions. When the assumptions turn out to be wrong, clever code fails in ways that are hard to diagnose because the failure path was never obvious.

## Corollaries

- Prefer the obvious algorithm unless measurement proves it inadequate.
- A comment that explains *what* the code does is a symptom — the code should be clear enough to speak for itself. A comment that explains *why* is valuable.
- If a clever solution is necessary, isolate and document it explicitly. Don't let its complexity leak outward.
- Names are a primary vehicle for clarity. See: Naming.

## Guards Against

- Code that only its author can modify safely.
- Bugs introduced during maintenance because the intent was obscured.
- Code that the author cannot explain to a colleague without running or demonstrating it.
