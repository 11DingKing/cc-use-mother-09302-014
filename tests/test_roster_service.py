"""排班后端服务的回归测试。"""
from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from roster_backend import RejectionCode, Role, RosterService, StateError
from roster_backend.models import AssignmentStatus, QualificationStatus, RecusalTargetType

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 9, 30, 12, 0, tzinfo=TZ)
DAY1 = date(2026, 10, 1)
DAY2 = date(2026, 10, 2)
TRAINED = {
    "completed_at": datetime(2026, 9, 1, 9, 0, tzinfo=TZ),
    "valid_until": datetime(2026, 12, 31, 23, 59, tzinfo=TZ),
}


def make_service() -> RosterService:
    service = RosterService(tz_name="Asia/Shanghai")
    service.create_position(
        "检票岗",
        Role.PARENT,
        required_trainings=("safety",),
        vendor_id="VEN-FOOD",
        position_id="P-GATE",
        now=NOW,
    )
    service.create_position(
        "引导岗", Role.TEACHER, required_trainings=("safety",), position_id="P-GUIDE", now=NOW
    )
    service.create_position("物资岗", Role.STUDENT_ASSISTANT, position_id="P-SUPPLY", now=NOW)
    service.create_shift("P-GATE", DAY1, time(8, 0), time(12, 0), headcount=1, shift_id="S-GATE-1", now=NOW)
    service.create_shift("P-GUIDE", DAY1, time(22, 0), time(2, 0), headcount=1, shift_id="S-GUIDE-1", now=NOW)
    service.create_shift("P-SUPPLY", DAY2, time(8, 0), time(10, 0), headcount=2, shift_id="S-SUPPLY-1", now=NOW)
    return service


def add_parent(
    service: RosterService,
    vid: str,
    name: str,
    *,
    trained: bool = True,
    recuse_vendor: bool = False,
    avail: tuple = ((DAY1, time(7, 0), time(13, 0)),),
) -> None:
    service.register_volunteer(name, [Role.PARENT], volunteer_id=vid, now=NOW)
    if trained:
        service.record_training(vid, "safety", now=NOW, **TRAINED)
    if recuse_vendor:
        service.declare_recusal(vid, RecusalTargetType.VENDOR, "VEN-FOOD", "亲属在该供应商任职", now=NOW)
    for day, start, end in avail:
        service.add_availability(vid, day, start, end, now=NOW)


def add_teacher(service: RosterService, vid: str, name: str, *, avail: tuple) -> None:
    service.register_volunteer(name, [Role.TEACHER], volunteer_id=vid, now=NOW)
    service.record_training(vid, "safety", now=NOW, **TRAINED)
    for day, start, end in avail:
        service.add_availability(vid, day, start, end, now=NOW)


class RejectionExplainTest(unittest.TestCase):
    """场景还原：家长有空闲但未完成安全培训且与供应商回避。"""

    def test_untrained_parent_with_recusal_is_rejected_explainably(self) -> None:
        service = make_service()
        add_parent(service, "V-WANG", "王芳", trained=False, recuse_vendor=True)
        result = service.assign("V-WANG", "S-GATE-1", now=NOW)
        self.assertFalse(result.decision.approved)
        self.assertIsNone(result.assignment)
        self.assertIn(RejectionCode.TRAINING_MISSING, result.decision.codes)
        self.assertIn(RejectionCode.RECUSAL_CONFLICT, result.decision.codes)
        explanation = result.decision.explain()
        self.assertIn("安全培训", explanation)
        self.assertIn("VEN-FOOD", explanation)
        self.assertIn("回避", explanation)
        self.assertEqual(service.shift_status("S-GATE-1")["filled"], 0)

    def test_expired_training_is_rejected(self) -> None:
        service = make_service()
        service.register_volunteer("刘敏", [Role.PARENT], volunteer_id="V-LIU", now=NOW)
        service.record_training(
            "V-LIU",
            "safety",
            completed_at=datetime(2025, 9, 1, 9, 0, tzinfo=TZ),
            valid_until=datetime(2026, 9, 29, 23, 59, tzinfo=TZ),
            now=NOW,
        )
        service.add_availability("V-LIU", DAY1, time(7, 0), time(13, 0), now=NOW)
        result = service.assign("V-LIU", "S-GATE-1", now=NOW)
        self.assertIn(RejectionCode.TRAINING_EXPIRED, result.decision.codes)
        self.assertIn("有效期至", result.decision.explain())

    def test_wrong_role_is_rejected(self) -> None:
        service = make_service()
        add_parent(service, "V-CHEN", "陈强")
        result = service.assign("V-CHEN", "S-GUIDE-1", now=NOW)
        self.assertIn(RejectionCode.ROLE_NOT_QUALIFIED, result.decision.codes)
        self.assertIn("教师", result.decision.explain())


