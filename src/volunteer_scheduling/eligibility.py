"""纯领域规则：把"这个人能不能上这个岗"拆成可逐条解释的检查。

每个检查要么返回 None（通过），要么返回一条结构化原因。汇总检查
:func:`evaluate_candidate` 会收集**全部**未通过项，而不是遇到第一条就停止，
这样拒绝决定一次性说明所有问题（培训过期 *且* 存在回避 *且* 时段不符……）。
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Iterable

from . import errors as E
from .models import (
    Assignment,
    Availability,
    Position,
    Qualification,
    QualStatus,
    TimeWindow,
    windows_overlap,
)


def _contains(av_date: str, av_start: int, av_end: int,
              w: TimeWindow) -> bool:
    """可用时段是否完整覆盖班次窗口（端点相接允许；跨午夜也成立）。"""
    from .models import _absolute_minutes

    s = _absolute_minutes(w.local_date, w.start_minutes)
    e = _absolute_minutes(w.local_date, w.end_minutes)
    as_, ae = _absolute_minutes(av_date, av_start), _absolute_minutes(av_date, av_end)
    return as_ <= s and e <= ae


def check_role(qualification: Qualification | None, position: Position) -> dict | None:
    if qualification is None:
        return E.reason(
            E.QUALIFICATION_MISSING,
            f"该志愿者没有排班所需的资格记录",
            required_role=position.required_role,
        )
    if qualification.role != position.required_role:
        return E.reason(
            E.ROLE_NOT_ALLOWED,
            f"岗位要求 {position.required_role} 角色，志愿者为 {qualification.role} 角色",
            required_role=position.required_role,
            actual_role=qualification.role,
        )
    return None


def check_qualification_status(qualification: Qualification | None) -> dict | None:
    if qualification is None:
        return None  # 已由 check_role 报告
    if qualification.status == QualStatus.REVOKED.value:
        return E.reason(
            E.QUALIFICATION_REVOKED,
            "该志愿者的资格已被撤销，不能排班",
        )
    return None


def check_training(qualification: Qualification | None, today: str) -> dict | None:
    """安全培训必须存在且在有效期内（valid_until 含当日）。"""
    if qualification is None or qualification.status == QualStatus.REVOKED.value:
        return None
    valid_until = qualification.training_valid_until
    if not valid_until:
        return E.reason(
            E.TRAINING_MISSING,
            "该志愿者尚未完成安全培训，不能上岗",
        )
    if valid_until < today:
        return E.reason(
            E.TRAINING_EXPIRED,
            f"安全培训已于 {valid_until} 过期",
            training_valid_until=valid_until,
            today=today,
        )
    return None


def check_recusal(recusal_vendor_ids: set[str], position: Position) -> dict | None:
    if position.vendor_id and position.vendor_id in recusal_vendor_ids:
        return E.reason(
            E.RECUSAL_CONFLICT,
            f"该志愿者与岗位关联供应商 {position.vendor_id} 存在回避关系",
            vendor_id=position.vendor_id,
        )
    return None


def check_availability(availabilities: Iterable[Availability],
                       position: Position) -> dict | None:
    w = position.window
    covered = any(_contains(a.local_date, a.start_minutes, a.end_minutes, w)
                  for a in availabilities)
    if not covered:
        return E.reason(
            E.OUTSIDE_AVAILABILITY,
            "该志愿者的可用时段不能完整覆盖该班次",
            shift=w.to_dict(),
        )
    return None


def check_rest_and_overlap(existing: Iterable[Assignment], position: Position,
                           ignore_assignment_id: str | None = None) -> list[dict]:
    """检查与本人其它占用中班次的时间重叠和最小休息间隔。

    - 时间轴相交 => 重复占用（SHIFT_OVERLAP）。
    - 相邻但间隔不足岗位要求 => REST_INTERVAL_TOO_SHORT。
    跨午夜窗口在连续分钟轴上比较，因此 23:00-次日02:00 与次日 06:00 的班次
    会被正确识别为仅间隔 4 小时。
    """
    reasons: list[dict] = []
    w = position.window
    for a in existing:
        if a.id == ignore_assignment_id:
            continue
        aw = a.window
        if windows_overlap(w.local_date, w.start_minutes, w.end_minutes,
                           aw.local_date, aw.start_minutes, aw.end_minutes):
            reasons.append(E.reason(
                E.SHIFT_OVERLAP,
                f"与本人已占用班次 {a.id} 时间重叠",
                conflicting_assignment_id=a.id,
                occupied=aw.to_dict(),
            ))
            continue
        # 两者不重叠：计算 a 在前或 w 在前两种情况下的间隔。
        from .models import _absolute_minutes
        ws, we = (_absolute_minutes(w.local_date, w.start_minutes),
                  _absolute_minutes(w.local_date, w.end_minutes))
        als, ale = (_absolute_minutes(aw.local_date, aw.start_minutes),
                    _absolute_minutes(aw.local_date, aw.end_minutes))
        if ale <= ws:
            gap = ws - ale
        elif we <= als:
            gap = als - we
        else:
            continue
        if gap < position.min_rest_minutes:
            reasons.append(E.reason(
                E.REST_INTERVAL_TOO_SHORT,
                f"与班次 {a.id} 之间仅休息 {gap} 分钟，"
                f"少于要求的 {position.min_rest_minutes} 分钟",
                conflicting_assignment_id=a.id,
                gap_minutes=gap,
                required_minutes=position.min_rest_minutes,
            ))
    return reasons


def evaluate_candidate(
    *,
    qualification: Qualification | None,
    recusal_vendor_ids: set[str],
    availabilities: list[Availability],
    existing_assignments: list[Assignment],
    position: Position,
    today: str,
    ignore_assignment_id: str | None = None,
) -> list[dict[str, Any]]:
    """汇总一个候选人针对某岗位的全部未通过原因（空列表 = 可排班）。"""
    reasons: list[dict[str, Any]] = []
    for r in (
        check_role(qualification, position),
        check_qualification_status(qualification),
        check_training(qualification, today),
        check_recusal(recusal_vendor_ids, position),
        check_availability(availabilities, position),
    ):
        if r:
            reasons.append(r)
    # 资格类硬伤已存在时，时间检查仍然照算，一并返回以便解释。
    reasons.extend(check_rest_and_overlap(
        existing_assignments, position, ignore_assignment_id))
    return reasons


def headcount_gaps(positions: list[Position],
                   assignments: list[Assignment]) -> list[dict[str, Any]]:
    """返回人数不足的岗位清单（确认排班与紧急补位时使用）。"""
    gaps: list[dict[str, Any]] = []
    by_position: dict[str, int] = {}
    for a in assignments:
        by_position[a.position_id] = by_position.get(a.position_id, 0) + 1
    for p in positions:
        filled = by_position.get(p.id, 0)
        if filled < p.required_headcount:
            gaps.append({
                "position_id": p.id,
                "title": p.title,
                "required": p.required_headcount,
                "filled": filled,
                "short": p.required_headcount - filled,
            })
    return gaps


def today_local() -> str:
    return dt.date.today().isoformat()
