"""Local run/session tracking helpers — core-layer maintenance workflows.

The core half of the former ``utilities/`` package plus the two run
maintenance workflows that were orphaned at the package root
(``reap``/``prune``, ADR-005 classified them core from the start):

* ``env`` — the capability guard for the tracker commands.
* ``runs`` — run-table projection shared by the run-listing commands.
* ``stats`` — pure session statistics for the review surfaces.
* ``checkpoint`` — checkpoint-cadence hint from local task-note events.
* ``reap`` — bulk-abort of wedged stale runs (#366).
* ``prune`` — event-retention pruning with the un-uploaded-session guard (#363).

Nothing here talks to Odoo except through an injected client; the package
orchestrates data-layer objects (state, client) on behalf of the surfaces,
which is exactly ADR-005's definition of core. Relocated by #717; the old
``odoo_sdk.utilities.*`` / ``odoo_sdk.reap`` / ``odoo_sdk.prune`` paths remain
importable as deprecation shims for at least two minor releases.
"""
