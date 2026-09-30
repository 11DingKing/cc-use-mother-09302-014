"""个人资料按权限隔离。"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .models import ROLE_LABELS, TRAINING_LABELS, Volunteer


class AccessLevel(str, Enum):
    COORDINATOR = "coordinator"  # 志愿者协调员：全部字段
    SELF = "self"  # 本人：全部字段
    PEER = "peer"  # 其他志愿者：仅姓名与角色
    PUBLIC = "public"  # 未登录/公众：仅姓名


@dataclass(frozen=True)
class Viewer:
    """查看者身份：协调员或某个志愿者编号。"""

    viewer_id: str | None = None
    is_coordinator: bool = False


def access_level_for(viewer: Viewer, volunteer_id: str) -> AccessLevel:
    if viewer.is_coordinator:
        return AccessLevel.COORDINATOR
    if viewer.viewer_id is not None and viewer.viewer_id == volunteer_id:
        return AccessLevel.SELF
    if viewer.viewer_id is not None:
        return AccessLevel.PEER
    return AccessLevel.PUBLIC


def profile_view(volunteer: Volunteer, viewer: Viewer) -> dict:
    """按查看者权限裁剪后的个人资料视图。"""
    level = access_level_for(viewer, volunteer.volunteer_id)
    view: dict = {
        "volunteer_id": volunteer.volunteer_id,
        "access_level": level.value,
        "name": volunteer.name,
    }
    if level is AccessLevel.PUBLIC:
        return view
    view["roles"] = [ROLE_LABELS[role] for role in volunteer.active_roles()]
    if level is AccessLevel.PEER:
        return view
    view["profile"] = {
        "phone": volunteer.profile.phone,
        "id_number": volunteer.profile.id_number,
        "emergency_contact": volunteer.profile.emergency_contact,
        "notes": volunteer.profile.notes,
    }
    view["availability"] = [
        {"start": window.start.isoformat(), "end": window.end.isoformat()}
        for window in volunteer.availability
    ]
    view["trainings"] = [
        {
            "training_type": record.training_type,
            "label": TRAINING_LABELS.get(record.training_type, record.training_type),
            "completed_at": record.completed_at.isoformat(),
            "valid_until": record.valid_until.isoformat(),
        }
        for record in volunteer.trainings
    ]
    view["recusals"] = [
        {"target_type": recusal.target_type.value, "target_id": recusal.target_id, "reason": recusal.reason}
        for recusal in volunteer.recusals
    ]
    return view
