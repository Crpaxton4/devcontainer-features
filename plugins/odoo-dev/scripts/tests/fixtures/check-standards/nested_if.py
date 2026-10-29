"""An if inside an if."""


def pick(a, b):
    """Return a or b."""
    if a:
        if b:
            return 1
    return 0
