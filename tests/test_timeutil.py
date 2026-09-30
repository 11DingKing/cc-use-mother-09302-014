"""本地时区时间窗口工具的回归测试。"""
from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from roster_backend.timeutil import TimeWindow, covers, gap_minutes, local_window, overlaps

TZ = ZoneInfo("Asia/Shanghai")


class LocalWindowTest(unittest.TestCase):
    def test_cross_midnight_rolls_to_next_day(self) -> None:
        window = local_window(date(2026, 10, 1), time(22, 0), time(2, 0), TZ)
        self.assertEqual(window.start, datetime(2026, 10, 1, 22, 0, tzinfo=TZ))
        self.assertEqual(window.end, datetime(2026, 10, 2, 2, 0, tzinfo=TZ))
        self.assertEqual(window.duration_minutes, 240)

    def test_same_day_window(self) -> None:
        window = local_window(date(2026, 10, 1), time(9, 0), time(12, 0), TZ)
        self.assertEqual(window.start.date(), date(2026, 10, 1))
        self.assertEqual(window.end.date(), date(2026, 10, 1))

    def test_naive_datetime_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TimeWindow(datetime(2026, 10, 1, 8, 0), datetime(2026, 10, 1, 9, 0, tzinfo=TZ))

    def test_end_not_after_start_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TimeWindow(
                datetime(2026, 10, 1, 9, 0, tzinfo=TZ),
                datetime(2026, 10, 1, 9, 0, tzinfo=TZ),
            )


class OverlapGapTest(unittest.TestCase):
    def setUp(self) -> None:
        self.morning = local_window(date(2026, 10, 1), time(8, 0), time(12, 0), TZ)
        self.night = local_window(date(2026, 10, 1), time(22, 0), time(2, 0), TZ)

    def test_overlaps(self) -> None:
        other = local_window(date(2026, 10, 1), time(11, 0), time(13, 0), TZ)
        self.assertTrue(overlaps(self.morning, other))
        self.assertFalse(overlaps(self.morning, self.night))

    def test_gap_minutes_across_midnight(self) -> None:
        dawn = local_window(date(2026, 10, 2), time(6, 0), time(8, 0), TZ)
        self.assertEqual(gap_minutes(self.night, dawn), 240)
        self.assertEqual(gap_minutes(dawn, self.night), 240)

    def test_adjacent_windows_have_zero_gap(self) -> None:
        noon = local_window(date(2026, 10, 1), time(12, 0), time(14, 0), TZ)
        self.assertFalse(overlaps(self.morning, noon))
        self.assertEqual(gap_minutes(self.morning, noon), 0)


class CoversTest(unittest.TestCase):
    def test_adjacent_windows_cover_target(self) -> None:
        first = local_window(date(2026, 10, 1), time(8, 0), time(10, 0), TZ)
        second = local_window(date(2026, 10, 1), time(10, 0), time(12, 0), TZ)
        target = local_window(date(2026, 10, 1), time(8, 0), time(12, 0), TZ)
        self.assertTrue(covers([first, second], target))

    def test_gap_between_windows_does_not_cover(self) -> None:
        first = local_window(date(2026, 10, 1), time(8, 0), time(9, 0), TZ)
        second = local_window(date(2026, 10, 1), time(10, 0), time(12, 0), TZ)
        target = local_window(date(2026, 10, 1), time(8, 0), time(12, 0), TZ)
        self.assertFalse(covers([first, second], target))

    def test_empty_windows_do_not_cover(self) -> None:
        target = local_window(date(2026, 10, 1), time(8, 0), time(12, 0), TZ)
        self.assertFalse(covers([], target))


if __name__ == "__main__":
    unittest.main()
