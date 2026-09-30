"""JSON HTTP API（仅标准库）。

鉴权约定（演示用途的轻量方案）
------------------------------
请求头 ``X-Principal`` 为 ``coordinator`` 或 ``volunteer:<person_id>``，
映射为 :class:`Principal`。生产环境应替换为真正的认证中间件，
权限模型本身（``permissions`` 模块）不依赖此处实现。

所有业务拒绝统一返回：
``422 {"error": {"code": "REJECTED", "reasons": [...]}}``，
原因结构直接面向协调员/志愿者，可读、可解释。
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import urlparse

from . import errors as E
from .permissions import Principal
from .repository import Repository, connect
from .services import SchedulingService


def _principal_from_headers(headers) -> Principal:
    raw = headers.get("X-Principal", "coordinator")
    if raw == "coordinator":
        return Principal("coordinator")
    if raw.startswith("volunteer:"):
        return Principal("volunteer", raw.split(":", 1)[1])
    raise E.Forbidden("无法识别的身份，请通过 X-Principal 指定")


class SchedulingHandler(BaseHTTPRequestHandler):
    server_version = "VolunteerScheduling/1.0"

    # 由 create_server 注入
    service_builder: Callable[[], SchedulingService]
    db_lock: threading.RLock

    # ---- 工具 --------------------------------------------------------------

    def _send(self, status: int, payload: Any) -> None:
        data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def log_message(self, *args: Any) -> None:  # 安静
        pass

    # ---- 路由 --------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.strip("/").split("/")
        with self.db_lock:
            try:
                principal = _principal_from_headers(self.headers)
                svc = self.service_builder()
                if len(path) == 3 and path[0] == "events" and path[2] == "roster":
                    self._send(200, svc.roster_view(principal, path[1]))
                elif len(path) == 2 and path[0] == "people":
                    self._send(200, svc.person_view(principal, path[1]))
                else:
                    self._send(404, {"error": {"code": "NOT_FOUND", "message": "未知路径"}})
            except (E.NotFound, KeyError) as exc:
                self._send(404, {"error": {"code": "NOT_FOUND", "message": str(exc)}})
            except E.Forbidden as exc:
                self._send(403, {"error": {"code": "FORBIDDEN", "message": str(exc)}})

    def do_POST(self) -> None:  # noqa: N802
        # 先读请求体（在锁外也安全），再串行执行业务写事务。
        try:
            body = self._body()
        except ValueError as exc:
            self._send(400, {"error": {"code": "BAD_REQUEST", "message": str(exc)}})
            return
        path = urlparse(self.path).path.strip("/").split("/")
        with self.db_lock:
            try:
                principal = _principal_from_headers(self.headers)
                svc = self.service_builder()
                self._route_post(svc, principal, path, body)
            except E.Rejection as exc:
                self._send(422, {"error": {"code": "REJECTED", "reasons": exc.reasons}})
            except E.Forbidden as exc:
                self._send(403, {"error": {"code": "FORBIDDEN", "message": str(exc)}})
            except E.NotFound as exc:
                self._send(404, {"error": {"code": "NOT_FOUND", "message": str(exc)}})
            except (ValueError, KeyError) as exc:
                self._send(400, {"error": {"code": "BAD_REQUEST", "message": str(exc)}})

    def _route_post(self, svc: SchedulingService, principal: Principal,
                    path: list[str], body: dict[str, Any]) -> None:
        # /people
        if path == ["people"]:
            person = svc.register_person(
                principal, body["person_id"], body["role"], body["name"],
                body.get("profile"))
            self._send(201, {"id": person.id, "role": person.role, "name": person.name})
        # /people/{id}/qualification
        elif len(path) == 3 and path[0] == "people" and path[2] == "qualification":
            q = svc.set_qualification(
                principal, path[1], body.get("training_valid_until"),
                body.get("status", "active"))
            self._send(200, {"person_id": q.person_id, "role": q.role,
                             "status": q.status,
                             "training_valid_until": q.training_valid_until})
        elif len(path) == 3 and path[0] == "people" and path[2] == "recusals":
            svc.add_recusal(principal, path[1], body["vendor_id"], body.get("reason"))
            self._send(201, {"ok": True})
        elif len(path) == 3 and path[0] == "people" and path[2] == "availabilities":
            svc.add_availability(
                principal, path[1], body["local_date"],
                body["start_minutes"], body["end_minutes"])
            self._send(201, {"ok": True})
        # /events
        elif path == ["events"]:
            event = svc.create_event(
                principal, body["event_id"], body["name"], body["local_timezone"])
            self._send(201, {"id": event.id, "status": event.status})
        # /events/{id}/positions
        elif len(path) == 3 and path[0] == "events" and path[2] == "positions":
            pos = svc.add_position(
                principal, path[1], body["title"], body["required_role"],
                int(body["required_headcount"]), body["local_date"],
                body["start"], body["end"], body.get("vendor_id"),
                int(body.get("min_rest_minutes", 720)),
                body.get("position_id"))
            self._send(201, {"id": pos.id, "window": pos.window.to_dict()})
        # /events/{id}/assignments
        elif len(path) == 3 and path[0] == "events" and path[2] == "assignments":
            a = svc.propose_assignment(
                principal, path[1], body["position_id"], body["person_id"])
            self._send(201, {"assignment_id": a.id, "status": a.status})
        elif len(path) == 3 and path[0] == "events" and path[2] == "confirm":
            self._send(200, svc.confirm_event(principal, path[1]))
        elif len(path) == 3 and path[0] == "events" and path[2] == "emergency-fill":
            a = svc.emergency_fill(
                principal, path[1], body["position_id"])
            self._send(201, {"assignment_id": a.id, "person_id": a.person_id,
                             "status": a.status})
        # /assignments/{id}/swap | leave
        elif len(path) == 3 and path[0] == "assignments" and path[2] == "swap":
            a = svc.swap_assignment(principal, path[1], body["to_person_id"])
            self._send(200, {"assignment_id": a.id, "person_id": a.person_id,
                             "status": a.status})
        elif len(path) == 3 and path[0] == "assignments" and path[2] == "leave":
            self._send(200, svc.request_leave(principal, path[1]))
        # /people/{id}/revoke
        elif len(path) == 3 and path[0] == "people" and path[2] == "revoke":
            self._send(200, svc.revoke_qualification(principal, path[1]))
        else:
            self._send(404, {"error": {"code": "NOT_FOUND", "message": "未知路径"}})


def create_server(database: str = ":memory:", host: str = "127.0.0.1",
                  port: int = 8000, today: str | None = None
                  ) -> ThreadingHTTPServer:
    """构造 HTTP 服务；每个请求共享同一个数据库连接（SQLite 串行写足够演示）。"""
    conn = connect(database, check_same_thread=False)
    repo = Repository(conn)
    db_lock = threading.RLock()

    def builder() -> SchedulingService:
        return SchedulingService(repo, today=today)

    handler = type("BoundHandler", (SchedulingHandler,),
                   {"service_builder": staticmethod(builder),
                    "db_lock": db_lock})
    return ThreadingHTTPServer((host, port), handler)


def run(database: str = ":memory:", host: str = "127.0.0.1",
        port: int = 8000, today: str | None = None) -> None:
    server = create_server(database, host, port, today)
    print(f"排班服务已启动：http://{host}:{port}")
    server.serve_forever()