class SnapshotTest(unittest.TestCase):
    def test_confirm_freezes_qualification_snapshot(self) -> None:
        service = make_service()
        add_parent(service, "V-CHEN", "陈强")
        result = service.assign("V-CHEN", "S-GATE-1", now=NOW)
        self.assertTrue(result.decision.approved)
        snapshot = result.assignment.snapshot
        self.assertEqual(snapshot.active_roles, (Role.PARENT,))
        self.assertEqual(len(snapshot.trainings), 1)
        self.assertEqual(snapshot.frozen_at, NOW)
        # 冻结后再登记新培训，快照保持不变
        service.record_training(
            "V-CHEN",
            "first_aid",
            completed_at=datetime(2026, 9, 30, 13, 0, tzinfo=TZ),
            valid_until=datetime(2027, 9, 30, 13, 0, tzinfo=TZ),
            now=NOW,
        )
        self.assertEqual(len(result.assignment.snapshot.trainings), 1)
        self.assertEqual(service.shift_status("S-GATE-1")["filled"], 1)


class HeadcountTest(unittest.TestCase):
    def test_headcount_blocks_extra_assignment(self) -> None:
        service = make_service()
        for vid, name in (("V-S1", "赵一"), ("V-S2", "钱二"), ("V-S3", "孙三")):
            service.register_volunteer(name, [Role.STUDENT_ASSISTANT], volunteer_id=vid, now=NOW)
            service.add_availability(vid, DAY2, time(7, 0), time(11, 0), now=NOW)
        first = service.assign("V-S1", "S-SUPPLY-1", now=NOW)
        second = service.assign("V-S2", "S-SUPPLY-1", now=NOW)
        third = service.assign("V-S3", "S-SUPPLY-1", now=NOW)
        self.assertTrue(first.decision.approved)
        self.assertTrue(second.decision.approved)
        self.assertFalse(third.decision.approved)
        self.assertIn(RejectionCode.HEADCOUNT_FULL, third.decision.codes)
        status = service.shift_status("S-SUPPLY-1")
        self.assertEqual(status["filled"], 2)
        self.assertEqual(status["gap"], 0)


class CrossMidnightRestTest(unittest.TestCase):
    def test_cross_midnight_shift_uses_local_timezone(self) -> None:
        service = make_service()
        status = service.shift_status("S-GUIDE-1")
        self.assertEqual(status["start"], "2026-10-01T22:00:00+08:00")
        self.assertEqual(status["end"], "2026-10-02T02:00:00+08:00")

    def _teacher_on_night_shift(self, service: RosterService) -> None:
        add_teacher(
            service,
            "V-LI",
            "李敏",
            avail=((DAY1, time(20, 0), time(3, 0)), (DAY2, time(5, 0), time(15, 0))),
        )
        result = service.assign("V-LI", "S-GUIDE-1", now=NOW)
        self.assertTrue(result.decision.approved)

    def test_rest_interval_violation_after_cross_midnight_shift(self) -> None:
        service = make_service()
        self._teacher_on_night_shift(service)
        service.create_shift("P-GUIDE", DAY2, time(6, 0), time(8, 0), headcount=1, shift_id="S-GUIDE-2", now=NOW)
        result = service.assign("V-LI", "S-GUIDE-2", now=NOW)
        self.assertFalse(result.decision.approved)
        self.assertIn(RejectionCode.REST_INTERVAL_VIOLATION, result.decision.codes)
        explanation = result.decision.explain()
        self.assertIn("240", explanation)  # 实际间隔 4 小时
        self.assertIn("480", explanation)  # 规定休息 8 小时

    def test_sufficient_rest_is_approved(self) -> None:
        service = make_service()
        self._teacher_on_night_shift(service)
        service.create_shift("P-GUIDE", DAY2, time(12, 0), time(14, 0), headcount=1, shift_id="S-GUIDE-3", now=NOW)
        result = service.assign("V-LI", "S-GUIDE-3", now=NOW)
        self.assertTrue(result.decision.approved)

    def test_overlapping_shift_is_rejected(self) -> None:
        service = make_service()
        self._teacher_on_night_shift(service)
        service.create_shift("P-GUIDE", DAY1, time(23, 0), time(1, 0), headcount=1, shift_id="S-GUIDE-4", now=NOW)
        result = service.assign("V-LI", "S-GUIDE-4", now=NOW)
        self.assertIn(RejectionCode.SHIFT_OVERLAP, result.decision.codes)


