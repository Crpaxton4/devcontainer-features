"""In-memory stand-in for the slice of openpyxl build_workbook.py uses.

The workbook build is a data transform wearing a spreadsheet library: every
contract worth testing (which columns, which cells, which problem lines) is
decided before a single byte of xlsx is written. openpyxl is a third-party
package, it is not installed in the plugin's test environment, and installing
it would make the suite depend on a wheel to answer questions that have
nothing to do with xlsx. So the tests put this package first on PYTHONPATH and
run the real script end to end against it.

`save()` writes a JSON transcript of the sheets rather than a spreadsheet —
a test can read it, and a human debugging a failure can too. Styling is
recorded and never interpreted: nothing downstream asserts on a fill colour.
"""

import json
from collections import defaultdict


class _Cell:
    def __init__(self, value=""):
        self.value = value
        self.alignment = None
        self.font = None
        self.fill = None


class _Dimension:
    def __init__(self):
        self.width = None


class _AutoFilter:
    def __init__(self):
        self.ref = None


class Worksheet:
    def __init__(self, title):
        self.title = title
        self.rows = []
        self.column_dimensions = defaultdict(_Dimension)
        self.freeze_panes = None
        self.auto_filter = _AutoFilter()

    def append(self, values):
        self.rows.append([_Cell(v) for v in values])

    @property
    def max_row(self):
        return len(self.rows)

    @property
    def dimensions(self):
        return f"A1:A{max(len(self.rows), 1)}"

    def iter_rows(self, min_row=1):
        return iter(self.rows[min_row - 1:])

    def cell(self, row, column):
        return self.rows[row - 1][column - 1]

    def __getitem__(self, index):
        return self.rows[index - 1] if index <= len(self.rows) else []

    def values(self):
        return [[c.value for c in row] for row in self.rows]


class Workbook:
    def __init__(self):
        self._sheets = [Worksheet("Sheet")]

    @property
    def active(self):
        return self._sheets[0] if self._sheets else None

    def create_sheet(self, title):
        sheet = Worksheet(title)
        self._sheets.append(sheet)
        return sheet

    def remove(self, sheet):
        self._sheets.remove(sheet)

    def __iter__(self):
        return iter(self._sheets)

    def save(self, path):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({s.title: s.values() for s in self._sheets}, fh,
                      indent=1, default=str)
