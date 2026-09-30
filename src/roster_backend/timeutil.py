"""本地时区时间窗口工具，支持跨午夜班次。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Iterable
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class TimeWindow:
    """带时区的起止时间窗口，end 必须晚于 start。"""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("时间必须携带时区信息")
        if self.end <= self.start:
            raise ValueError("结束时间必须晚于开始时间")

    @property
    def duration_minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60


def local_window(day: date, start: time, end: time, tz: ZoneInfo) -> TimeWindow:
    """按本地日期与起止时刻构造窗口。

    结束时刻不晚于开始时刻时视为跨午夜班次，结束时间顺延到次日，
    全部按本地时区解释，不做 UTC 换算。
    """
    start_dt = datetime.combine(day, start, tzinfo=tz)
    end_dt = datetime.combine(day, end, tzinfo=tz)
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)
    return TimeWindow(start_dt, end_dt)


def overlaps(a: TimeWindow, b: TimeWindow) -> bool:
    """两个窗口是否重叠（端点相接不算重叠）。"""
    return a.start < b.end and b.start < a.end


def gap_minutes(a: TimeWindow, b: TimeWindow) -> float:
    """两个窗口之间的间隔分钟数；重叠时为 0。"""
    if overlaps(a, b):
        return 0.0
    if a.end <= b.start:
        return (b.start - a.end).total_seconds() / 60
    return (a.start - b.end).total_seconds() / 60


def covers(windows: Iterable[TimeWindow], target: TimeWindow) -> bool:
    """判断若干可用时段的并集是否完整覆盖目标窗口（相邻时段可拼接）。"""
    spans = sorted(((w.start, w.end) for w in windows), key=lambda span: span[0])
    cursor = target.start
    for start, end in spans:
        if end <= cursor:
            continue
        if start > cursor:
            return False
        cursor = max(cursor, end)
        if cursor >= target.end:
            return True
    return cursor >= target.end