class AvailabilityTest(unittest.TestCase):
    def test_adjacent_windows_cover_shift(self) -> None:
        service = make_service()
        add_parent(
            service,
            "V-CHEN",
            "陈强",
            avail=((DAY1, time(8, 0), time(10, 0)), (DAY1, time(10, 0), time(12, 0))),
        )
        result = service.assign("V-CHEN", "S-GATE-1", now=NOW)
        self.assertTrue(result.decision.approved)

    def test_gap_between_windows_is_rejected(self) -> None:
        service = make_service()
        add_parent(
            service,
            "V-CHEN",
            "陈强",
            avail=((DAY1, time(8, 0), time(9, 0)), (DAY1, time(10, 0), time(12, 0))),
        )
        result = service.assign("V-CHEN", "S-GATE-1", now=NOW)
        self.assertIn(RejectionCode.AVAILABILITY_GAP, result.decision.codes)


class SwapTest(unittest.TestCase):
    def test_swap_replaces_occupancy_atomically(self) -> None:
        service = make_service()
        add_parent(service, "V-CHEN", "陈强")
        old = service.assign("V-CHEN", "S-GATE-1", now=NOW).assignment
        add_parent(service, "V-ZHOU", "周洁")
        result = service.swap(old.assignment_id, "V-ZHOU", now=NOW)
        self.assertTrue(result.decision.approved)
        self.assertEqual(old.status, AssignmentStatus.RELEASED)
        self.assertIn("换班", old.release_reason)
        self.assertEqual(result.assignment.status, AssignmentStatus.CONFIRMED)
        self.assertEqual(service.shift_status("S-GATE-1")["filled"], 1)  # 占用不变

    def test_failed_swap_keeps_original_assignment(self) -> None:
        service = make_service()
        add_parent(service, "V-CHEN", "陈强")
        old = service.assign("V-CHEN", "S-GATE-1", now=NOW).assignment
        add_parent(service, "V-WANG", "王芳", trained=False, recuse_vendor=True)
        result = service.swap(old.assignment_id, "V-WANG", now=NOW)
        self.assertFalse(result.decision.approved)
        self.assertIsNone(result.assignment)
        self.assertEqual(old.status, AssignmentStatus.CONFIRMED)
        self.assertEqual(service.shift_status("S-GATE-1")["filled"], 1)

    def test_swap_released_assignment_raises(self) -> None:
        service = make_service()
        add_parent(service, "V-CHEN", "陈强")
        old = service.assign("V-CHEN", "S-GATE-1", now=NOW).assignment
        service.request_leave(old.assignment_id, "家中有事", now=NOW)
        with self.assertRaises(StateError):
            service.swap(old.assignment_id, "V-OTHER", now=NOW)


class LeaveTest(unittest.TestCase):
    def test_leave_releases_occupancy_and_reports_gap(self) -> None:
        service = make_service()
        add_parent(service, "V-CHEN", "陈强")
        assignment = service.assign("V-CHEN", "S-GATE-1", now=NOW).assignment
        result = service.request_leave(assignment.assignment_id, "家中急事", now=NOW)
        self.assertEqual(result.gap, 1)
        self.assertEqual(assignment.status, AssignmentStatus.RELEASED)
        self.assertIn("请假", assignment.release_reason)
        self.assertEqual(service.shift_status("S-GATE-1")["filled"], 0)

    def test_double_leave_raises(self) -> None:
        service = make_service()
        add_parent(service, "V-CHEN", "陈强")
        assignment = service.assign("V-CHEN", "S-GATE-1", now=NOW).assignment
        service.request_leave(assignment.assignment_id, "家中急事", now=NOW)
        with self.assertRaises(StateError):
            service.request_leave(assignment.assignment_id, "再次请假", now=NOW)


class RevokeTest(unittest.TestCase):
    def test_revocation_cascades_to_confirmed_assignments(self) -> None:
        service = make_service()
        add_parent(
            service,
            "V-CHEN",
            "陈强",
            avail=((DAY1, time(7, 0), time(13, 0)), (DAY2, time(7, 0), time(13, 0))),
        )
        service.create_shift("P-GATE", DAY2, time(8, 0), time(12, 0), headcount=1, shift_id="S-GATE-2", now=NOW)
        first = service.assign("V-CHEN", "S-GATE-1", now=NOW).assignment
        second = service.assign("V-CHEN", "S-GATE-2", now=NOW).assignment
        result = service.revoke_qualification("V-CHEN", Role.PARENT, "资格复核未通过", now=NOW)
        self.assertEqual(set(result.released_assignment_ids), {first.assignment_id, second.assignment_id})
        self.assertEqual(service.shift_status("S-GATE-1")["filled"], 0)
        self.assertEqual(service.shift_status("S-GATE-2")["filled"], 0)
        qualification = service.repo.volunteers["V-CHEN"].qualifications[Role.PARENT]
        self.assertEqual(qualification.status, QualificationStatus.REVOKED)
        # 撤销后再排班给出可解释拒绝
        again = service.assign("V-CHEN", "S-GATE-1", now=NOW)
        self.assertIn(RejectionCode.QUALIFICATION_REVOKED, again.decision.codes)
        self.assertIn("资格复核未通过", again.decision.explain())

    def test_revoke_missing_qualification_raises(self) -> None:
        service = make_service()
        add_parent(service, "V-CHEN", "陈强")
        with self.assertRaises(Exception):
            service.revoke_qualification("V-CHEN", Role.TEACHER, "无此资格", now=NOW)


