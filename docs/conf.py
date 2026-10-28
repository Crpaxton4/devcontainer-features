# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import os

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from sphinx.util import logging as sphinx_logging

# -- Paths -------------------------------------------------------------------

_DOCS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_DOCS_DIR)

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

with open(
    os.path.join(_REPO_ROOT, "libraries", "odoo_client", "pyproject.toml"), "rb"
) as _pyproject:
    release = tomllib.load(_pyproject)["project"]["version"]

project = "odoo-dev suite"
copyright = "2026, Chris Paxton"
author = "Chris Paxton"

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx_autodoc_typehints",
    "myst_parser",
]

exclude_patterns = [
    "_build",
    "design/devcontainer-task-orchestrator.html",
]

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

autosummary_generate = True

# Two warning categories are structural, not authoring mistakes, so they are
# suppressed to keep -W achievable. Any other warning type still fails the build.
#   * ref.python: the packages re-export symbols from their implementation
#     modules (odoo_client.OdooClient aliases odoo_client.client.client.OdooClient),
#     so a cross-reference has more than one equally valid target.
#   * misc.highlighting_failure: the design pages use mermaid fences, and Pygments
#     has no mermaid lexer. The effect is cosmetic.
suppress_warnings = ["ref.python", "misc.highlighting_failure"]


# The same re-export pattern makes apidoc document some objects on both the
# package page and the module page. The Python domain emits the "duplicate object
# description" warning without a suppress_warnings type, so a log filter drops it.
class _KnownWarningFilter:
    def filter(self, record):
        return "duplicate object description" not in record.getMessage()


sphinx_logging.getLogger("sphinx.domains.python").logger.addFilter(
    _KnownWarningFilter()
)

# The linkcheck builder skips local addresses and the Odoo instance patterns
# that appear in the examples.
linkcheck_ignore = [
    r"https?://localhost(:\d+)?(/.*)?",
    r"https?://127\.0\.0\.1(:\d+)?(/.*)?",
    r"https?://[\w.-]*odoo\.com(/.*)?",
    r"https?://example\.odoo\.com(/.*)?",
]

# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = "furo"

# -- API reference generation -------------------------------------------------
# sphinx-apidoc paths must be absolute: Sphinx does not chdir into the conf.py
# directory before running this hook. Each workspace package gets its own output
# directory, docs/api/<package>/. The docs/api tree is generated and ignored by git.

_PACKAGE_DIRS = [
    os.path.join(_REPO_ROOT, parent, name, "src", name)
    for parent, names in (
        (
            "libraries",
            (
                "odoo_client",
                "odoo_dev_data",
                "odoo_dev_core",
                "dispatcher",
                "usage_log",
            ),
        ),
        ("apps", ("odoo_cli", "odoo_mcp", "odoo_tui", "webui")),
    )
    for name in names
]


def setup(*_):
    from sphinx.ext.apidoc import main

    for package_dir in _PACKAGE_DIRS:
        name = os.path.basename(package_dir)
        main(
            [
                "-f",
                "--separate",
                "--module-first",
                "-o",
                os.path.join(_DOCS_DIR, "api", name),
                package_dir,
            ]
        )
