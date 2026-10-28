"""The resync workflow against fake gateways.

The pullers themselves are contract-tested data-side in
``libraries/odoo_dev_data/tests/test_adapters_sync.py``. Here the gateways are
fakes, so the tests assert only the workflow's own behavior: which pullers run,
what each receives, and how the per-source results are reported.
"""

import unittest
from datetime import date

from odoo_dev_core.workflows.resync import ResyncReport, parse_sources, run_resync

from ..fakes import FakeChatter, FakeGit, FakeGitHub, fake_services


class TestParseSources(unittest.TestCase):
    def test_default_and_blank_select_all(self) -> None:
        self.assertEqual(parse_sources("git,github,odoo"), ["git", "github", "odoo"])
        self.assertEqual(parse_sources(""), ["git", "github", "odoo"])
        self.assertEqual(parse_sources("  "), ["git", "github", "odoo"])

    def test_subset_kept_in_stable_order(self) -> None:
        self.assertEqual(parse_sources("odoo,git"), ["git", "odoo"])

    def test_unknown_tokens_ignored(self) -> None:
        self.assertEqual(parse_sources("git,bogus"), ["git"])


class TestRunResync(unittest.TestCase):
    def setUp(self) -> None:
        self.git = FakeGit({"inserted": 2})
        self.github = FakeGitHub({"inserted": 1})
        self.chatter = FakeChatter({"inserted": 3})
        self.services = fake_services(
            git=self.git, github=self.github, chatter=self.chatter
        )

    def test_runs_all_sources_by_default(self) -> None:
        report = run_resync(self.services)
        self.assertEqual(
            report.results,
            {
                "git": {"inserted": 2},
                "github": {"inserted": 1},
                "odoo": {"inserted": 3},
            },
        )

    def test_each_puller_receives_the_shared_ports(self) -> None:
        run_resync(self.services)
        client = self.services.client
        events, config = self.services.events, self.services.config
        window = {"start": None, "end": None}
        # git and github take state, config, client. The odoo puller is
        # client-first, then state and config.
        self.assertEqual(
            self.git.calls, [{"args": (events, config, client), "kwargs": window}]
        )
        self.assertEqual(
            self.github.calls, [{"args": (events, config, client), "kwargs": window}]
        )
        self.assertEqual(
            self.chatter.calls, [{"args": (client, events, config), "kwargs": window}]
        )

    def test_subset_runs_only_requested_pullers(self) -> None:
        report = run_resync(self.services, sources=("git",))
        self.assertEqual(report.results, {"git": {"inserted": 2}})
        self.assertEqual(len(self.git.calls), 1)
        self.assertEqual(self.github.calls, [])
        self.assertEqual(self.chatter.calls, [])

    def test_unknown_sources_are_ignored_and_order_is_fixed(self) -> None:
        report = run_resync(self.services, sources=("odoo", "bogus", "git"))
        self.assertEqual(list(report.results), ["git", "odoo"])

    def test_the_range_reaches_every_puller(self) -> None:
        run_resync(self.services, start=date(2026, 7, 1), end=date(2026, 7, 31))
        expected = {"start": date(2026, 7, 1), "end": date(2026, 7, 31)}
        for gateway in (self.git, self.github, self.chatter):
            self.assertEqual(gateway.calls[0]["kwargs"], expected)


class TestResyncReport(unittest.TestCase):
    def test_ok_without_errors_even_with_skips(self) -> None:
        report = ResyncReport({"git": {"inserted": 1}, "odoo": {"skipped": "no odoo"}})
        self.assertTrue(report.ok)

    def test_one_error_makes_the_report_not_ok(self) -> None:
        services = fake_services(
            git=FakeGit({"error": "no repos"}), github=FakeGitHub({"inserted": 1})
        )
        report = run_resync(services, sources=("git", "github"))
        self.assertFalse(report.ok)
        self.assertEqual(report.results["github"], {"inserted": 1})

    def test_lines_follow_the_run_order(self) -> None:
        report = ResyncReport(
            {
                "git": {"inserted": 2, "found": 5, "repos": 3, "failed_repos": 1},
                "github": {"skipped": "no gh"},
                "odoo": {"error": "boom"},
            }
        )
        self.assertEqual(
            report.lines(),
            [
                "git: inserted 2 (found 5 in 3 repos; 1 failed)",
                "github: skipped (no gh)",
                "odoo: error (boom)",
            ],
        )

    def test_line_flags_warnings_notes_and_unattributed_reviews(self) -> None:
        report = ResyncReport(
            {
                "github": {
                    "inserted": 1,
                    "unattributed_reviews": ["pr 4"],
                    "warnings": ["truncated"],
                    "note": "range ignored",
                }
            }
        )
        self.assertEqual(
            report.lines(),
            [
                "github: inserted 1; WARNING: 1 review event(s) resolved no task id"
                "; WARNING: truncated; note: range ignored"
            ],
        )


if __name__ == "__main__":
    unittest.main()
