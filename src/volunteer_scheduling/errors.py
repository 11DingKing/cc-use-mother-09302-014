"""可解释的排班拒绝决定。

所有违反领域约束（培训、回避、可用时段、休息间隔、人数等）的情况都以
结构化 ``Violation`` 列表返回，而不是一句模糊的报错，便于协调员向志愿者解释。
"""
from __future__ import annotations

from typing import Any

# 资格类
TRAINING_MISSING = "TRAINING_MISSING"
TRAINING_EXPIRED = "TRAINING_EXPIRED"
QUALIFICATION_REVOKED = "QUALIFICATION_REVOKED"
ROLE_NOT_ALLOWED = "ROLE_NOT_ALLOWED"
# 回避 / 时段类
RECUSAL_CONFLICT = "RECUSAL_CONFLICT"
OUTSIDE_AVAILABILITY = "OUTSIDE_AVAILABILITY"
# 排班结构类
REST_INTERVAL_TOO_SHORT = "REST_INTERVAL_TOO_SHORT"
HEADCOUNT_SHORT = "HEADCOUNT_SHORT"
DUPLICATE_ASSIGNMENT = "DUPLICATE_ASSIGNMENT"
SHIFT_TIME_INVALID = "SHIFT_TIME_INVALID"
SHIFT_OVERLAP = "SHIFT_OVERLAP"
POSITION_FULL = "POSITION_FULL"
# 换班 / 补位类
QUALIFICATION_MISSING = "QUALIFICATION_MISSING"
ASSIGNMENT_NOT_CHANGEABLE = "ASSIGNMENT_NOT_CHANGEABLE"
NO_ELIGIBLE_CANDIDATE = "NO_ELIGIBLE_CANDIDATE"
EVENT_ALREADY_CONFIRMED = "EVENT_ALREADY_CONFIRMED"
EVENT_NOT_CONFIRMED = "EVENT_NOT_CONFIRMED"


def reason(code: str, message: str, **detail: Any) -> dict[str, Any]:
    """构造一条结构化拒绝原因。"""
    item = {"code": code, "message": message}
    if detail:
        item["detail"] = {k: v for k, v in detail.items() if v is not None}
    return item


class Rejection(Exception):
    """一个或多个领域约束未通过，拒绝执行该决定。"""

    def __init__(self, reasons: list[dict[str, Any]]):
        self.reasons = reasons
        messages = "；".join(r["message"] for r in reasons)
        super().__init__(messages)


class NotFound(Exception):
    """资源不存在。"""


class Forbidden(Exception):
    """当前身份无权执行该操作或查看该资料。"""
