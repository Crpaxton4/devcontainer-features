"""Local session tracking helpers — core-layer workflows.

* ``stats`` — pure session statistics for the review surfaces.
* ``events`` — the core door to the raw-event read path.
* ``models`` — the promoted tracker vocabulary (events, windows).

Nothing here talks to Odoo except through an injected client; the package
orchestrates data-layer objects (state, client) on behalf of the surfaces,
which is exactly ADR-005's definition of core.
"""
