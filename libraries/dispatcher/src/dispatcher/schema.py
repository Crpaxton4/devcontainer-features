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


_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
}


def _is_type(value, kind: str | None) -> bool:
    """True when ``value`` has JSON type ``kind`` (no type means anything goes)."""
    if kind == "integer":
        if isinstance(value, float):  # JSON Schema: 3.0 is an integer
            return value.is_integer()
        return isinstance(value, int) and not isinstance(value, bool)
    return kind not in _TYPES or isinstance(value, _TYPES[kind])


def _object_conforms(value: dict, schema: dict) -> bool:
    if any(key not in value for key in schema.get("required", ())):
        return False
    props = schema.get("properties", {})
    return all(conforms(value[key], props[key]) for key in props if key in value)


def conforms(value, schema: dict) -> bool:
    """Check ``value`` against the subset of JSON Schema that RUN_SCHEMA uses.

    The harness enforces the schema, but the dispatcher does not take a billing
    decision on trust: a value missing a required field is not a complete run.
    """
    if "enum" in schema:
        return value in schema["enum"]
    kind = schema.get("type")
    if not _is_type(value, kind):
        return False
    if kind == "array":
        return all(conforms(item, schema["items"]) for item in value)
    if kind == "object":
        return _object_conforms(value, schema)
    return True


def usable_structured(envelope: dict | None) -> dict | None:
    """Return the structured value when a usable one arrived, else None.

    Usable means it conforms to :data:`RUN_SCHEMA` (every required field
    present and well typed) and its ``summary`` is not blank, since the summary
    is the description a run is billed with. Status fields of the envelope are
    deliberately ignored.
    """
    if not isinstance(envelope, dict):
        return None
    value = envelope.get("structured_output")
    if not isinstance(value, dict):
        return None
    if not conforms(value, RUN_SCHEMA):
        return None
    # Deliberate departure from the schema: a blank summary is rejected. The
    # summary is the description a run is billed with, and a run with none is a
    # dead letter by design (section 10), even though rejecting a legitimate
    # result is the costlier direction of error. Chosen, not an oversight.
    if not value["summary"].strip():
        return None
    return value


def question_count(structured: dict) -> int:
    questions = structured.get("questions")
    return len(questions) if isinstance(questions, list) else 0
