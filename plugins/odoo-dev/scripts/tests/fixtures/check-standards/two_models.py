"""Two models in one file."""


class First(object):
    """First model."""

    _name = "x.first"


class Second(object):
    """Second model."""

    _inherit = "x.other"
