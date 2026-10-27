import unittest
from unittest.mock import MagicMock, patch

from odoo_dev_core import default_registry
from odoo_mcp import __main__ as entry


class TestMainEntryPoint(unittest.TestCase):
    def setUp(self):
        guard = patch("odoo_mcp.__main__.assert_configured")
        self.guard = guard.start()
        self.addCleanup(guard.stop)
        client = patch("odoo_mcp.bootstrap.OdooClient")
        self.client = client.start()
        self.addCleanup(client.stop)
        settings = patch("odoo_mcp.bootstrap.settings_from_config")
        settings.start()
        self.addCleanup(settings.stop)

    def _run(self, argv=()):
        with patch("odoo_mcp.__main__.OdooMCPServer") as MockServer:
            entry.main(list(argv))
        return MockServer

    def test_main_builds_default_service_and_runs(self):
        MockServer = self._run()
        self.client.from_settings.assert_called_once()
        MockServer.assert_called_once()
        MockServer.return_value.run.assert_called_once_with()

    def test_main_checks_the_configuration_first(self):
        self._run()
        self.guard.assert_called_once()

    def test_main_registers_all_builtins(self):
        service = self._run().call_args.args[0]
        self.assertEqual(service.registry.ids(), default_registry().ids())

    def test_main_passes_resolved_profiling_to_server(self):
        fake_config = MagicMock()
        fake_config.profiling = True
        with (
            patch("odoo_mcp.__main__.LocalConfig") as MockConfig,
            patch("odoo_mcp.__main__.bootstrap") as boot,
            patch("odoo_mcp.__main__.OdooMCPServer") as MockServer,
        ):
            MockConfig.load.return_value = fake_config
            entry.main([])
        boot.assert_called_once_with(config=fake_config)
        self.assertIs(MockServer.call_args.kwargs["profiling"], True)

    def test_profiling_flag_turns_profiling_on(self):
        MockServer = self._run(["--profiling"])
        self.assertIs(MockServer.call_args.kwargs["profiling"], True)

    def test_profiling_is_off_by_default(self):
        MockServer = self._run()
        self.assertIs(MockServer.call_args.kwargs["profiling"], False)


if __name__ == "__main__":
    unittest.main()
