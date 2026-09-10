# Generation

**Statement:** Avoid hand-hacking; write programs to write programs when you can.

## Rationale

When the same code pattern must exist in multiple places, each copy is a potential divergence point — see: Avoid Duplication for the general principle. Generation eliminates this risk for code patterns specifically by making the generator — not any individual instance — the canonical form. When the pattern changes, only the generator changes; all instances update together.

Generation also documents the intent: the generator encodes what the generated output *should* be, which is often more informative than inspecting any individual instance.

The threshold for generation is lower than it seems. If a pattern appears three or more times and requires coordinated changes when it changes, a generator is probably warranted.

## Corollaries

- Generated code should be clearly marked as generated and not manually modified.
- The generator is the source of truth; the generated artefacts are derived.
- Schemas, grammars, and specifications are generators: they define a space of valid outputs rather than a single instance.
- Template expansion, metaprogramming, and macro systems are all applications of this rule at different levels.

## Guards Against

- Pattern drift: copies of generated code that have diverged through manual edits and no longer match the canonical form.
- Copy-paste programming where each copy is slightly different and the differences are not intentional.
