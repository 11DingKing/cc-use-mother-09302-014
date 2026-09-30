"""排班后端异常。"""
from __future__ import annotations


class RosterError(Exception):
    """排班后端基础异常。"""


class NotFoundError(RosterError):
    """实体不存在。"""


class StateError(RosterError):
    """实体状态不允许当前操作。"""
