"""Shared test helpers for opening an event store on a throwaway home.

:class:`~odoo_dev_data.events.EventStore` creates ``state.db`` under
``$ODOO_DEV_HOME`` on first connect, so a test needs only a fresh home directory.
"""

import tempfile
from pathlib import Path
from typing import Optional

from odoo_dev_data.events import EventStore


def make_event_store(home: Optional[Path] = None) -> EventStore:
    """Return an :class:`EventStore` on ``home``, or on a fresh temp home.

    With no ``home`` a new ``tempfile.mkdtemp()`` directory is used, so each
    call yields an empty, isolated store.
    """
    if home is None:
        home = Path(tempfile.mkdtemp())
    return EventStore(home=home)
