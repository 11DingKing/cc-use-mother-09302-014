"""活动志愿者排班后端。

对外主要导出：

- :class:`~volunteer_scheduling.services.SchedulingService`：排班业务用例。
- :class:`~volunteer_scheduling.repository.Repository` / :func:`connect`：持久化。
- :class:`~volunteer_scheduling.permissions.Principal`：调用身份与资料隔离。
- :class:`~volunteer_scheduling.errors.Rejection`：可解释的拒绝决定。
"""
from __future__ import annotations

from .errors import Forbidden, NotFound, Rejection, reason
from .permissions import Principal
from .repository import Repository, connect
from .services import SchedulingService

__all__ = [
    "SchedulingService",
    "Repository",
    "connect",
    "Principal",
    "Rejection",
    "NotFound",
    "Forbidden",
    "reason",
]
