"""Odoo-chatter adapter package (#718): resync capture over the ``RpcClient`` port.

One package per external system (ADR-005 amendment, #718). The external
system is Odoo itself, so unlike the sibling packages this one introduces no
new port: the chatter puller talks to Odoo exclusively through the injected
:class:`~odoo_sdk.commands.protocols.RpcClient` — the port that already
fronts every Odoo call — and reconciles the user's authored ``mail.message``
task chatter into the ``events`` table.

The implementation currently remains in
:mod:`odoo_sdk.adapters.external_sync` (see that module's layout note): the
frozen tests patch the shared ``_run_capture`` seam (used for the repo-label
lookup) on that module object, so physically relocating the code would
escape the patches. This façade is the canonical import path; the code
follows when the adapter tests are next allowed to move.
"""

from odoo_sdk.adapters.external_sync import sync_odoo_chatter

__all__ = ["sync_odoo_chatter"]
