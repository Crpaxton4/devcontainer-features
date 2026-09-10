# Explicit Options

**Statement:** Pass options explicitly at call sites; never rely on defaults you don't own.

*Source: TigerStyle*

## Rationale

Defaults are a form of hidden state. When a call site relies on a default, the behavior of that call is determined by a value defined elsewhere — possibly in a dependency you do not control, possibly changeable by a future update. The call site does not document the behavior it expects; it inherits whatever the dependency currently provides.

This creates a class of latent bugs: the dependency's default changes (or is found to have been wrong), and the call site silently inherits the new behavior. The bug may not surface immediately or obviously.

Explicit options make the call site self-documenting. A reader can understand what behavior is expected without tracing to the dependency's default definitions. Explicit options also make the intention testable: if the intended value is stated, it can be verified.

## Corollaries

- At call sites that accept options, name every option explicitly, even when the value matches the default.
- "The default is what I want" is not a reason to omit it — the default may change, and the omission cannot be distinguished from an oversight.
- Library defaults are the library author's choice, not yours. Accepting them implicitly is accepting someone else's design decisions without acknowledgment.
- Explicit options double as documentation: the call site tells the reader what behavior is expected and why each option was chosen.
- This principle applies to configuration as well as code: configuration values that are "inherited from defaults" should be made explicit in the config.

## Guards Against

- Latent bugs introduced when a dependency's default value changes.
- Call sites whose behavior cannot be understood without reading the library's default definitions.
- Subtle divergence between what the programmer intended and what the default provides.
