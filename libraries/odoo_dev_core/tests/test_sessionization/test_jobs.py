"""Job rows as session windows: ``sessions_from_jobs`` and ``merge_job_windows``."""

import unittest
from datetime import datetime, timedelta, timezone

from odoo_dev_core.sessionization.jobs import (
    derive_windows,
    merge_job_windows,
    sessions_from_jobs,
    to_us,
)
from odoo_dev_data.events import EventRecord, SessionWindow, session_key

from ..fakes import FakeJob, FakeJobs, fake_services

UTC = timezone.utc
GAP = 3600
T0 = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def job(start: float, end: float, *, sid: str = "s1", task: str = "101", **kw):
    return FakeJob(
        task_id=task,
        started_us=to_us(at(start)),
        ended_us=to_us(at(end)),
        session_id=sid,
        **kw,
    )


def event_window(start: float, end: float, *, ids=(1,), task="101", repo="o/r"):
    return SessionWindow(
        id=min(ids),
        task_id=task,
        repo=repo,
        started_at=at(start),
        ended_at=at(end),
        event_ids=tuple(ids),
    )


def merged(events, jobs):
    return merge_job_windows(events, sessions_from_jobs(jobs), gap_secs=GAP)


class TestSessionsFromJobs(unittest.TestCase):
    def test_one_window_per_job(self):
        (window,) = sessions_from_jobs([job(0, 30, sid="abc", repo="o/r", pr_num=7)])
        self.assertEqual((window.started_at, window.ended_at), (at(0), at(30)))
        self.assertEqual(session_key(window), "101|run:abc")
        self.assertEqual(window.id, -to_us(at(0)))
        self.assertEqual((window.repo, window.pr_num), ("o/r", 7))
        self.assertEqual(
            (window.strategy_name, window.category), ("development", "Development")
        )
        self.assertEqual(window.event_ids, ())
        self.assertFalse(window.has_failed)

    def test_failed_outcome_marks_the_window(self):
        (window,) = sessions_from_jobs([job(0, 50, outcome="FAILED")])
        self.assertTrue(window.has_failed)
        self.assertEqual(window.duration_seconds, 3000)

    def test_dead_letter_state_marks_the_window(self):
        (window,) = sessions_from_jobs([job(0, 5, state="DEAD_LETTER")])
        self.assertTrue(window.has_failed)

    def test_a_job_without_a_start_or_task_is_skipped(self):
        never_started = FakeJob(task_id="101", started_us=None, ended_us=None)
        no_task = job(0, 5, task="")
        self.assertEqual(sessions_from_jobs([never_started, no_task]), [])

    def test_a_job_without_an_end_is_zero_length(self):
        (window,) = sessions_from_jobs(
            [FakeJob(task_id="1", started_us=to_us(at(0)), ended_us=None)]
        )
        self.assertEqual(window.duration_seconds, 0)


