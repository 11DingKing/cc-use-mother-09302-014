"""活动志愿者排班资格后端。"""
from __future__ import annotations

from .decisions import Decision, Rejection, RejectionCode
from .errors import NotFoundError, RosterError, StateError
from .models import (
    ROLE_LABELS,
    SAFETY_TRAINING,
    TRAINING_LABELS,
    Assignment,
    AssignmentStatus,
    Event,
    PersonalProfile,
    Position,
    Qualification,
    QualificationSnapshot,
    QualificationStatus,
    RecusalRelation,
    RecusalTargetType,
    Role,
    SchedulingPolicy,
    Shift,
    TrainingRecord,
    Volunteer,
)
from .permissions import AccessLevel, Viewer
from .repository import InMemoryRepository
from .service import (
    AssignmentResult,
    BackfillResult,
    CandidateAttempt,
    LeaveResult,
    RevokeResult,
    RosterService,
    SwapResult,
)
from .timeutil import TimeWindow, covers, gap_minutes, local_window, overlaps

__all__ = [
    "ROLE_LABELS",
    "SAFETY_TRAINING",
    "TRAINING_LABELS",
    "AccessLevel",
    "Assignment",
    "AssignmentResult",
    "AssignmentStatus",
    "BackfillResult",
    "CandidateAttempt",
    "Decision",
    "Event",
    "InMemoryRepository",
    "LeaveResult",
    "NotFoundError",
    "PersonalProfile",
    "Position",
    "Qualification",
    "QualificationSnapshot",
    "QualificationStatus",
    "RecusalRelation",
    "RecusalTargetType",
    "Rejection",
    "RejectionCode",
    "RevokeResult",
    "Role",
    "RosterError",
    "RosterService",
    "SchedulingPolicy",
    "Shift",
    "StateError",
    "SwapResult",
    "TimeWindow",
    "TrainingRecord",
    "Viewer",
    "Volunteer",
    "covers",
    "gap_minutes",
    "local_window",
    "overlaps",
]
