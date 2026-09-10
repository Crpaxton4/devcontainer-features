"""Odoo-facing service helpers — the data-layer half of the former ``utilities/``.

Every module here wraps calls against a live Odoo (each takes an
:class:`~odoo_sdk.client.OdooClient` and issues one well-defined operation),
which is what makes this a *data* package under ADR-005: it talks to an
external system. Command bodies (core) compose these helpers so business
logic reads at one altitude; pure primitive-only helpers live in the shared
kernel (``_utils``) or beside their sole consumer instead.

Split out of ``utilities/`` by #717; the old ``odoo_sdk.utilities.*`` module
paths remain importable as deprecation shims for at least two minor releases.
"""
