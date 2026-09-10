"""GitHub adapter package (#718): the ``IssueTracker`` port's data side.

One package per external system (ADR-005 amendment, #718). The external
system is GitHub, reached through the authenticated ``gh`` CLI: account-wide
authored PRs (as billable ``pr_opened`` plus audit ``merge`` events),
reviews on own and others' PRs, and authored issue/PR comments. This
package satisfies the core-owned
:class:`~odoo_sdk.commands.protocols.IssueTracker` Protocol structurally
(PEP 544 module-implements-protocol).

The implementation currently remains in
:mod:`odoo_sdk.adapters.external_sync` (see that module's layout note): the
frozen tests patch the shared ``_run_capture``/``_gh_json`` seams on that
module object, so physically relocating the code would escape the patches.
This façade is the canonical import path; the code follows when the adapter
tests are next allowed to move.
"""

from odoo_sdk.adapters.external_sync import sync_github

__all__ = ["sync_github"]
