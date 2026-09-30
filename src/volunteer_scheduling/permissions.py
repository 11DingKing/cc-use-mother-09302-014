"""按权限隔离个人资料。

身份模型
--------
- ``coordinator`` 志愿者协调员：可查看完整资料（含手机号、证件号），可执行排班。
- ``volunteer`` 志愿者本人：只能查看**自己**的完整资料，查看他人时仅得到
  姓名/角色等排班所必需的最小字段。
- 任何人都看不到与排班无关的敏感字段，除非具备上述授权。

敏感字段采用白名单隔离，而不是黑名单：新增敏感字段时默认不会泄露。
"""
from __future__ import annotations

from typing import Any

from .errors import Forbidden
from .models import Person

# 被视为敏感、默认隐藏的 profile 键。
SENSITIVE_KEYS = {"phone", "id_card", "email", "emergency_contact", "address"}

# 角色 -> 该角色可查看的他人资料范围
PUBLIC_PROFILE_KEYS = ("volunteer_years", "team")


class Principal:
    """发起请求的身份。"""

    def __init__(self, kind: str, person_id: str | None = None):
        # kind: "coordinator" | "volunteer"
        if kind not in ("coordinator", "volunteer"):
            raise ValueError(f"未知身份类型：{kind}")
        self.kind = kind
        self.person_id = person_id

    @property
    def is_coordinator(self) -> bool:
        return self.kind == "coordinator"

    def can_manage_schedule(self) -> bool:
        return self.is_coordinator

    def can_view_full_profile(self, person_id: str) -> bool:
        return self.is_coordinator or self.person_id == person_id

    def require_coordinator(self) -> None:
        if not self.is_coordinator:
            raise Forbidden("仅志愿者协调员可以执行该操作")


def redact_profile(principal: Principal, person: Person) -> dict[str, Any]:
    """根据查看者权限裁剪个人资料。"""
    if principal.can_view_full_profile(person.id):
        return dict(person.profile)
    return {k: v for k, v in person.profile.items() if k in PUBLIC_PROFILE_KEYS}


def person_to_view(principal: Principal, person: Person) -> dict[str, Any]:
    """返回按权限隔离后的人员视图。"""
    return {
        "id": person.id,
        "role": person.role,
        "name": person.name,
        "profile": redact_profile(principal, person),
    }


def require_full_access(principal: Principal, person_id: str) -> None:
    if not principal.can_view_full_profile(person_id):
        raise Forbidden("无权查看该志愿者的个人资料")
