"""A clean module file: one model, docstrings, allowed nesting."""


class Clean(object):
    """The one model of this file."""

    _name = "x.clean"

    def _compute_total(self, rows):
        """Sum the rows; for-in-for, for-in-if and an elif chain are allowed."""
        total = 0
        for row in rows:
            for cell in row:
                total += cell
            if total > 100:
                total = 100
            elif total < 0:
                total = 0
        return total
