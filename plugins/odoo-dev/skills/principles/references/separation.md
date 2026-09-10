# Rule of Separation

**Statement:** Separate policy from mechanism; separate interfaces from engines.

## Rationale

A *mechanism* is how something is done. A *policy* is what should be done and under what conditions. Conflating them creates systems where neither can change independently — altering the policy requires modifying the engine, and the engine cannot be reused under a different policy.

When policy and mechanism are separated, the same engine can serve multiple policies without modification. Policies can be changed, overridden, or configured without touching the engine's implementation.

This is why Unix separates the kernel (mechanism) from shell scripts (policy), and why well-designed libraries expose an API (mechanism) while leaving decisions about when and how to call it to the caller (policy).

## Corollaries

- Configuration, flags, and parameters are policy surfaces. Keep them at the boundary; don't bury them inside the engine.
- An engine that enforces policy is harder to test: tests must satisfy the policy to reach the mechanism.
- Policy embedded in a library forces every caller to share the same decisions.
- The interface *is* the separation point. What the interface exposes and hides determines how cleanly policy and mechanism are divided.

## Guards Against

- Engines that cannot be reused because policy is hardcoded inside them.
- Systems where changing a business rule requires modifying core infrastructure.
- Inability to test mechanism independently of the conditions that trigger it.
