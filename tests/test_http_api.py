"""HTTP JSON API 的端到端集成测试。"""
from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from volunteer_scheduling.http_app import create_server  # noqa: E402

EVENT_DAY = "2026-10-01"


class HttpClient:
    def __init__(self, base: str):
        self.base = base

    def call(self, method: str, path: str, body=None, principal: str = "coordinator"):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Content-Type": "application/json", "X-Principal": principal})
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())


class HttpApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = create_server(":memory:", "127.0.0.1", 0, today="2026-09-30")
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.api = HttpClient(f"http://127.0.0.1:{cls.port}")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()

    def test_full_flow_with_explainable_rejection(self) -> None:
        api = self.api

        # 活动 + 供应商岗位（需要 2 名家长）
        self._post(201, "/events", {"event_id": "ev1", "name": "开放日",
                                    "local_timezone": "Asia/Shanghai"})
        _, pos_payload = self._post(201, "/events/ev1/positions", {
            "title": "入口值守", "required_role": "parent", "required_headcount": 2,
            "local_date": EVENT_DAY, "start": "19:00", "end": "22:00",
            "vendor_id": "vendor-catering"})
        position_id = pos_payload["id"]

        # 未培训 + 回避的家长：尝试排班，422 且原因可解释
        self._post(201, "/people", {"person_id": "p1", "role": "parent", "name": "王芳",
                                    "profile": {"phone": "139"}}, )
        self._post(200, "/people/p1/qualification", {"training_valid_until": None})
        self._post(201, "/people/p1/recusals", {"vendor_id": "vendor-catering"})
        self._post(201, "/people/p1/availabilities",
                   {"local_date": EVENT_DAY, "start_minutes": 1080, "end_minutes": 1380})

        status, rejected = self._post(422, "/events/ev1/assignments",
                                      {"position_id": position_id, "person_id": "p1"})
        codes = {r["code"] for r in rejected["error"]["reasons"]}
        self.assertEqual(codes, {"TRAINING_MISSING", "RECUSAL_CONFLICT"})

        # 补齐两名合格家长并确认
        self._make_ready_parent("p2")
        self._make_ready_parent("p3")
        self._post(201, "/events/ev1/assignments",
                   {"position_id": position_id, "person_id": "p2"})
        self._post(201, "/events/ev1/assignments",
                   {"position_id": position_id, "person_id": "p3"})
        _, confirmed = self._post(200, "/events/ev1/confirm", {})
        self.assertEqual(confirmed["status"], "confirmed")

        # 请假产生缺口，再紧急补位（补位者 p4 合格）
        roster = self._get(200, "/events/ev1/roster")[1]
        asg_p2 = next(
            a["assignment_id"]
            for pos in roster["positions"] for a in pos["assigned"]
            if a["person_id"] == "p2")
        _, leave = self._post(200, f"/assignments/{asg_p2}/leave", {},
                              principal="volunteer:p2")
        self.assertIsNotNone(leave["opened_gap"])
        self._make_ready_parent("p4")
        status, filled = self._post(201, "/events/ev1/emergency-fill",
                                    {"position_id": position_id})
        self.assertEqual(filled["person_id"], "p4")
        self.assertEqual(self._get(200, "/events/ev1/roster")[1]["gaps"], [])

    def test_permission_isolation_over_http(self) -> None:
        api = self.api
        self._post(201, "/people", {"person_id": "q1", "role": "teacher", "name": "李老师",
                                    "profile": {"phone": "139", "id_card": "X"}})
        # 协调员可见
        self.assertIn("phone", self._get(200, "/people/q1")[1]["profile"])
        # 本人可见
        self.assertIn("phone",
                      self._get(200, "/people/q1", principal="volunteer:q1")[1]["profile"])
        # 其他志愿者不可见
        other = self._get(200, "/people/q1", principal="volunteer:q9")[1]["profile"]
        self.assertNotIn("phone", other)
        self.assertNotIn("id_card", other)

    def test_volunteer_forbidden_from_scheduling(self) -> None:
        self._post(403, "/events",
                   {"event_id": "evX", "name": "x", "local_timezone": "Asia/Shanghai"},
                   principal="volunteer:q9")

    # -- 帮助方法 --

    def _make_ready_parent(self, pid: str) -> None:
        self._post(201, "/people", {"person_id": pid, "role": "parent", "name": pid})
        self._post(200, f"/people/{pid}/qualification",
                   {"training_valid_until": "2026-12-31"})
        self._post(201, f"/people/{pid}/availabilities",
                   {"local_date": EVENT_DAY, "start_minutes": 0, "end_minutes": 1440})

    def _get(self, expected: int, path: str, principal: str = "coordinator"):
        status, payload = self.api.call("GET", path, principal=principal)
        self.assertEqual(status, expected, payload)
        return status, payload

    def _post(self, expected: int, path: str, body=None,
              principal: str = "coordinator"):
        status, payload = self.api.call("POST", path, body, principal)
        self.assertEqual(status, expected, payload)
        return status, payload


if __name__ == "__main__":
    unittest.main()
