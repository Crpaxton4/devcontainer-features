"""Deprecated shim: :mod:`odoo_sdk.reap` moved to :mod:`odoo_sdk.tracking.reap` (#717).

Importing this path hands back the relocated module itself (``sys.modules``
aliasing), so every existing import — and any test that patches attributes on
this path — keeps exactly its old behavior. Excluded from the ADR-005
import-linter contracts by design (a shim re-exports across layers on
purpose). Kept for at least two minor releases and never removed in the
release that introduced it (ADR-005 shim policy, #717).
"""

import sys
import warnings

from odoo_sdk.tracking import reap as _relocated

warnings.warn(
    "odoo_sdk.reap is deprecated; import odoo_sdk.tracking.reap instead "
    "(shim kept for at least two minor releases; #717).",
    DeprecationWarning,
    stacklevel=2,
)

sys.modules[__name__] = _relocated
