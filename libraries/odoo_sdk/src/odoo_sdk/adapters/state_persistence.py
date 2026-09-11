"""Deprecated shim: :mod:`odoo_sdk.adapters.state_persistence` moved to :mod:`odoo_sdk.adapters.state.persistence` (#718).

Part of the one-package-per-external-system adapter layout (ADR-005
amendment, #718): the sessionization/state persistence bridge now lives in
the ``adapters/state`` package behind the core-owned ``StateStore`` port.
Importing this path hands back the relocated module itself (``sys.modules``
aliasing), so every existing import — and any test that patches attributes
on this path — keeps exactly its old behavior. Excluded from the ADR-005
import-linter contracts by design (a shim re-exports across layers on
purpose). Kept for at least two minor releases and never removed in the
release that introduced it (ADR-005 shim policy, #717).
"""

import sys
import warnings

from odoo_sdk.adapters.state import persistence as _relocated

warnings.warn(
    "odoo_sdk.adapters.state_persistence is deprecated; import "
    "odoo_sdk.adapters.state.persistence instead (shim kept for at least two "
    "minor releases; #718).",
    DeprecationWarning,
    stacklevel=2,
)

sys.modules[__name__] = _relocated
