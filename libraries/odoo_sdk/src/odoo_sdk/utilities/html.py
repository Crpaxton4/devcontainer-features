"""Pure HTML-to-Markdown conversion helpers.

These functions accept and return only primitives; they perform no Odoo I/O and
have no side effects, so they belong to the utilities (pure functional) layer.
"""

import io

from markitdown import MarkItDown

_md_converter = MarkItDown()


def html_to_markdown(html: str) -> str:
    """Convert an HTML fragment to trimmed Markdown text.

    :param html: HTML source to convert; an empty string yields an empty string.
    :type html: str
    :return: Markdown rendering of the HTML with surrounding whitespace stripped.
    :rtype: str
    """
    if not html:
        return ""
    result = _md_converter.convert_stream(
        io.BytesIO(html.encode()), file_extension=".html"
    )
    return result.text_content.strip()
