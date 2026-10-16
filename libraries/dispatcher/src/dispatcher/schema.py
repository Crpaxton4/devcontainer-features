"""The run schema (design section 10) and the one success test.

The schema is passed to ``claude --json-schema``; the harness enforces it and
returns the validated object as ``structured_output`` in the result envelope.
The dispatcher's success test is never a status field (a real run returned
``subtype: "success"`` and ``is_error: true`` together). It is whether a usable
structured value arrived: :func:`usable_structured`.
"""

import json

REPORTED_OUTCOMES = ("done", "blocked", "partial")

RUN_SCHEMA: dict = {
    "type": "object",
    "required": [
        "summary",
        "outcome",
        "modules",
        "changes",
        "tests",
        "questions",
        "friction",
    ],
    "properties": {
        "summary": {"type": "string"},
        "outcome": {"enum": list(REPORTED_OUTCOMES)},
        "modules": {"type": "array", "items": {"type": "string"}},
        "changes": {"type": "array", "items": {"type": "string"}},
        "tests": {
            "type": "object",
            "required": ["ran", "passed", "failures"],
            "properties": {
                "ran": {"type": "integer"},
                "passed": {"type": "boolean"},
                "failures": {"type": "array", "items": {"type": "string"}},
            },
        },
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["question", "assumption", "why"],
                "properties": {
                    "question": {"type": "string"},
                    "assumption": {"type": "string"},
                    "why": {"type": "string"},
                },
            },
        },
        "friction": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["tool", "expected", "observed", "did"],
                "properties": {
                    "tool": {"type": "string"},
                    "expected": {"type": "string"},
                    "observed": {"type": "string"},
                    "did": {"type": "string"},
                },
            },
        },
    },
}


def schema_argument() -> str:
    """The schema as the single compact string ``--json-schema`` takes."""
    return json.dumps(RUN_SCHEMA, separators=(",", ":"))


def usable_structured(envelope: dict | None) -> dict | None:
    """Return the structured value when a usable one arrived, else None.

    Usable means a dict with a non-empty ``summary`` string (the description a
    run is billed with) and an ``outcome`` that is one of the reported values.
    Status fields of the envelope are deliberately ignored.
    """
    if not isinstance(envelope, dict):
        return None
    value = envelope.get("structured_output")
    if not isinstance(value, dict):
        return None
    summary = value.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return None
    if value.get("outcome") not in REPORTED_OUTCOMES:
        return None
    return value


def question_count(structured: dict) -> int:
    questions = structured.get("questions")
    return len(questions) if isinstance(questions, list) else 0