class TestMerge(unittest.TestCase):
    def test_a_job_alone_is_keyed_on_its_session(self):
        (window,) = merged([], [job(0, 30, sid="s9")])
        self.assertEqual(session_key(window), "101|run:s9")
        self.assertEqual(window.id, -to_us(at(0)))

    def test_event_within_the_gap_joins_the_job(self):
        (window,) = merged([event_window(0, 0, ids=(4,))], [job(30, 50, sid="s2")])
        self.assertEqual((window.started_at, window.ended_at), (at(0), at(50)))
        self.assertEqual(window.event_ids, (4,))
        self.assertEqual(window.id, 4)
        self.assertEqual(session_key(window), "101|run:s2")

    def test_event_outside_the_gap_stays_a_window_of_its_own(self):
        windows = merged([event_window(0, 0, ids=(4,))], [job(300, 310, sid="s2")])
        self.assertEqual(len(windows), 2)
        self.assertEqual(session_key(windows[0]), "101|4")
        self.assertEqual(session_key(windows[1]), "101|run:s2")

    def test_exactly_the_gap_joins_and_a_second_past_splits(self):
        end = at(10)
        joins = job(10, 20, sid="a")
        joins.started_us = to_us(end) + GAP * 1_000_000
        joins.ended_us = joins.started_us + 60_000_000
        self.assertEqual(len(merged([event_window(0, 10)], [joins])), 1)
        joins.started_us += 1_000_000
        joins.ended_us += 1_000_000
        self.assertEqual(len(merged([event_window(0, 10)], [joins])), 2)

    def test_a_job_bridging_two_event_windows_makes_one(self):
        events = [event_window(0, 0, ids=(1,)), event_window(200, 200, ids=(2,))]
        self.assertEqual(len(merged(events, [])), 2)
        (window,) = merged(events, [job(30, 170, sid="b")])
        self.assertEqual(window.event_ids, (1, 2))
        self.assertEqual(window.id, 1)

    def test_the_earliest_job_keys_the_window(self):
        (window,) = merged([], [job(60, 70, sid="late"), job(0, 10, sid="early")])
        self.assertEqual(session_key(window), "101|run:early")
        self.assertEqual(window.id, -to_us(at(0)))
        self.assertEqual(window.duration_seconds, 4200)

    def test_the_key_does_not_move_when_an_event_joins(self):
        before = merged([], [job(0, 2, sid="k")])[0]
        after = merged([event_window(0, 61, ids=(9,))], [job(0, 2, sid="k")])[0]
        self.assertEqual(session_key(before), session_key(after))

    def test_failed_job_sticks_to_the_merged_window(self):
        jobs = [job(0, 50, sid="a", outcome="FAILED"), job(60, 70, sid="b")]
        (window,) = merged([], jobs)
        self.assertTrue(window.has_failed)
        self.assertEqual(window.duration_seconds, 4200)

    def test_completed_jobs_carry_no_mark(self):
        self.assertFalse(merged([], [job(0, 50)])[0].has_failed)

    def test_two_tasks_stay_separate(self):
        windows = merged([], [job(0, 30, task="1"), job(10, 40, task="2", sid="s2")])
        self.assertEqual({w.task_id for w in windows}, {"1", "2"})

    def test_repo_is_the_non_empty_label_in_any_order(self):
        jobs = [
            job(0, 5, repo=""),
            job(6, 9, repo="a/b", sid="x"),
            job(10, 12, repo=""),
        ]
        self.assertEqual(merged([], jobs)[0].repo, "a/b")

    def test_a_job_makes_a_review_window_a_development_window(self):
        review = event_window(0, 0)
        review.strategy_name, review.category = "review", "Review"
        (window,) = merged([review], [job(10, 20)])
        self.assertEqual(window.strategy_name, "development")

    def test_the_result_is_ordered_by_start(self):
        windows = merged([event_window(500, 500, ids=(1,), task="2")], [job(0, 5)])
        self.assertEqual([w.started_at for w in windows], [at(0), at(500)])


class TestDeriveWindows(unittest.TestCase):
    def setUp(self):
        self.services = fake_services()
        self.state = self.services.events

    def _event(self, minutes: float, task: str = "101", repo: str = "") -> None:
        self.state.add_event(
            EventRecord(
                id=None,
                source="commit",
                timestamp=at(minutes),
                task_ids=[task],
                repo=repo,
            )
        )

    def derive(self, jobs, **kw):
        return derive_windows(self.state, jobs, at(-60), at(600), gap_secs=GAP, **kw)

    def test_without_a_job_port_the_events_stand_alone(self):
        self._event(0)
        (window,) = self.derive(None)
        self.assertEqual(session_key(window), f"101|{window.id}")

    def test_finished_jobs_fold_into_the_event_windows(self):
        self._event(0)
        jobs = FakeJobs([job(30, 50, sid="s2")])
        (window,) = self.derive(jobs)
        self.assertEqual((window.started_at, window.ended_at), (at(0), at(50)))
        self.assertEqual(session_key(window), "101|run:s2")

    def test_the_job_query_is_widened_by_the_derivation_margin(self):
        jobs = FakeJobs()
        self.derive(jobs)
        (call,) = jobs.list_calls
        day = 86_400 * 1_000_000
        self.assertEqual(call["start_us"], to_us(at(-60)) - day)
        self.assertEqual(call["end_us"], to_us(at(600)) + day)

    def test_a_job_that_overlaps_only_the_margin_is_dropped(self):
        far = job(-60 * 12, -60 * 11, sid="far")
        self.assertEqual(self.derive(FakeJobs([far])), [])

    def test_unbounded_edges_leave_the_job_bounds_open(self):
        jobs = FakeJobs([job(0, 5)])
        windows = derive_windows(
            self.state, jobs, datetime.min, datetime.max, gap_secs=GAP
        )
        self.assertEqual(len(windows), 1)
        self.assertEqual(jobs.list_calls, [{"start_us": None, "end_us": None}])

    def test_task_and_repo_filters_apply_to_the_merged_windows(self):
        self._event(0, task="101", repo="")
        jobs = FakeJobs([job(10, 20, repo="o/r"), job(0, 5, task="202", sid="t2")])
        (window,) = self.derive(jobs, task_id="101")
        self.assertEqual(window.task_id, "101")
        self.assertEqual(len(self.derive(jobs, repo="o/r")), 1)
        self.assertEqual(self.derive(jobs, repo="x/y"), [])


if __name__ == "__main__":
    unittest.main()
