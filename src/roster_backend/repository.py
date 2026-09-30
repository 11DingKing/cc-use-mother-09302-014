"""内存仓储：保存领域实体与审计事件。"""
from __future__ import annotations

from .models import Assignment, Event, Position, Shift, Volunteer


class InMemoryRepository:
    """按编号索引的内存仓储，便于测试与演示。"""

    def __init__(self) -> None:
        self.volunteers: dict[str, Volunteer] = {}
        self.positions: dict[str, Position] = {}
        self.shifts: dict[str, Shift] = {}
        self.assignments: dict[str, Assignment] = {}
        self.events: list[Event] = []
        self._sequences: dict[str, int] = {}

    def next_id(self, prefix: str) -> str:
        """生成确定性的顺序编号。"""
        self._sequences[prefix] = self._sequences.get(prefix, 0) + 1
        return f"{prefix}-{self._sequences[prefix]:04d}"
