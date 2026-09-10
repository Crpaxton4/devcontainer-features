"""Shim/alias contract tests for the #718 restructure (port set + promotion).

The per-system adapter packages, the ``state_persistence`` relocation, the
promoted tracker vocabulary, and the new core doors all promise that EVERY
old import path keeps working and hands back the identical objects. These
tests pin that promise explicitly. New file, deliberately additive: the
pre-existing suite is the untouched regression oracle for the moves
themselves (see ``tests/test_shims.py`` for the #717 counterpart).
"""

import importlib
import sys
import unittest
import warnings


def _reimport(old_name: str):
    """Re-execute the shim at ``old_name``, returning (module, caught warnings)."""
    sys.modules.pop(old_name, None)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        module = importlib.import_module(old_name)
    return module, caught


class TestStatePersistenceShim(unittest.TestCase):
    def test_old_path_warns_and_aliases_the_relocated_module(self):
        module, caught = _reimport("odoo_sdk.adapters.state_persistence")
        deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        self.assertTrue(deprecations, "state_persistence emitted no DeprecationWarning")
        message = str(deprecations[0].message)
        self.assertIn("odoo_sdk.adapters.state_persistence", message)
        self.assertIn("odoo_sdk.adapters.state.persistence", message)
        self.assertIn("#718", message)
        self.assertIs(
            module, importlib.import_module("odoo_sdk.adapters.state.persistence")
        )
        self.assertIs(
            sys.modules["odoo_sdk.adapters.state_persistence"],
            sys.modules["odoo_sdk.adapters.state.persistence"],
        )


class TestStateModelsAlias(unittest.TestCase):
    """``state.models`` is a sanctioned (non-warning) alias of the promoted module."""

    def test_alias_is_the_promoted_module_and_does_not_warn(self):
        module, caught = _reimport("odoo_sdk.state.models")
        self.assertIs(module, importlib.import_module("odoo_sdk.tracking.models"))
        self.assertFalse(
            [w for w in caught if issubclass(w.category, DeprecationWarning)],
            "the state.models alias must not deprecation-warn: odoo_sdk.state "
            "imports it eagerly on every import",
        )

    def test_public_reexports_are_the_promoted_objects(self):
        import odoo_sdk.state as state
        import odoo_sdk.tracking.models as models

        for name in (
            "TaskState",
            "TaskRun",
            "EventRecord",
            "SessionWindow",
            "session_key",
            "TaskAlreadyRunningError",
            "TaskNotRunningError",
            "InvalidStateTransitionError",
            "TrackerStateMissingError",
        ):
            with self.subTest(name=name):
                self.assertIs(getattr(state, name), getattr(models, name))

    def test_repo_label_vocabulary_still_importable_from_state_db(self):
        import odoo_sdk.state.db as db
        import odoo_sdk.tracking.models as models

        self.assertIs(db.format_repo_label, models.format_repo_label)
        self.assertEqual(db.AGENTLESS_REPO, models.AGENTLESS_REPO)
        self.assertEqual(db.AGENTLESS_REPO_LABEL, models.AGENTLESS_REPO_LABEL)
        self.assertEqual(db.AGENTLESS_REPO_SENTINEL, models.AGENTLESS_REPO_SENTINEL)

    def test_errors_facade_still_reexports_the_identical_fsm_errors(self):
        import odoo_sdk.errors as errors
        import odoo_sdk.tracking.models as models

        for name in (
            "TaskAlreadyRunningError",
            "TaskNotRunningError",
            "InvalidStateTransitionError",
            "TrackerStateMissingError",
        ):
            with self.subTest(name=name):
                self.assertIs(getattr(errors, name), getattr(models, name))


class TestAdapterPackages(unittest.TestCase):
    """The per-system packages export the same objects as the historical paths."""

    def test_facade_packages_reexport_the_pullers(self):
        from odoo_sdk.adapters import external_sync as ex
        from odoo_sdk.adapters import git, github, google, odoo

        self.assertIs(git.sync_git_log, ex.sync_git_log)
        self.assertIs(github.sync_github, ex.sync_github)
        self.assertIs(odoo.sync_odoo_chatter, ex.sync_odoo_chatter)
        self.assertIs(google.sync_google_calendar, ex.sync_google_calendar)
        self.assertIs(google.sync_gmail, ex.sync_gmail)
        self.assertIs(google.GoogleAuthError, ex.GoogleAuthError)
        self.assertIs(google.GoogleAPIError, ex.GoogleAPIError)

    def test_google_canonical_home_backs_the_compat_reexports(self):
        from odoo_sdk.adapters import external_sync as ex
        from odoo_sdk.adapters.google import sync as gs

        for name in (
            "sync_google_calendar",
            "sync_gmail",
            "GoogleAuthError",
            "GoogleAPIError",
            "_urllib_transport",
            "_expand_ticks",
            "_tick_external_id",
            "_parse_google_dt",
            "_resolve_google_token_path",
        ):
            with self.subTest(name=name):
                self.assertIs(getattr(ex, name), getattr(gs, name))

    def test_flat_adapters_surface_is_unchanged(self):
        import odoo_sdk.adapters as adapters

        for name in (
            "sync_git_log",
            "sync_github",
            "sync_odoo_chatter",
            "sync_google_calendar",
            "sync_gmail",
            "GoogleAuthError",
            "GoogleAPIError",
            "load_raw_events",
            "source_to_event_type",
            "UnknownEventSourceError",
            "event_record_to_raw_event",
            "raw_event_to_event_record",
            "is_synthetic_tick",
        ):
            with self.subTest(name=name):
                self.assertTrue(hasattr(adapters, name))

    def test_core_doors_reexport_the_data_side_callables(self):
        from odoo_sdk.adapters.state import load_raw_events as canonical_load
        from odoo_sdk.billing.logged import logged_hours_by_task_day as door_logged
        from odoo_sdk.services.logged_lines import (
            logged_hours_by_task_day as canonical_logged,
        )
        from odoo_sdk.tracking.events import load_raw_events as door_load

        self.assertIs(door_load, canonical_load)
        self.assertIs(door_logged, canonical_logged)


if __name__ == "__main__":
    unittest.main()
