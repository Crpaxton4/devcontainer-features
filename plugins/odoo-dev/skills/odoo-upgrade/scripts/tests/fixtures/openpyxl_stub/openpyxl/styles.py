"""Style objects are recorded, never interpreted: they carry no contract."""


class _Style:
    def __init__(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs


class Font(_Style):
    pass


class PatternFill(_Style):
    pass


class Alignment(_Style):
    pass
