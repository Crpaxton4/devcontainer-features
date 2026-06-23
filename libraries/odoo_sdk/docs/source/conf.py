# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import os

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = "Odoo SDK"
copyright = "2026, Chris Paxton"
author = "Chris Paxton"
release = "0.1.0"

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx_autodoc_typehints",
    "myst_parser",
    "nbsphinx",
]

templates_path = ["_templates"]
exclude_patterns = []

autosummary_generate = True

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

# Never execute notebooks during the docs build — cells require a live Odoo
# connection and valid credentials that are not available in CI.
nbsphinx_execute = "never"


# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = "furo"
html_static_path = ["_static"]

# Copies the standalone diagram viewer into the build output. html_extra_path
# entries are resolved relative to this confdir (docs/source) and a single
# file is copied flat to the output root, hence the ../ link in the page that
# references it (see design/odoo-sdk-architecture-plan.md).
html_extra_path = ["design/odoo-sdk-architecture-diagrams.html"]


# -- API reference generation -------------------------------------------------
# sphinx-apidoc paths must be absolute: Sphinx does not chdir into the conf.py
# directory before running this hook, so plain relative paths resolve against
# whatever directory `sphinx-build` was invoked from, not against this file.

_SOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_SOURCE_DIR, "..", ".."))
_PACKAGE_DIR = os.path.join(_REPO_ROOT, "src", "odoo_sdk")
_API_OUT_DIR = os.path.join(_SOURCE_DIR, "api")


def setup(*_):
    from sphinx.ext.apidoc import main

    main(
        [
            "-f",
            "--separate",
            "--module-first",
            "-o",
            _API_OUT_DIR,
            _PACKAGE_DIR,
        ]
    )
