"""AbuseTracker: counting events per person and escalating warn -> kick -> ban."""

import unittest
from unittest import mock

import abuse_tracker
from abuse_tracker import AbuseTracker


class TrackerTest(unittest.TestCase):
    def setUp(self):
        self.now = [1000.0]
        patcher = mock.patch.object(abuse_tracker.time, "time", lambda: self.now[0])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tracker = AbuseTracker(login_count=3, join_count=3, badword_count=2, window_sec=60)

    def record(self, times, kind="badword", key="budi@10.0.0.1"):
        return [self.tracker.record(kind, key) for _ in range(times)]

    def test_stages_escalate_up_to_three(self):
        self.assertEqual(self.record(2), [0, 1])
        self.assertEqual(self.record(2), [0, 2])
        self.assertEqual(self.record(2), [0, 3])
        self.assertEqual(self.record(2), [0, 0])  # already at the last stage
        self.assertEqual(self.tracker.get_stage("badword", "budi@10.0.0.1"), 3)

    def test_people_are_counted_separately(self):
        self.assertEqual(self.record(1, key="a"), [0])
        self.assertEqual(self.record(1, key="b"), [0])
        self.assertEqual(self.record(1, key="a"), [1])

    def test_events_outside_the_window_are_forgotten(self):
        self.record(1)
        self.now[0] += 61
        self.assertEqual(self.record(1), [0])

    def test_stage_clears_after_the_window(self):
        self.record(2)
        self.now[0] += 61
        self.assertEqual(self.record(2), [0, 1])

    def test_reset(self):
        self.record(2)
        self.tracker.reset("badword", "budi@10.0.0.1")
        self.assertEqual(self.tracker.get_stage("badword", "budi@10.0.0.1"), 0)
        self.assertNotIn(("badword", "budi@10.0.0.1"), self.tracker.tracked_keys())

    def test_empty_key_is_ignored(self):
        self.assertEqual(self.record(5, key=""), [0] * 5)

    def test_active_stages(self):
        self.record(2)
        self.now[0] += 10
        self.assertEqual(self.tracker.active_stages(), [("badword", "budi@10.0.0.1", 1, 50.0)])


if __name__ == "__main__":
    unittest.main()
