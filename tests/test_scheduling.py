"""排班后端的领域用例测试。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from volunteer_scheduling import (  # noqa: E402
    Principal,
    Rejection,
    Repository,
    SchedulingService,
    connect,
)
from volunteer_scheduling import errors as E  # noqa: E402

COORD = Principal("coordinator")
TODAY = "2026-09-30"
EVENT_DAY = "2026-10-01"


def make_service(today: str = TODAY) -> SchedulingService:
    repo = Repository(connect(":memory:"))
    return SchedulingService(repo, today=today)


def setup_event(svc: SchedulingService) -> str:
    svc.create_event(COORD, "ev1", "校园开放日", "Asia/Shanghai")
    return "ev1"


class EligibilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = make_service()
        setup_event(self.svc)

    def test_parent_with_free_time_but_no_training_and_recusal_is_rejected(self) -> None:
        """开场场景：家长有空闲，却没培训 + 与供应商回避，两条原因都要给出。"""
        svc = self.svc
        svc.register_person(COORD, "p1", "parent", "王芳")
        svc.set_qualification(COORD, "p1", None)                 # 未完成安全培训
        svc.add_recusal(COORD, "p1", "vendor-catering", "家属经营")
        svc.add_availability(COORD, "p1", EVENT_DAY, 18 * 60, 23 * 60)
        # 岗位：供应商餐饮岗，晚间值守
        pos = svc.add_position(
            COORD, "ev1", "入口值守", "parent", 1,
            EVENT_DAY, "19:00", "22:00", vendor_id="vendor-catering")

        with self.assertRaises(Rejection) as ctx:
            svc.propose_assignment(COORD, "ev1", pos.id, "p1")
        codes = {r["code"] for r in ctx.exception.reasons}
        self.assertIn(E.TRAINING_MISSING, codes)
        self.assertIn(E.RECUSAL_CONFLICT, codes)
        # 时段其实覆盖，因此不应报时段问题
        self.assertNotIn(E.OUTSIDE_AVAILABILITY, codes)

    def test_training_expired_is_explained(self) -> None:
        svc = self.svc
        svc.register_person(COORD, "t1", "teacher", "李老师")
        svc.set_qualification(COORD, "t1", "2026-09-01")  # 已过期
        svc.add_availability(COORD, "t1", EVENT_DAY, 0, 1440)
        pos = svc.add_position(COORD, "ev1", "舞台协调", "teacher", 1,
                               EVENT_DAY, "09:00", "11:00")
        with self.assertRaises(Rejection) as ctx:
            svc.propose_assignment(COORD, "ev1", pos.id, "t1")
        self.assertIn(E.TRAINING_EXPIRED, {r["code"] for r in ctx.exception.reasons})

    def test_role_mismatch(self) -> None:
        svc = self.svc
        svc.register_person(COORD, "s1", "student", "小陈")
        svc.set_qualification(COORD, "s1", "2026-12-31")
        svc.add_availability(COORD, "s1", EVENT_DAY, 0, 1440)
        pos = svc.add_position(COORD, "ev1", "教师专属岗", "teacher", 1,
                               EVENT_DAY, "09:00", "11:00")
        with self.assertRaises(Rejection) as ctx:
            svc.propose_assignment(COORD, "ev1", pos.id, "s1")
        self.assertIn(E.ROLE_NOT_ALLOWED, {r["code"] for r in ctx.exception.reasons})

    def test_availability_must_cover_whole_shift(self) -> None:
        svc = self.svc
        svc.register_person(COORD, "p2", "parent", "赵敏")
        svc.set_qualification(COORD, "p2", "2026-12-31")
        # 只空闲到 20:00，无法覆盖到 22:00
        svc.add_availability(COORD, "p2", EVENT_DAY, 18 * 60, 20 * 60)
        pos = svc.add_position(COORD, "ev1", "入口值守", "parent", 1,
                               EVENT_DAY, "19:00", "22:00")
        with self.assertRaises(Rejection) as ctx:
            svc.propose_assignment(COORD, "ev1", pos.id, "p2")
        self.assertIn(E.OUTSIDE_AVAILABILITY, {r["code"] for r in ctx.exception.reasons})


class RestIntervalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = make_service()
        setup_event(self.svc)

    def _ready_person(self, pid: str, role: str = "teacher") -> None:
        svc = self.svc
        svc.register_person(COORD, pid, role, pid)
        svc.set_qualification(COORD, pid, "2026-12-31")
        # 全天可用（含跨午夜）
        svc.add_availability(COORD, pid, EVENT_DAY, 0, 48 * 60)

    def test_cross_midnight_rest_interval_enforced_local_time(self) -> None:
        """跨午夜班次按本地时区计算：23:00-次日02:00 与次日 06:00 仅隔 4 小时。"""
        svc = self.svc
        self._ready_person("t1")
        # 跨午夜岗：23:00 -> 次日 02:00（end=1560），要求休息 8 小时
        night = svc.add_position(
            COORD, "ev1", "夜间值守", "teacher", 1,
            EVENT_DAY, "23:00", "02:00", min_rest_minutes=8 * 60)
        svc.propose_assignment(COORD, "ev1", night.id, "t1")

        # 次日 06:00-08:00 的岗位：local_date 为 10-02
        morning = svc.add_position(
            COORD, "ev1", "晨间引导", "teacher", 1,
            "2026-10-02", "06:00", "08:00", min_rest_minutes=8 * 60)
        with self.assertRaises(Rejection) as ctx:
            svc.propose_assignment(COORD, "ev1", morning.id, "t1")
        reasons = ctx.exception.reasons
        self.assertEqual([r["code"] for r in reasons], [E.REST_INTERVAL_TOO_SHORT])
        gap = reasons[0]["detail"]["gap_minutes"]
        self.assertEqual(gap, 4 * 60)

    def test_enough_rest_after_cross_midnight_ok(self) -> None:
        svc = self.svc
        self._ready_person("t1")
        night = svc.add_position(COORD, "ev1", "夜间值守", "teacher", 1,
                                 EVENT_DAY, "23:00", "02:00",
                                 min_rest_minutes=8 * 60)
        svc.propose_assignment(COORD, "ev1", night.id, "t1")
        morning = svc.add_position(COORD, "ev1", "晨间引导", "teacher", 1,
                                   "2026-10-02", "11:00", "12:00",
                                   min_rest_minutes=8 * 60)
        svc.propose_assignment(COORD, "ev1", morning.id, "t1")  # 不抛异常


class ConfirmationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = make_service()
        setup_event(self.svc)

    def _ready(self, pid: str, role: str = "parent", vendor: str | None = None) -> None:
        svc = self.svc
        svc.register_person(COORD, pid, role, pid,
                            {"phone": "13900000000", "volunteer_years": 2})
        svc.set_qualification(COORD, pid, "2026-12-31")
        svc.add_availability(COORD, pid, EVENT_DAY, 0, 1440)
        if vendor:
            svc.add_recusal(COORD, pid, vendor)

    def test_confirm_freezes_qualification_snapshot(self) -> None:
        svc = self.svc
        pos = svc.add_position(COORD, "ev1", "入口", "parent", 1,
                               EVENT_DAY, "09:00", "12:00")
        self._ready("p1")
        svc.propose_assignment(COORD, "ev1", pos.id, "p1")
        result = svc.confirm_event(COORD, "ev1")
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["snapshot_version"], 1)

        # 排班已冻结，且记录了资格快照
        asg = svc.repo.active_assignments_for_event("ev1")[0]
        self.assertEqual(asg.status, "confirmed")
        self.assertIsNotNone(asg.snapshot["qualification"])
        snap = svc.repo.get_snapshot("ev1")
        self.assertEqual(len(snap["qualifications"]), 1)

    def test_confirm_blocked_by_headcount_shortage(self) -> None:
        """临时换人导致岗位缺口时，确认必须被拒绝并指出缺口。"""
        svc = self.svc
        svc.add_position(COORD, "ev1", "入口", "parent", 2,
                         EVENT_DAY, "09:00", "12:00")
        self._ready("p1")
        pos = svc.repo.positions_for_event("ev1")[0]
        svc.propose_assignment(COORD, "ev1", pos.id, "p1")  # 只排了 1/2
        with self.assertRaises(Rejection) as ctx:
            svc.confirm_event(COORD, "ev1")
        codes = {r["code"] for r in ctx.exception.reasons}
        self.assertIn(E.HEADCOUNT_SHORT, codes)
        gap = next(r["detail"] for r in ctx.exception.reasons
                   if r["code"] == E.HEADCOUNT_SHORT)
        self.assertEqual((gap["required"], gap["filled"], gap["short"]), (2, 1, 1))

    def test_cannot_modify_positions_after_confirm(self) -> None:
        svc = self.svc
        self._ready("p1")
        svc.add_position(COORD, "ev1", "入口", "parent", 1,
                         EVENT_DAY, "09:00", "12:00")
        pos = svc.repo.positions_for_event("ev1")[0]
        svc.propose_assignment(COORD, "ev1", pos.id, "p1")
        svc.confirm_event(COORD, "ev1")
        with self.assertRaises(Rejection):
            svc.add_position(COORD, "ev1", "新增岗", "parent", 1,
                             EVENT_DAY, "13:00", "14:00")


class SwapLeaveRevokeFillTest(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = make_service()
        setup_event(self.svc)
        svc = self.svc
        pos = svc.add_position(COORD, "ev1", "入口", "parent", 2,
                               EVENT_DAY, "19:00", "22:00",
                               vendor_id="vendor-catering")
        self.pos_id = pos.id
        for pid in ("p1", "p2", "p3"):
            svc.register_person(COORD, pid, "parent", pid)
            svc.set_qualification(COORD, pid, "2026-12-31")
            svc.add_availability(COORD, pid, EVENT_DAY, 18 * 60, 23 * 60)
        # p3 与供应商有回避，不能补这个岗
        svc.add_recusal(COORD, "p3", "vendor-catering")
        svc.propose_assignment(COORD, "ev1", self.pos_id, "p1")
        svc.propose_assignment(COORD, "ev1", self.pos_id, "p2")
        svc.confirm_event(COORD, "ev1")

    def _assignment_of(self, person_id: str) -> str:
        return next(a.id for a in self.svc.repo.active_assignments_for_event("ev1")
                    if a.person_id == person_id)

    def test_swap_to_ineligible_person_keeps_original_occupancy(self) -> None:
        """换给不合格者（回避）必须整体回滚，原排班保留、不产生缺口。"""
        svc = self.svc
        asg = self._assignment_of("p1")
        with self.assertRaises(Rejection) as ctx:
            svc.swap_assignment(COORD, asg, "p3")
        self.assertIn(E.RECUSAL_CONFLICT,
                      {r["code"] for r in ctx.exception.reasons})
        # p1 仍然在岗
        self.assertIsNotNone(svc.repo.get_assignment(asg))
        self.assertEqual(svc.repo.get_assignment(asg).status, "confirmed")
        self.assertEqual(svc.roster_view(COORD, "ev1")["gaps"], [])

    def test_swap_then_fill_sync_occupancy(self) -> None:
        """合格换班：旧占用释放、新占用建立，人数不变；补位恢复缺口。"""
        svc = self.svc
        asg = self._assignment_of("p1")
        # p3 不能补此岗（回避），因此先解除其回避以制造一个合格补位人选不合适，
        # 改为：直接把 p1 的班换给……没有第四个合格的人。给 p3 换个无供应商岗位场景，
        # 这里验证换给一个新合格者。
        svc.register_person(COORD, "p4", "parent", "p4")
        svc.set_qualification(COORD, "p4", "2026-12-31")
        svc.add_availability(COORD, "p4", EVENT_DAY, 18 * 60, 23 * 60)
        new_a = svc.swap_assignment(COORD, asg, "p4")
        self.assertEqual(new_a.person_id, "p4")
        self.assertEqual(new_a.status, "confirmed")
        self.assertEqual(svc.repo.get_assignment(asg).status, "released")
        self.assertEqual(svc.roster_view(COORD, "ev1")["gaps"], [])

    def test_leave_opens_gap_and_emergency_fill_closes_it(self) -> None:
        svc = self.svc
        asg = self._assignment_of("p2")
        report = svc.request_leave(Principal("volunteer", "p2"), asg)
        self.assertIsNotNone(report["opened_gap"])
        self.assertEqual(report["opened_gap"]["short"], 1)

        # 紧急补位：p3 因回避落选，p4 合格应被选中
        svc.register_person(COORD, "p4", "parent", "p4")
        svc.set_qualification(COORD, "p4", "2026-12-31")
        svc.add_availability(COORD, "p4", EVENT_DAY, 18 * 60, 23 * 60)
        filled = svc.emergency_fill(COORD, "ev1", self.pos_id)
        self.assertEqual(filled.person_id, "p4")
        self.assertEqual(svc.roster_view(COORD, "ev1")["gaps"], [])

    def test_emergency_fill_explains_when_no_candidate(self) -> None:
        svc = self.svc
        asg = self._assignment_of("p2")
        svc.request_leave(COORD, asg)
        # 只有 p3 空闲但她回避供应商 -> 无可补之人，原因可解释
        with self.assertRaises(Rejection) as ctx:
            svc.emergency_fill(COORD, "ev1", self.pos_id)
        r = ctx.exception.reasons[0]
        self.assertEqual(r["code"], E.NO_ELIGIBLE_CANDIDATE)
        cand = r["detail"]["candidates"]
        self.assertEqual(cand[0]["person_id"], "p3")
        self.assertIn(E.RECUSAL_CONFLICT,
                      {x["code"] for x in cand[0]["reasons"]})

    def test_emergency_fill_noop_when_no_gap(self) -> None:
        with self.assertRaises(Rejection) as ctx:
            self.svc.emergency_fill(COORD, "ev1", self.pos_id)
        self.assertEqual(ctx.exception.reasons[0]["code"], E.POSITION_FULL)

    def test_revoke_qualification_releases_occupancy_and_reports_gaps(self) -> None:
        """资格撤销必须同步释放本人所有占用，并暴露缺口。"""
        svc = self.svc
        result = svc.revoke_qualification(COORD, "p1")
        self.assertEqual(len(result["released_assignments"]), 1)
        self.assertTrue(result["gaps"])
        # 撤销后不能再被排上：用草稿活动验证资格撤销拦截
        svc = self.svc
        svc.create_event(COORD, "ev2", "第二场", "Asia/Shanghai")
        pos2 = svc.add_position(COORD, "ev2", "x", "parent", 1,
                                EVENT_DAY, "19:00", "22:00")
        with self.assertRaises(Rejection) as ctx2:
            svc.propose_assignment(COORD, "ev2", pos2.id, "p1")
        self.assertIn(E.QUALIFICATION_REVOKED,
                      {r["code"] for r in ctx2.exception.reasons})

    def test_volunteer_can_only_request_own_leave(self) -> None:
        svc = self.svc
        asg = self._assignment_of("p2")
        with self.assertRaises(E.Forbidden):
            svc.request_leave(Principal("volunteer", "p1"), asg)

    def test_volunteer_cannot_schedule(self) -> None:
        with self.assertRaises(E.Forbidden):
            self.svc.add_position(Principal("volunteer", "p1"), "ev1", "x",
                                 "parent", 1, EVENT_DAY, "09:00", "10:00")


class ProfileIsolationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = make_service()
        svc = self.svc
        svc.register_person(COORD, "p1", "parent", "王芳",
                            {"phone": "139xxxx", "id_card": "110...",
                             "volunteer_years": 3})

    def test_coordinator_sees_full_profile(self) -> None:
        view = self.svc.person_view(Principal("coordinator"), "p1")
        self.assertIn("phone", view["profile"])
        self.assertIn("id_card", view["profile"])

    def test_self_sees_full_profile(self) -> None:
        view = self.svc.person_view(Principal("volunteer", "p1"), "p1")
        self.assertIn("phone", view["profile"])

    def test_other_volunteer_sees_redacted_profile(self) -> None:
        view = self.svc.person_view(Principal("volunteer", "p2"), "p1")
        self.assertNotIn("phone", view["profile"])
        self.assertNotIn("id_card", view["profile"])
        self.assertIn("volunteer_years", view["profile"])
        # 他人不暴露资格/回避等明细
        self.assertNotIn("qualification", view)


if __name__ == "__main__":
    unittest.main()
