"""Schedule of the playlists to sort (sort_playlist.py): when a Notion row is due."""
import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sort_playlist as sp  # noqa: E402

P = sp.PARIS


def row(frequency=None, day=None, hour=None, last=None):
    props = {"Frequency": {"select": {"name": frequency} if frequency else None},
             "Day": {"select": {"name": day} if day else None},
             "Hour": {"number": hour},
             "Last sorted": {"date": {"start": last} if last else None}}
    return props


class Schedule(unittest.TestCase):
    def test_defaults_are_friday_19(self):
        self.assertEqual(sp.row_schedule(row()), ("Weekly", 4, 19))

    def test_weekly_due_after_the_hour(self):
        friday = datetime(2026, 11, 13, 19, 7, tzinfo=P)
        self.assertTrue(sp.is_due(row(last="2026-11-11T16:40:00+01:00"), friday))
        self.assertFalse(sp.is_due(row(last="2026-11-13T19:20:00+01:00"), friday))

    def test_weekly_not_due_before_the_hour(self):
        friday_morning = datetime(2026, 11, 13, 10, 7, tzinfo=P)
        self.assertFalse(sp.is_due(row(last="2026-11-11T16:40:00+01:00"), friday_morning))

    def test_missed_time_is_caught_up(self):
        saturday = datetime(2026, 11, 14, 9, 7, tzinfo=P)
        self.assertTrue(sp.is_due(row(last="2026-11-11T16:40:00+01:00"), saturday))

    def test_daily(self):
        now = datetime(2026, 11, 12, 8, 7, tzinfo=P)
        r = row("Daily", hour=8, last="2026-11-11T08:30:00+01:00")
        self.assertTrue(sp.is_due(r, now))
        self.assertFalse(sp.is_due(row("Daily", hour=9, last="2026-11-11T09:30:00+01:00"), now))

    def test_monthly_first_monday(self):
        # First Monday of December 2026: the 7th.
        self.assertEqual(sp.last_slot("Monthly", 0, 10, datetime(2026, 12, 20, 12, 0, tzinfo=P)),
                         datetime(2026, 12, 7, 10, 0, tzinfo=P))

    def test_new_row_is_due(self):
        self.assertTrue(sp.is_due(row(), datetime(2026, 11, 12, 8, 7, tzinfo=P)))

    def test_date_without_time(self):
        friday = datetime(2026, 11, 13, 19, 7, tzinfo=P)
        self.assertTrue(sp.is_due(row(last="2026-11-13"), friday))


if __name__ == "__main__":
    unittest.main()
