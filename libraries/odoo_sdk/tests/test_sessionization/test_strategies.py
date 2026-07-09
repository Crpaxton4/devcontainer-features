import unittest

from odoo_sdk.sessionization import (
    DEFAULT_SESSION_STRATEGY_CONFIGS,
    EventType,
    FixedDurationStrategy,
    SessionizationContext,
    SessionStrategyConfig,
    WindowedSessionStrategy,
    make_sessionization_context,
)
from odoo_sdk.sessionization.strategies import (
    StrategyEventGroup,
    _strategy_from_settings,
)

from ._helpers import one_day_config, raw_event


class TestContextConstruction(unittest.TestCase):
    def test_default_context_builds(self):
        ctx = make_sessionization_context()
        self.assertIsInstance(ctx, SessionizationContext)

    def test_missing_strategy_coverage_raises(self):
        only_commit = (DEFAULT_SESSION_STRATEGY_CONFIGS[0],)
        # development covers COMMIT + AGENT but not MERGE / REVIEW.
        with self.assertRaises(ValueError):
            make_sessionization_context(only_commit)

    def test_duplicate_event_type_coverage_raises(self):
        dup = (
            SessionStrategyConfig(
                "a", "A", (EventType.COMMIT,), "session", ("strategy", "repo", "task_id")
            ),
            SessionStrategyConfig(
                "b", "B", (EventType.COMMIT,), "session", ("strategy", "repo", "task_id")
            ),
        )
        with self.assertRaises(ValueError):
            make_sessionization_context(dup)

    def test_unknown_strategy_kind_raises(self):
        settings = SessionStrategyConfig(
            "x", "X", (EventType.COMMIT,), "nope", ("strategy", "repo", "task_id")
        )
        with self.assertRaises(ValueError):
            _strategy_from_settings(settings)


class TestWindowedStrategy(unittest.TestCase):
    def test_merges_commits_into_one_entry(self):
        ctx = make_sessionization_context()
        events = [
            raw_event(12, 0, task="101"),
            raw_event(12, 20, task="101"),
        ]
        entries = ctx.build_entries(events, 3600, one_day_config())
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].task_id, "101")
        self.assertEqual(entries[0].strategy_name, "development")

    def test_agent_events_route_to_windowed(self):
        ctx = make_sessionization_context()
        events = [raw_event(12, 0, task="101", event_type=EventType.AGENT)]
        entries = ctx.build_entries(events, 3600, one_day_config())
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].strategy_name, "development")

    def test_windowed_strategy_default_gap(self):
        settings = DEFAULT_SESSION_STRATEGY_CONFIGS[0]
        strat = WindowedSessionStrategy(settings)
        group = StrategyEventGroup(
            strat, ("development", "owner/repo", "101"), [raw_event(12, 0)]
        )
        entries = strat.build_entries(group, one_day_config(), gap_secs=None)
        self.assertEqual(len(entries), 1)


class TestFixedStrategy(unittest.TestCase):
    def test_one_entry_per_event(self):
        ctx = make_sessionization_context()
        events = [
            raw_event(12, 0, task="101", event_type=EventType.MERGE, pr_num=7),
            raw_event(13, 0, task="101", event_type=EventType.MERGE, pr_num=8),
        ]
        entries = ctx.build_entries(events, 3600, one_day_config())
        self.assertEqual(len(entries), 2)
        for entry in entries:
            self.assertEqual(entry.strategy_name, "merge")
            self.assertEqual(entry.duration_secs, 15 * 60)


class TestGrouping(unittest.TestCase):
    def test_unknown_task_grouped_as_unknown(self):
        ctx = make_sessionization_context()
        events = [raw_event(12, 0, task="")]
        groups = ctx.groups(events)
        self.assertTrue(any("UNKNOWN" in group.key for group in groups))

    def test_entries_sorted_by_start(self):
        ctx = make_sessionization_context()
        events = [
            raw_event(14, 0, task="102", event_type=EventType.MERGE, pr_num=2),
            raw_event(12, 0, task="101", event_type=EventType.MERGE, pr_num=1),
        ]
        entries = ctx.build_entries(events, 3600, one_day_config())
        self.assertLess(entries[0].start, entries[1].start)


if __name__ == "__main__":
    unittest.main()
