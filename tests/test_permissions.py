"""个人资料权限隔离的回归测试。"""
from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from roster_backend import PersonalProfile, Role, RosterService, Viewer
from roster_backend.models import RecusalTargetType

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 9, 30, 12, 0, tzinfo=TZ)


def make_service() -> RosterService:
    service = RosterService(tz_name="Asia/Shanghai")
    service.register_volunteer(
        "王芳",
        [Role.PARENT],
        volunteer_id="V-WANG",
        profile=PersonalProfile(
            phone="13800000001",
            id_number="310101199001010011",
            emergency_contact="13900000002",
            notes="对花粉过敏",
        ),
        now=NOW,
    )
    service.record_training(
        "V-WANG",
        "safety",
        completed_at=datetime(2026, 9, 1, 9, 0, tzinfo=TZ),
        valid_until=datetime(2026, 12, 31, 23, 59, tzinfo=TZ),
        now=NOW,
    )
    service.add_availability("V-WANG", date(2026, 10, 1), time(7, 0), time(13, 0), now=NOW)
    service.declare_recusal("V-WANG", RecusalTargetType.VENDOR, "VEN-FOOD", "亲属在该供应商任职", now=NOW)
    service.register_volunteer("陈强", [Role.PARENT], volunteer_id="V-CHEN", now=NOW)
    return service


class PermissionIsolationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = make_service()

    def test_coordinator_sees_full_profile(self) -> None:
        view = self.service.profile_view("V-WANG", Viewer(viewer_id="V-COORD", is_coordinator=True))
        self.assertEqual(view["access_level"], "coordinator")
        self.assertEqual(view["profile"]["phone"], "13800000001")
        self.assertEqual(view["profile"]["id_number"], "310101199001010011")
        self.assertEqual(view["trainings"][0]["label"], "安全培训")
        self.assertEqual(view["recusals"][0]["target_id"], "VEN-FOOD")
        self.assertEqual(len(view["availability"]), 1)

    def test_self_sees_own_sensitive_fields(self) -> None:
        view = self.service.profile_view("V-WANG", Viewer(viewer_id="V-WANG"))
        self.assertEqual(view["access_level"], "self")
        self.assertEqual(view["profile"]["emergency_contact"], "13900000002")
        self.assertEqual(view["notes"] if "notes" in view else view["profile"]["notes"], "对花粉过敏")

    def test_peer_sees_only_name_and_roles(self) -> None:
        view = self.service.profile_view("V-WANG", Viewer(viewer_id="V-CHEN"))
        self.assertEqual(view["access_level"], "peer")
        self.assertEqual(view["name"], "王芳")
        self.assertEqual(view["roles"], ["家长"])
        self.assertNotIn("profile", view)
        self.assertNotIn("availability", view)
        self.assertNotIn("trainings", view)
        self.assertNotIn("recusals", view)

    def test_public_sees_only_name(self) -> None:
        view = self.service.profile_view("V-WANG", Viewer())
        self.assertEqual(view["access_level"], "public")
        self.assertEqual(view["name"], "王芳")
        self.assertNotIn("roles", view)
        self.assertNotIn("profile", view)

    def test_sensitive_fields_never_leak_to_peer_or_public(self) -> None:
        for viewer in (Viewer(viewer_id="V-CHEN"), Viewer()):
            view = self.service.profile_view("V-WANG", viewer)
            leaked = [key for key in ("phone", "id_number", "emergency_contact", "notes") if key in view]
            self.assertEqual(leaked, [])


if __name__ == "__main__":
    unittest.main()