class BackfillTest(unittest.TestCase):
    def test_backfill_with_named_volunteer_after_leave(self) -> None:
        service = make_service()
        add_parent(service, "V-CHEN", "陈强")
        old = service.assign("V-CHEN", "S-GATE-1", now=NOW).assignment
        service.request_leave(old.assignment_id, "家中急事", now=NOW)
        add_parent(service, "V-ZHOU", "周洁")
        result = service.emergency_backfill("S-GATE-1", volunteer_id="V-ZHOU", now=NOW)
        self.assertTrue(result.decision.approved)
        self.assertEqual(result.assignment.volunteer_id, "V-ZHOU")
        self.assertTrue(result.assignment.emergency)
        self.assertEqual(service.shift_status("S-GATE-1")["filled"], 1)

    def test_backfill_auto_selects_qualified_candidate(self) -> None:
        service = make_service()
        add_parent(service, "V-WANG", "王芳", trained=False, recuse_vendor=True)
        add_parent(service, "V-ZHOU", "周洁")
        result = service.emergency_backfill("S-GATE-1", now=NOW)
        self.assertIsNotNone(result.assignment)
        self.assertEqual(result.assignment.volunteer_id, "V-ZHOU")
        # 王芳不合格，尝试记录中应有她的拒绝决定
        attempted = {attempt.volunteer_id for attempt in result.attempts}
        self.assertIn("V-WANG", attempted)

    def test_backfill_without_candidate_fails_explainably(self) -> None:
        service = make_service()
        add_parent(service, "V-WANG", "王芳", trained=False, recuse_vendor=True)
        result = service.emergency_backfill("S-GATE-1", now=NOW)
        self.assertIsNone(result.assignment)
        self.assertFalse(result.decision.approved)
        self.assertIn(RejectionCode.NO_ELIGIBLE_CANDIDATE, result.decision.codes)
        self.assertIn("V-WANG", result.decision.explain())

    def test_backfill_rest_override_requires_explicit_flag(self) -> None:
        service = make_service()
        add_teacher(
            service,
            "V-WANGT",
            "王强",
            avail=((DAY1, time(20, 0), time(3, 0)), (DAY2, time(5, 0), time(9, 0))),
        )
        service.assign("V-WANGT", "S-GUIDE-1", now=NOW)
        service.create_shift("P-GUIDE", DAY2, time(6, 0), time(8, 0), headcount=1, shift_id="S-GUIDE-2", now=NOW)
        strict = service.emergency_backfill("S-GUIDE-2", now=NOW)
        self.assertIsNone(strict.assignment)
        relaxed = service.emergency_backfill("S-GUIDE-2", allow_rest_override=True, now=NOW)
        self.assertIsNotNone(relaxed.assignment)
        self.assertTrue(relaxed.assignment.rest_override)
        self.assertTrue(relaxed.decision.overrides)
        self.assertEqual(service.shift_status("S-GUIDE-2")["filled"], 1)

    def test_backfill_on_full_shift_raises(self) -> None:
        service = make_service()
        add_parent(service, "V-CHEN", "陈强")
        service.assign("V-CHEN", "S-GATE-1", now=NOW)
        with self.assertRaises(StateError):
            service.emergency_backfill("S-GATE-1", now=NOW)


class AuditEventTest(unittest.TestCase):
    def test_events_track_occupancy_changes(self) -> None:
        service = make_service()
        add_parent(service, "V-CHEN", "陈强")
        assignment = service.assign("V-CHEN", "S-GATE-1", now=NOW).assignment
        service.request_leave(assignment.assignment_id, "家中有事", now=NOW)
        types = [event.event_type for event in service.repo.events]
        self.assertIn("assignment_confirmed", types)
        self.assertIn("assignment_released", types)
        released = [e for e in service.repo.events if e.event_type == "assignment_released"][0]
        self.assertEqual(released.payload["filled"], 0)


if __name__ == "__main__":
    unittest.main()
