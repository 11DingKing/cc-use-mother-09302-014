"""演示大型校园活动开场前的排班处置流程（对应领域场景）。"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from roster_backend import PersonalProfile, Role, RosterService, Viewer
from roster_backend.models import RecusalTargetType
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 9, 30, 12, 0, tzinfo=TZ)
DAY1 = date(2026, 10, 1)
DAY2 = date(2026, 10, 2)


def trained(service: RosterService, vid: str) -> None:
    service.record_training(
        vid,
        "safety",
        completed_at=datetime(2026, 9, 1, 9, 0, tzinfo=TZ),
        valid_until=datetime(2026, 12, 31, 23, 59, tzinfo=TZ),
        now=NOW,
    )


def main() -> None:
    service = RosterService(tz_name="Asia/Shanghai")
    steps: list[dict] = []

    # 岗位与班次：检票岗需家长+安全培训并关联餐饮供应商；引导岗跨午夜
    service.create_position("检票岗", Role.PARENT, required_trainings=("safety",), vendor_id="VEN-FOOD", position_id="P-GATE", now=NOW)
    service.create_position("引导岗", Role.TEACHER, required_trainings=("safety",), position_id="P-GUIDE", now=NOW)
    service.create_shift("P-GATE", DAY1, time(8, 0), time(12, 0), headcount=1, shift_id="S-GATE", now=NOW)
    service.create_shift("P-GUIDE", DAY1, time(22, 0), time(2, 0), headcount=1, shift_id="S-NIGHT", now=NOW)

    # 王芳：有空闲时间，但未完成安全培训，且与餐饮供应商存在回避关系
    service.register_volunteer(
        "王芳",
        [Role.PARENT],
        volunteer_id="V-WANG",
        profile=PersonalProfile(phone="138****0001", id_number="310***********01", emergency_contact="139****0002", notes="对花粉过敏"),
        now=NOW,
    )
    service.add_availability("V-WANG", DAY1, time(7, 0), time(13, 0), now=NOW)
    service.declare_recusal("V-WANG", RecusalTargetType.VENDOR, "VEN-FOOD", "配偶在该餐饮供应商任职", now=NOW)
    rejected = service.assign("V-WANG", "S-GATE", now=NOW)
    steps.append({"环节": "王芳报名检票岗", "结果": rejected.decision.explain()})

    # 陈强：合格家长，确认排班并冻结资格快照
    service.register_volunteer("陈强", [Role.PARENT], volunteer_id="V-CHEN", now=NOW)
    trained(service, "V-CHEN")
    service.add_availability("V-CHEN", DAY1, time(7, 0), time(13, 0), now=NOW)
    confirmed = service.assign("V-CHEN", "S-GATE", now=NOW)
    steps.append({
        "环节": "陈强确认检票岗",
        "结果": confirmed.decision.explain(),
        "冻结角色": [r.value for r in confirmed.assignment.snapshot.active_roles],
        "占用": service.shift_status("S-GATE")["filled"],
    })

    # 陈强临时请假 → 岗位缺口 → 紧急补位周洁
    leave = service.request_leave(confirmed.assignment.assignment_id, "家中急事", now=NOW)
    steps.append({"环节": "陈强请假", "岗位缺口": leave.gap})
    service.register_volunteer("周洁", [Role.PARENT], volunteer_id="V-ZHOU", now=NOW)
    trained(service, "V-ZHOU")
    service.add_availability("V-ZHOU", DAY1, time(7, 0), time(13, 0), now=NOW)
    backfill = service.emergency_backfill("S-GATE", volunteer_id="V-ZHOU", now=NOW)
    steps.append({"环节": "紧急补位周洁", "结果": backfill.decision.explain(), "占用": service.shift_status("S-GATE")["filled"]})

    # 李敏：跨午夜引导岗确认后，清晨续班因休息间隔不足被拒
    service.register_volunteer("李敏", [Role.TEACHER], volunteer_id="V-LI", now=NOW)
    trained(service, "V-LI")
    service.add_availability("V-LI", DAY1, time(20, 0), time(3, 0), now=NOW)  # 跨午夜可用时段
    service.add_availability("V-LI", DAY2, time(5, 0), time(9, 0), now=NOW)
    service.assign("V-LI", "S-NIGHT", now=NOW)
    night = service.shift_status("S-NIGHT")
    steps.append({"环节": "李敏确认跨午夜引导岗", "班次窗口": f"{night['start']} ~ {night['end']}"})
    service.create_shift("P-GUIDE", DAY2, time(6, 0), time(8, 0), headcount=1, shift_id="S-DAWN", now=NOW)
    dawn = service.assign("V-LI", "S-DAWN", now=NOW)
    steps.append({"环节": "李敏续排清晨班", "结果": dawn.decision.explain()})

    # 周洁资格被撤销 → 级联释放占用，缺口再现
    revoked = service.revoke_qualification("V-ZHOU", Role.PARENT, "复核发现培训证明无效", now=NOW)
    steps.append({
        "环节": "撤销周洁家长资格",
        "释放占用": list(revoked.released_assignment_ids),
        "检票岗占用": service.shift_status("S-GATE")["filled"],
    })

    # 个人资料按权限隔离
    coordinator = service.profile_view("V-WANG", Viewer(viewer_id="V-COORD", is_coordinator=True))
    peer = service.profile_view("V-WANG", Viewer(viewer_id="V-CHEN"))
    steps.append({
        "环节": "个人资料权限隔离",
        "协调员可见字段": sorted(coordinator.keys()),
        "其他志愿者可见字段": sorted(peer.keys()),
    })

    print(json.dumps({"时区": "Asia/Shanghai", "步骤": steps}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
