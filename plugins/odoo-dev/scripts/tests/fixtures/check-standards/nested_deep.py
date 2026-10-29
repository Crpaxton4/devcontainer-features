"""A loop inside a loop inside a loop."""


def walk(cube):
    """Visit every cell."""
    for plane in cube:
        for row in plane:
            for cell in row:
                print(cell)
