"""可解释的排班决定。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class RejectionCode(str, Enum):
    ROLE_NOT_QUALIFIED = "ROLE_NOT_QUALIFIED"
    QUALIFICATION_REVOKED = "QUALIFICATION_REVOKED"
    TRAINING_MISSING = "TRAINING_MISSING"
    TRAINING_EXPIRED = "TRAINING_EXPIRED"
    RECUSAL_CONFLICT = "RECUSAL_CONFLICT"
    AVAILABILITY_GAP = "AVAILABILITY_GAP"
    SHIFT_OVERLAP = "SHIFT_OVERLAP"
    REST_INTERVAL_VIOLATION = "REST_INTERVAL_VIOLATION"
    HEADCOUNT_FULL = "HEADCOUNT_FULL"
    NO_ELIGIBLE_CANDIDATE = "NO_ELIGIBLE_CANDIDATE"


@dataclass(frozen=True)
class Rejection:
    """一条拒绝原因：机器可读编码 + 人可读说明 + 结构化细节。"""

    code: RejectionCode
    message: str
    details: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    """一次排班评估的结论；拒绝时列出全部原因，批准时记录被放宽的规则。"""

    approved: bool
    reasons: tuple[Rejection, ...] = ()
    overrides: tuple[str, ...] = ()

    @property
    def codes(self) -> tuple[RejectionCode, ...]:
        return tuple(r.code for r in self.reasons)

    def explain(self) -> str:
        if self.approved:
            if self.overrides:
                return "批准（放宽规则：" + "、".join(self.overrides) + "）"
            return "批准"
        return "拒绝：" + "；".join(r.message for r in self.reasons)
