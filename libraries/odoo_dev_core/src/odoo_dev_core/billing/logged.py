"""Core façade over the already-logged-hours read (#718).

The one core-owned door to
:func:`odoo_dev_core.services.logged_lines.logged_hours_by_task_day` (the
Odoo-reading data-layer helper behind the TUI review surface's
already-logged badge, #378 item 7). Under ADR-005 rule 4 a surface must
reach the data layer through core; the TUI driver imports the read from
here, retiring the last ``tui.app`` surface→data edge, mirroring the doors
:mod:`odoo_dev_core.attribution` (#717) and :mod:`odoo_dev_core.sessionization.persistence`
(#718) provide for their seams.
"""

from odoo_dev_core.services.logged_lines import logged_hours_by_task_day

__all__ = ["logged_hours_by_task_day"]
