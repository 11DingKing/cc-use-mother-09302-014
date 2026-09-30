"""领域模型与常量。

时间约定
--------
所有跨午夜班次都以**活动本地时区**表达：``TimeWindow`` 携带本地日期，
``end_minutes`` 可以超过 1440（例如 22:00 到次日 02:00 = 1320..1560），
计算重叠、休息间隔时一律用"自本地当日 00:00 起的连续分钟轴"，
只有真正持久化时间戳时才转换为 UTC，避免 UTC 日期把跨午夜班次错误切开。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ---- 角色：教师、家长、学生助理 -------------------------------------------

class Role(str, Enum):
    TEACHER = "teacher"        # 教师志愿者
    PARENT = "parent"         # 家长志愿者
    STUDENT = "student"       # 学生助理

    @classmethod
    def of(cls, value: str) -> "Role":
        try:
            return cls(value)
        except ValueError:
            raise ValueError(f"未知角色：{value}") from None


# ---- 实体状态 ---------------------------------------------------------------

class QualStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"


class EventStatus(str, Enum):
    DRAFT = "draft"                 # 报名 / 核资阶段
    CONFIRMED = "confirmed"         # 已确认（资格已冻结）
    COMPLETED = "completed"


class AssignmentStatus(str, Enum):
    PLANNED = "planned"     # 草稿中的拟排班
    CONFIRMED = "confirmed"  # 随活动确认而冻结
    RELEASED = "released"   # 换班/请假后释放
    CANCELLED = "cancelled"


# 每种角色默认允许承担的岗位标签（可用岗位上的 role_required 进一步收窄）。
ALL_ROLES = tuple(r.value for r in Role)


@dataclass(frozen=True)
class TimeWindow:
    """本地时区下的时间窗，end 可跨越午夜（end_minutes > 1440）。"""

    local_date: str            # YYYY-MM-DD，班次开始的本地日期
    start_minutes: int         # 自当日 00:00 起，0..1439
    end_minutes: int           # 可 >1440；必须 > start_minutes

    def __post_init__(self) -> None:
        if not (0 <= self.start_minutes < 1440):
            raise ValueError("start_minutes 必须落在本地当日 0..1439")
        if self.end_minutes <= self.start_minutes:
            raise ValueError("end_minutes 必须晚于 start_minutes（跨午夜请超过 1440）")

    @property
    def duration_minutes(self) -> int:
        return self.end_minutes - self.start_minutes

    @property
    def crosses_midnight(self) -> bool:
        return self.end_minutes > 1440

    @classmethod
    def of(cls, local_date: str, start_hm: str, end_hm: str) -> "TimeWindow":
        """以 ``HH:MM`` 构造；结束时钟时间早于开始即视为跨午夜。"""
        sh, sm = (int(x) for x in start_hm.split(":"))
        eh, em = (int(x) for x in end_hm.split(":"))
        start = sh * 60 + sm
        end = eh * 60 + em
        if end <= start:
            end += 24 * 60
        return cls(local_date, start, end)

    def to_dict(self) -> dict[str, Any]:
        return {
            "local_date": self.local_date,
            "start_minutes": self.start_minutes,
            "end_minutes": self.end_minutes,
            "crosses_midnight": self.crosses_midnight,
        }


def _absolute_minutes(local_date: str, minutes: int) -> int:
    """把"本地日期 + 分钟轴（可超 1440）"投影到统一连续分钟轴上。"""
    y, m, d = (int(x) for x in local_date.split("-"))
    import datetime as _dt

    day_index = _dt.date(y, m, d).toordinal()
    return day_index * 1440 + minutes


def windows_overlap(a_date: str, a_start: int, a_end: int,
                    b_date: str, b_start: int, b_end: int) -> bool:
    """两个本地时间窗（均允许跨午夜）是否在连续时间轴上相交。"""
    s1, e1 = _absolute_minutes(a_date, a_start), _absolute_minutes(a_date, a_end)
    s2, e2 = _absolute_minutes(b_date, b_start), _absolute_minutes(b_date, b_end)
    return s1 < e2 and s2 < e1


def gap_between(a_date: str, a_end: int, b_date: str, b_start: int) -> int:
    """a 结束到 b 开始之间的分钟间隔（b 必须不早于 a 结束才有意义）。"""
    return _absolute_minutes(b_date, b_start) - _absolute_minutes(a_date, a_end)


@dataclass
class Person:
    id: str
    role: str
    name: str
    # 个人资料：手机号、证件号等为敏感字段，按查看者权限隔离（见 permissions）。
    profile: dict[str, Any] = field(default_factory=dict)


@dataclass
class Qualification:
    """角色资格 + 安全培训有效期。"""

    person_id: str
    role: str
    status: str = QualStatus.ACTIVE.value
    training_valid_until: str | None = None   # 本地日期 YYYY-MM-DD（含当日有效）
    version: int = 0                          # 乐观锁 / 冻结版本


@dataclass
class Recusal:
    """回避关系：志愿者在与某供应商相关的岗位上须回避。"""

    person_id: str
    vendor_id: str
    reason: str | None = None


@dataclass
class Availability:
    """志愿者可用时段（本地时区，可跨午夜）。"""

    person_id: str
    local_date: str
    start_minutes: int
    end_minutes: int


@dataclass
class Event:
    id: str
    name: str
    local_timezone: str
    status: str = EventStatus.DRAFT.value
    # 排班确认时冻结资格所用的快照版本号
    qualification_snapshot_version: int | None = None


@dataclass
class Position:
    """岗位需求：所需角色、供应商关联、人数与班次窗口。"""

    id: str
    event_id: str
    title: str
    required_role: str                 # 该岗位只接受该角色
    required_headcount: int
    window: TimeWindow
    vendor_id: str | None = None       # 与该岗位存在业务往来的供应商
    min_rest_minutes: int = 12 * 60   # 相邻两班之间的最小休息间隔


@dataclass
class Assignment:
    id: str
    event_id: str
    position_id: str
    person_id: str
    window: TimeWindow
    status: str = AssignmentStatus.PLANNED.value
    vendor_id: str | None = None
    # 冻结时记录的资格快照，使历史决定可审计
    snapshot: dict[str, Any] | None = None
