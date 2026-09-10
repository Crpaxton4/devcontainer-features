"""Git adapter package (#718): the ``GitGateway`` port's data side.

One package per external system (ADR-005 amendment, #718). The external
system is the local ``git`` CLI: the puller recursively discovers every
checkout under the current directory and reconciles authored commits into
the ``events`` table. This package satisfies the core-owned
:class:`~odoo_sdk.commands.protocols.GitGateway` Protocol structurally
(PEP 544 module-implements-protocol).

The implementation currently remains in
:mod:`odoo_sdk.adapters.external_sync` (see that module's layout note): the
frozen tests patch the git/GitHub-shared subprocess seams on that module
object, so physically relocating the code would escape the patches. This
façade is the canonical import path; the code follows when the adapter
tests are next allowed to move.
"""

from odoo_sdk.adapters.external_sync import sync_git_log

__all__ = ["sync_git_log"]
