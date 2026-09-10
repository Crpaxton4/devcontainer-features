"""Deprecation-shim contract tests for the #717 restructure.

The ``utilities/`` dissolution, the ``reap``/``prune`` relocation, and the
connection-settings extraction all promise that EVERY old import path keeps
working. These tests pin that promise explicitly:

* each old module path emits a :class:`DeprecationWarning` when (re-)imported
  and hands back the relocated module itself (``sys.modules`` aliasing), so
  attribute patches on the old path keep reaching the canonical code;
* the ``odoo_sdk.utilities`` package lazily forwards its historical
  re-exports with a warning and still raises ``AttributeError`` for unknown
  names;
* the ``odoo_sdk.state.config`` settings names are the identical objects now
  canonically homed in the shared-kernel ``odoo_sdk.settings`` module.

New file, deliberately additive: the pre-existing suite is the untouched
regression oracle for the moves themselves.
"""

import importlib
import sys
import unittest
import warnings

# Old module path -> canonical new module path. One row per aliasing shim.
ALIASED_MODULES = {
    "odoo_sdk.utilities.odoo_helpers": "odoo_sdk.services.odoo_helpers",
    "odoo_sdk.utilities.activities": "odoo_sdk.services.activities",
    "odoo_sdk.utilities.attachments": "odoo_sdk.services.attachments",
    "odoo_sdk.utilities.knowledge": "odoo_sdk.services.knowledge",
    "odoo_sdk.utilities.mail_status": "odoo_sdk.services.mail_status",
    "odoo_sdk.utilities.logged_lines": "odoo_sdk.services.logged_lines",
    "odoo_sdk.utilities.env": "odoo_sdk.tracking.env",
    "odoo_sdk.utilities.runs": "odoo_sdk.tracking.runs",
    "odoo_sdk.utilities.stats": "odoo_sdk.tracking.stats",
    "odoo_sdk.utilities.checkpoint": "odoo_sdk.tracking.checkpoint",
    "odoo_sdk.utilities.prompt_messages": "odoo_sdk.mcp.prompts.messages",
    "odoo_sdk.reap": "odoo_sdk.tracking.reap",
    "odoo_sdk.prune": "odoo_sdk.tracking.prune",
}


def _reimport(old_name: str):
    """Re-execute the shim at ``old_name``, returning (module, caught warnings).

    The shim body runs once per interpreter, so the cached alias is dropped
    from ``sys.modules`` first; re-importing then re-runs the shim file (its
    warning included) and re-establishes the alias.
    """
    sys.modules.pop(old_name, None)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        module = importlib.import_module(old_name)
    return module, caught


class TestAliasingShims(unittest.TestCase):
    def test_every_old_path_warns_and_aliases_the_relocated_module(self):
        for old, new in ALIASED_MODULES.items():
            with self.subTest(old=old):
                module, caught = _reimport(old)
                deprecations = [
                    w for w in caught if issubclass(w.category, DeprecationWarning)
                ]
                self.assertTrue(deprecations, f"{old} emitted no DeprecationWarning")
                message = str(deprecations[0].message)
                self.assertIn(old, message)
                self.assertIn(new, message)
                self.assertIn("#717", message)
                # sys.modules aliasing: the old path IS the relocated module,
                # so attribute patches through the old path reach the
                # canonical code (the untouched-suite compatibility contract).
                self.assertIs(module, importlib.import_module(new))
                self.assertIs(sys.modules[old], sys.modules[new])

    def test_from_import_still_yields_the_canonical_objects(self):
        module, _ = _reimport("odoo_sdk.utilities.env")
        from odoo_sdk.tracking.env import assert_sdk_configured

        self.assertIs(module.assert_sdk_configured, assert_sdk_configured)


class TestUtilitiesPackageForwarding(unittest.TestCase):
    def test_moved_names_warn_and_forward(self):
        utilities = importlib.import_module("odoo_sdk.utilities")
        expected = {
            "assert_sdk_configured": "odoo_sdk.tracking.env",
            "format_chatter": "odoo_sdk._utils",
            "resolve_many2one": "odoo_sdk.services.odoo_helpers",
            "name_search_projects": "odoo_sdk.services.odoo_helpers",
            "name_search_tasks": "odoo_sdk.services.odoo_helpers",
            "get_employee_id": "odoo_sdk.services.odoo_helpers",
            "post_chatter_note": "odoo_sdk.services.odoo_helpers",
            "get_task_chatter": "odoo_sdk.services.odoo_helpers",
            "get_task_detail": "odoo_sdk.services.odoo_helpers",
        }
        for name, target in expected.items():
            with self.subTest(name=name):
                with self.assertWarns(DeprecationWarning) as ctx:
                    resolved = getattr(utilities, name)
                self.assertIn(target, str(ctx.warning))
                self.assertIs(resolved, getattr(importlib.import_module(target), name))

    def test_all_is_unchanged(self):
        utilities = importlib.import_module("odoo_sdk.utilities")
        self.assertEqual(
            utilities.__all__,
            [
                "assert_sdk_configured",
                "html_to_markdown",
                "resolve_many2one",
                "format_chatter",
                "name_search_projects",
                "name_search_tasks",
                "get_employee_id",
                "post_chatter_note",
                "get_task_chatter",
                "get_task_detail",
            ],
        )

    def test_html_stays_shared_without_warning(self):
        utilities = importlib.import_module("odoo_sdk.utilities")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            converter = utilities.html_to_markdown
        from odoo_sdk.utilities.html import html_to_markdown

        self.assertIs(converter, html_to_markdown)
        self.assertFalse(
            [w for w in caught if issubclass(w.category, DeprecationWarning)]
        )

    def test_unknown_attribute_raises_attribute_error(self):
        utilities = importlib.import_module("odoo_sdk.utilities")
        with self.assertRaises(AttributeError):
            utilities.does_not_exist


class TestSettingsExtraction(unittest.TestCase):
    def test_state_config_re_exports_the_shared_kernel_objects(self):
        settings = importlib.import_module("odoo_sdk.settings")
        state_config = importlib.import_module("odoo_sdk.state.config")
        self.assertIs(
            state_config.OdooConnectionSettings, settings.OdooConnectionSettings
        )
        self.assertIs(state_config.CONNECTION_ENV_VARS, settings.CONNECTION_ENV_VARS)
        self.assertEqual(
            state_config.DEFAULT_TIMEOUT_SECONDS, settings.DEFAULT_TIMEOUT_SECONDS
        )

    def test_transports_share_the_kernel_timeout_by_reference(self):
        from odoo_sdk.settings import DEFAULT_TIMEOUT_SECONDS
        from odoo_sdk.transport.json2 import (
            DEFAULT_REQUEST_TIMEOUT_SECONDS as json2_timeout,
        )
        from odoo_sdk.transport.rpc import (
            DEFAULT_REQUEST_TIMEOUT_SECONDS as rpc_timeout,
        )

        self.assertEqual(json2_timeout, DEFAULT_TIMEOUT_SECONDS)
        self.assertEqual(rpc_timeout, DEFAULT_TIMEOUT_SECONDS)

    def test_transport_no_longer_imports_state_config(self):
        """The #717 extraction goal, pinned: transport names settings only."""
        import pathlib

        import odoo_sdk.transport as transport

        transport_dir = pathlib.Path(transport.__file__).parent
        offenders = [
            path.name
            for path in transport_dir.glob("*.py")
            if "state.config" in path.read_text()
        ]
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
