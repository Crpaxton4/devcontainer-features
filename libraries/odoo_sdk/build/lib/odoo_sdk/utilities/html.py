"""Pure Markdown/HTML conversion helpers.

These functions accept and return only primitives; they perform no Odoo I/O and
have no side effects, so they belong to the utilities (pure functional) layer.
"""

import io

from markdown_it import MarkdownIt
from markitdown import MarkItDown

_md_converter = MarkItDown()

# CommonMark renderer with GitHub-flavoured tables enabled. Reused across calls
# so the parser/plugin wiring is built once (mirrors the module-level
# ``_md_converter`` above).
_md_renderer = MarkdownIt("commonmark").enable("table")


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


def markdown_to_html(md: str) -> str:
    """Render a Markdown fragment to an HTML fragment.

    Odoo's ``mail.message.body`` is an HTML field, so Markdown posted verbatim
    renders as literal text with collapsed newlines. This is the write-path
    inverse of :func:`html_to_markdown`: it turns Markdown (headings, bold,
    bullet lists, tables, line breaks) into the HTML Odoo will display.

    :param md: Markdown source to render; an empty or whitespace-only string
        yields an empty string.
    :type md: str
    :return: HTML rendering of the Markdown with surrounding whitespace stripped.
    :rtype: str
    """
    if not md or not md.strip():
        return ""
    return _md_renderer.render(md).strip()
