"""排班领域模型。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .timeutil import TimeWindow


class Role(str, Enum):
    """志愿者角色。"""

    TEACHER = "teacher"
    PARENT = "parent"
    STUDENT_ASSISTANT = "student_assistant"


ROLE_LABELS = {
    Role.TEACHER: "教师",
    Role.PARENT: "家长",
    Role.STUDENT_ASSISTANT: "学生助理",
}

SAFETY_TRAINING = "safety"

TRAINING_LABELS = {
    SAFETY_TRAINING: "安全培训",
}


class QualificationStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"


@dataclass(frozen=True)
class Qualification:
    """某角色的资格及其状态；撤销时整体替换为新实例。"""

    role: Role
    status: QualificationStatus
    granted_at: datetime
    revoked_at: datetime | None = None
    revoke_reason: str | None = None


@dataclass(frozen=True)
class TrainingRecord:
    """培训记录，valid_until 之后视为过期。"""

    training_type: str
    completed_at: datetime
    valid_until: datetime

    def valid_at(self, moment: datetime) -> bool:
        return self.completed_at <= moment <= self.valid_until


class RecusalTargetType(str, Enum):
    VENDOR = "vendor"
    POSITION = "position"


@dataclass(frozen=True)
class RecusalRelation:
    """回避关系：志愿者与供应商或岗位之间的利益冲突申报。"""

    target_type: RecusalTargetType
    target_id: str
    reason: str


@dataclass(frozen=True)
class PersonalProfile:
    """敏感个人资料，按权限隔离展示。"""

    phone: str = ""
    id_number: str = ""
    emergency_contact: str = ""
    notes: str = ""


@dataclass
class Volunteer:
    volunteer_id: str
    name: str
    profile: PersonalProfile = field(default_factory=PersonalProfile)
    qualifications: dict[Role, Qualification] = field(default_factory=dict)
    trainings: list[TrainingRecord] = field(default_factory=list)
    recusals: list[RecusalRelation] = field(default_factory=list)
    availability: list[TimeWindow] = field(default_factory=list)

    def active_roles(self) -> tuple[Role, ...]:
        return tuple(
            sorted(
                (role for role, q in self.qualifications.items() if q.status is QualificationStatus.ACTIVE),
                key=lambda role: role.value,
            )
        )


@dataclass(frozen=True)
class Position:
    """岗位需求：所需角色、所需培训、关联供应商。"""

    position_id: str
    name: str
    required_role: Role
    required_trainings: tuple[str, ...] = ()
    vendor_id: str | None = None


@dataclass
class Shift:
    """班次：某岗位在一个时间窗口内需要的人数。

    filled 为当前占用数，随确认、换班、请假、撤销、补位同步更新。
    """

    shift_id: str
    position_id: str
    window: TimeWindow
    headcount_needed: int
    filled: int = 0


class AssignmentStatus(str, Enum):
    CONFIRMED = "confirmed"
    RELEASED = "released"


@dataclass(frozen=True)
class QualificationSnapshot:
    """确认排班时冻结的资格快照，事后资格变化不影响历史记录。"""

    volunteer_id: str
    active_roles: tuple[Role, ...]
    trainings: tuple[TrainingRecord, ...]
    recusals: tuple[RecusalRelation, ...]
    frozen_at: datetime


@dataclass
class Assignment:
    """一次班次占用。"""

    assignment_id: str
    shift_id: str
    volunteer_id: str
    snapshot: QualificationSnapshot
    confirmed_at: datetime
    status: AssignmentStatus = AssignmentStatus.CONFIRMED
    emergency: bool = False
    rest_override: bool = False
    released_at: datetime | None = None
    release_reason: str | None = None


@dataclass(frozen=True)
class SchedulingPolicy:
    """排班约束策略。"""

    min_rest_minutes: int = 480
    training_must_cover_shift_end: bool = False


@dataclass(frozen=True)
class Event:
    """审计事件，记录占用变化等关键动作。"""

    event_type: str
    at: datetime
    payload: dict
