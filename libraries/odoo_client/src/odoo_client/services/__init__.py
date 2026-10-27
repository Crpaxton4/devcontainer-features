"""Odoo-facing service helpers — thin single-call Odoo wrappers that commands compose.

Every module here wraps calls against a live Odoo (each takes an
:class:`~odoo_client.client.OdooClient` and issues one well-defined operation),
which is what makes this a *data* package under ADR-005: it talks to an
external system. Command bodies (core) compose these helpers so business
logic reads at one altitude; pure primitive-only helpers live in the shared
kernel (``_utils``) or beside their sole consumer instead.
"""
