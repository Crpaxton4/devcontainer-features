"""Compat alias: the tracker vocabulary moved to :mod:`odoo_sdk.tracking.models` (#718).

The FSM states, runs, events, session windows, and their error taxonomy were
promoted from the persistence layer to the core-owned vocabulary module
(ADR-005 amendment, #718). Importing this path hands back the relocated
module itself (``sys.modules`` aliasing), so every existing import — and any
test that patches attributes on this path — keeps exactly its old behavior,
and ``odoo_sdk.state``'s public re-exports are the identical objects.

Unlike the #717 ``utilities`` shims this alias does NOT warn: it is imported
eagerly by ``odoo_sdk.state.__init__`` (the supported public re-export path),
so a :class:`DeprecationWarning` here would fire on every ``import
odoo_sdk.state``. The path is a sanctioned alias, not a deprecated one.
Excluded from the ADR-005 import-linter contract expectations via a single
ignored edge (``state.models -> tracking.models``) documented in the ADR.
"""

import sys

from odoo_sdk.tracking import models as _relocated

sys.modules[__name__] = _relocated
