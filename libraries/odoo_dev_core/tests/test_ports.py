"""The concrete ``odoo_dev_data`` objects satisfy the core ports structurally.

The ports are ``runtime_checkable`` Protocols with no registration step, so
nothing but these tests proves the adapters actually fit. ``isinstance`` checks
member presence; the signature tests pin parameter names and order, which the
handlers' call sites depend on.
"""

import inspect
import tempfile
import unittest
from pathlib import Path

from odoo_client.client import OdooClient
from odoo_dev_core.ports import (
    ChatterGateway,
    Clock,
    GitGateway,
    IssueTracker,
    JobsPort,
    RepoMapPort,
    RpcClient,
    SettingsView,
    StateStore,
    TaskRecords,
)
from odoo_dev_data.adapters import git as git_adapter
from odoo_dev_data.adapters import github as github_adapter
from odoo_dev_data.adapters import odoo as chatter_adapter
from odoo_dev_data.config import LocalConfig
from odoo_dev_data.events import EventStore
from odoo_dev_data.jobs import JobStore

from .fakes import (
    FakeChatter,
    FakeGit,
    FakeGitHub,
    FakeJobs,
    FakeRepoMap,
    FakeRpcClient,
    FakeTasks,
    FixedClock,
)


def _methods(protocol):
    return sorted(
        name
        for name, value in vars(protocol).items()
        if inspect.isfunction(value) and not name.startswith("_")
    )


def _shape(func, *, strip_self):
    params = list(inspect.signature(func).parameters.values())
    if strip_self and params and params[0].name == "self":
        params = params[1:]
    return [(p.name, p.kind) for p in params]


class _Conformance(unittest.TestCase):
    maxDiff = None

    def assert_module_implements(self, module, protocol):
        for name in _methods(protocol):
            with self.subTest(port=protocol.__name__, member=name):
                impl = getattr(module, name)
                self.assertEqual(
                    _shape(getattr(protocol, name), strip_self=True),
                    _shape(impl, strip_self=False),
                )

    def assert_class_implements(self, cls, protocol):
        for name in _methods(protocol):
            with self.subTest(port=protocol.__name__, member=name):
                self.assertEqual(
                    _shape(getattr(protocol, name), strip_self=True),
                    _shape(getattr(cls, name), strip_self=True),
                )


class TestDataObjectsSatisfyThePorts(_Conformance):
    def test_event_store(self):
        with tempfile.TemporaryDirectory() as home:
            self.assertIsInstance(EventStore(home=Path(home)), StateStore)
        self.assert_class_implements(EventStore, StateStore)

    def test_ingest_only_members_stay_off_the_state_port(self):
        declared = set(_methods(StateStore))
        for absent in ("add_event_dedup", "get_event", "get_events"):
            self.assertNotIn(absent, declared)

    def test_local_config(self):
        self.assertIsInstance(LocalConfig(), SettingsView)
        self.assert_class_implements(LocalConfig, SettingsView)

    def test_settings_view_properties_are_properties_on_local_config(self):
        for name, value in vars(SettingsView).items():
            if isinstance(value, property):
                with self.subTest(member=name):
                    self.assertIsInstance(
                        inspect.getattr_static(LocalConfig, name), property
                    )

    def test_job_store(self):
        with tempfile.TemporaryDirectory() as home:
            self.assertIsInstance(JobStore(home=Path(home)), JobsPort)
        self.assert_class_implements(JobStore, JobsPort)

    def test_odoo_client(self):
        self.assert_class_implements(OdooClient, RpcClient)
        self.assertIsInstance(inspect.getattr_static(OdooClient, "uid"), property)

    def test_adapter_modules_are_the_gateways(self):
        self.assertIsInstance(git_adapter, GitGateway)
        self.assertIsInstance(github_adapter, IssueTracker)
        self.assertIsInstance(chatter_adapter, ChatterGateway)
        self.assert_module_implements(git_adapter, GitGateway)
        self.assert_module_implements(github_adapter, IssueTracker)
        self.assert_module_implements(chatter_adapter, ChatterGateway)


class TestFakesSatisfyThePorts(unittest.TestCase):
    def test_every_fake(self):
        self.assertIsInstance(FakeRpcClient(), RpcClient)
        self.assertIsInstance(FakeGit(), GitGateway)
        self.assertIsInstance(FakeGitHub(), IssueTracker)
        self.assertIsInstance(FakeChatter(), ChatterGateway)
        self.assertIsInstance(FakeJobs(), JobsPort)
        self.assertIsInstance(FakeRepoMap(), RepoMapPort)
        self.assertIsInstance(FakeTasks(), TaskRecords)
        self.assertIsInstance(FixedClock(), Clock)


if __name__ == "__main__":
    unittest.main()
