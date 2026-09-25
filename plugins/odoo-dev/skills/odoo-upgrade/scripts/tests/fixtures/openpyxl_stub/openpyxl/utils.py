"""Column index to spreadsheet letter, same mapping as the real package."""

import string


def get_column_letter(index):
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = string.ascii_uppercase[remainder] + letters
    return letters
