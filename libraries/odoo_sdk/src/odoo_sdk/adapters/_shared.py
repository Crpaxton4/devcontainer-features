"""Cross-system helpers shared by the external-sync adapter packages (#718).

Extracted from :mod:`odoo_sdk.adapters.external_sync` so the Google adapter
package (:mod:`odoo_sdk.adapters.google.sync`) can use the task-id extraction
and ISO parsing without importing the git/GitHub/Odoo puller module (which
would be a cycle: ``external_sync`` re-exports the Google names for its
frozen-test compatibility surface). ``external_sync`` re-imports every name
here into its own globals, so all historical ``external_sync._extract_task_ids``
/ ``external_sync._parse_iso_utc`` reads keep working.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

# Minimum task-id magnitude. Real Odoo task ids are 4-5 digits; requiring at
# least this many digits kills false positives where a short client-side number
# (``#31 - Hardcode…``) or a PR cross-reference (``(#189)``) minted a phantom
# task lane (issue #378 item 1).
_MIN_TASK_ID_DIGITS = 4

# Task-id extractors applied to a commit/PR subject and its branch/ref context.
# Documented, ordered forms (all require >= ``_MIN_TASK_ID_DIGITS`` digits):
#   ``#<id>``          GitHub-style reference
#   ``odoo-<id>``      branch convention (case-insensitive)
#   ``[<id>]``         bracketed
#   ``<id>-slug``      branch-prefix convention used on client branches (#622:
#                      id BEFORE the ``-``); anchored to a token start so a
#                      digit run buried mid-token never reads as an id
#   ``task <id>``      PR-title form ``(task NNNNN)`` (optional space/hyphen)
#   ``(<id>)``         trailing ``(NNNNN)`` in a PR title (NOT ``(#NNNNN)``)
#   ``<id> title``     bare leading id at subject START (space/#/:/- after the
#                      digits, issue #654), GATED behind ``allow_leading_id``
#                      (git/GitHub call sites only). ``^`` is safe: the scanned
#                      text is ``f"{subject} {branch}"``, so a branch never
#                      sits at string start, and the join space delimits a
#                      bare-only title; a leading ``NNNNN-`` subject also
#                      matches the ``<id>-slug`` form — dedupe absorbs it
_TASK_ID_PATTERNS = (
    re.compile(rf"#(\d{{{_MIN_TASK_ID_DIGITS},}})"),
    re.compile(rf"odoo-(\d{{{_MIN_TASK_ID_DIGITS},}})", re.IGNORECASE),
    re.compile(rf"\[(\d{{{_MIN_TASK_ID_DIGITS},}})\]"),
    re.compile(rf"(?:^|[\s,/])(\d{{{_MIN_TASK_ID_DIGITS},}})-"),
    re.compile(rf"\btask[ -]?(\d{{{_MIN_TASK_ID_DIGITS},}})\b", re.IGNORECASE),
    re.compile(rf"\((\d{{{_MIN_TASK_ID_DIGITS},}})\)"),
)

# The gated ``<id> title`` form above. Kept OUT of ``_TASK_ID_PATTERNS`` so the
# calendar/gmail call sites (explicit-marker contract) can never grow it by
# accident; :func:`_extract_task_ids` appends it LAST when ``allow_leading_id``
# is set, preserving first-seen dedupe precedence.
_LEADING_TASK_ID_PATTERN = re.compile(rf"^(\d{{{_MIN_TASK_ID_DIGITS},}})[\s#:-]")


def _extract_task_ids(
    subject: str, branch: str, *, allow_leading_id: bool = False
) -> list[str]:
    """Return the distinct task ids in ``"{subject} {branch}"``, first-seen order.

    Every form requires at least :data:`_MIN_TASK_ID_DIGITS` digits, so a short
    client-side number or a PR cross-reference is never mistaken for a task id.
    ``allow_leading_id`` additionally admits the bare-leading-id form
    (:data:`_LEADING_TASK_ID_PATTERN`, issue #654); only the git/GitHub call
    sites pass True — calendar/gmail attribution stays explicit-marker only.
    """
    text = f"{subject} {branch}"
    patterns = _TASK_ID_PATTERNS
    if allow_leading_id:
        patterns += (_LEADING_TASK_ID_PATTERN,)
    ids: list[str] = []
    for pattern in patterns:
        for match in pattern.findall(text):
            if match not in ids:
                ids.append(match)
    return ids


def _parse_iso_utc(value: str) -> datetime:
    """Parse an offset-aware ISO-8601 timestamp (git ``%aI`` / GitHub) as UTC."""
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
