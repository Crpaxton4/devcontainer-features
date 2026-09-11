"""Structural-conformance tests for the consumer-side port set (#718).

The ports in :mod:`odoo_sdk.commands.protocols` are Protocols with no
registration step, so nothing but these tests proves the concrete adapters
actually satisfy them. Each test walks a Protocol's declared members and
asserts the adapter provides a matching callable with a signature the
consumers' call sites fit — parameter names, kinds, and order (defaults are
adapter detail; the gateway ports declare ``...`` where the adapter binds a
production default). Module-implemented ports (PEP 544) are checked with
``self`` stripped from the protocol method.

New file, deliberately additive (#718 test policy).
"""

import inspect
import unittest

from odoo_sdk.adapters import git as git_adapter
from odoo_sdk.adapters import github as github_adapter
from odoo_sdk.adapters import google as google_adapter
from odoo_sdk.client import OdooClient
from odoo_sdk.commands.protocols import (
    CalendarGateway,
    GitGateway,
    IssueTracker,
    RpcClient,
    SettingsView,
    StateStore,
)
from odoo_sdk.state import LocalConfig, LocalStateClient


def _protocol_methods(protocol):
    """Return the protocol's declared public method names."""
    return sorted(
        name
        for name, value in vars(protocol).items()
        if inspect.isfunction(value) and not name.startswith("_")
    )


def _parameter_shape(func, *, strip_self):
    """Return ``(name, kind)`` pairs for ``func``'s parameters."""
    params = list(inspect.signature(func).parameters.values())
    if strip_self and params and params[0].name == "self":
        params = params[1:]
    return [(p.name, p.kind) for p in params]


class _ConformanceAssertions(unittest.TestCase):
    maxDiff = None

    def assert_module_implements(self, module, protocol):
        """The module's functions match the protocol's methods (self stripped)."""
        for name in _protocol_methods(protocol):
            with self.subTest(port=protocol.__name__, member=name):
                impl = getattr(module, name, None)
                self.assertTrue(
                    callable(impl), f"{module.__name__} lacks callable {name!r}"
                )
                self.assertEqual(
                    _parameter_shape(getattr(protocol, name), strip_self=True),
                    _parameter_shape(impl, strip_self=False),
                )

    def assert_class_implements(self, cls, protocol):
        """The class's methods match the protocol's methods (both keep self)."""
        for name in _protocol_methods(protocol):
            with self.subTest(port=protocol.__name__, member=name):
                impl = getattr(cls, name, None)
                self.assertTrue(callable(impl), f"{cls.__name__} lacks {name!r}")
                self.assertEqual(
                    _parameter_shape(getattr(protocol, name), strip_self=True),
                    _parameter_shape(impl, strip_self=True),
                )


class TestStateStorePort(_ConformanceAssertions):
    def test_local_state_client_satisfies_the_port(self):
        self.assert_class_implements(LocalStateClient, StateStore)

    def test_port_is_consumer_derived_not_the_whole_store(self):
        """The ingest-only members stay off the port (adapters use them directly)."""
        declared = set(_protocol_methods(StateStore))
        for absent in (
            "add_event_dedup",
            "get_event",
            "get_events",
            "update_timesheet_id",
        ):
            self.assertNotIn(absent, declared)
        # Spot-check the members every consumer group leans on.
        for present in (
            "require_active_run",
            "create_run",
            "derive_sessions_overlapping",
            "get_unattributed_events",
            "record_session_upload",
            "get_setting",
        ):
            self.assertIn(present, declared)


class TestGatewayPorts(_ConformanceAssertions):
    def test_git_adapter_package_satisfies_git_gateway(self):
        self.assert_module_implements(git_adapter, GitGateway)

    def test_github_adapter_package_satisfies_issue_tracker(self):
        self.assert_module_implements(github_adapter, IssueTracker)

    def test_google_adapter_package_satisfies_calendar_gateway(self):
        self.assert_module_implements(google_adapter, CalendarGateway)


class TestRpcClientPort(_ConformanceAssertions):
    def test_odoo_client_satisfies_the_port(self):
        self.assert_class_implements(OdooClient, RpcClient)
        self.assertIsInstance(inspect.getattr_static(OdooClient, "uid"), property)


class TestSettingsViewPort(unittest.TestCase):
    def test_local_config_satisfies_the_view(self):
        declared = [
            name
            for name, value in vars(SettingsView).items()
            if isinstance(value, property)
        ]
        self.assertEqual(declared, ["session_gap_mins"])
        for name in declared:
            with self.subTest(member=name):
                self.assertIsInstance(
                    inspect.getattr_static(LocalConfig, name), property
                )


if __name__ == "__main__":
    unittest.main()
