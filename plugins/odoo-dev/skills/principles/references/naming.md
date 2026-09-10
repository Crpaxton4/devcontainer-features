# Naming

**Statement:** Never abbreviate. Choose names that are descriptive, unambiguous, pronounceable, and searchable. Replace magic literals with named constants. Never encode type information into names.

*Sources: TigerStyle + personal standard*

## Rationale

Names are the primary interface between code and the programmer reading it. Modern tooling (autocomplete, search, navigation) eliminates the cost of long names; the cost now lives entirely in the reader's side — in guessing what a symbol is, decoding what a short name means, and disambiguating between names that are similar but not identical.

Naming is also a domain modelling exercise. Getting the nouns and verbs exactly right demonstrates understanding of the problem. When names are wrong or vague, it often signals that the abstraction is wrong — the thing being named does not have a clear identity because it does not have a clear purpose.

## The Six Rules

**1. Choose descriptive and unambiguous names.**
A name should say precisely what the thing is or does, with no room for misinterpretation. If you find yourself writing a comment to clarify what a name means, the name is wrong. Rename until the comment is unnecessary.

**2. Make meaningful distinctions.**
Don't use names that differ only superficially (`data` vs `info`, `get` vs `fetch`, `Manager` vs `Handler`). If two things have different purposes, their names should communicate that difference unambiguously. Names that look similar but mean different things cause the reader to stop and verify which is which.

**3. Use pronounceable names.**
If you cannot say the name out loud in a code review, in a conversation, or in a comment, it is not a good name. Unpronounceability is a symptom of abbreviation or encoding. Names that are speakable are also easier to remember and search for.

**4. Use searchable names.**
Single-character names, very short names, and names that are common English words are unsearchable — a search for `e` or `data` returns noise. Prefer names specific enough that searching for them returns only relevant results. This is especially important for constants: a magic number `86400` is unsearchable; `seconds_per_day` is not.

**5. Replace magic literals with named constants.**
Any literal value (number, string, boolean) used for a specific purpose must be extracted to a named constant in the appropriate scope. The name encodes the meaning; the constant encodes the value. When the value must change, it changes in one place.

**6. Avoid encodings — do not append type information to names.**
Hungarian notation and type-in-name patterns (`user_list`, `is_flag`, `str_name`, `count_int`) are redundant noise. The type is either evident from the value, available in the signature annotation, or inferrable from context. Encoding it in the name creates a maintenance obligation: when the type changes, every name must change too. Type annotations in function signatures are appropriate and encouraged; type information baked into identifiers is not.

## Additional Naming Guidance

- **Units and qualifiers**: include them in the name, placed last, sorted by descending significance: `timeout_ms_max` rather than `max_timeout`. This groups related names naturally and prevents unit confusion.
- **No overloading**: avoid using the same name to mean different things in different contexts. Names with unique referents are unambiguous by construction.
- **Nouns over adjectives** for things that will be referred to repeatedly. Nouns compose naturally; adjectives require rephrasing when used in different grammatical contexts.
- **Never abbreviate**, including well-known abbreviations you assume everyone knows. The only exception is when the abbreviation is the canonical name in the domain and the full expansion is never used (`id`, `url`, `cpu`, `http`).

## Guards Against

- Bugs from unit confusion (milliseconds treated as seconds, bytes as kilobytes).
- Wasted time decoding abbreviated or encoded names.
- Ambiguity from names that look similar but mean different things.
- Magic literals scattered through code whose meaning is only clear to the original author.
- Type-encoding that becomes a maintenance liability when types change.
