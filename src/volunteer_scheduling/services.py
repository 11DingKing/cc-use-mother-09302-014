"""应用服务层：编排排班业务流程并强制领域不变量。

事务边界：每个公开操作在单个 SQLite 事务内完成，要么全部生效要么全部回滚。
所有拒绝都抛出 :class:`Rejection`，携带完整、可解释的原因列表。
"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from . import errors as E
from . import eligibility as EL
from .models import (
    Assignment,
    AssignmentStatus,
    Availability,
    Event,
    EventStatus,
    Person,
    Position,
    Qualification,
    QualStatus,
    Recusal,
    Role,
    TimeWindow,
)
from .permissions import Principal
from .repository import Repository


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class SchedulingService:
    def __init__(self, repo: Repository, *, today: str | None = None):
        self.repo = repo
        self._today_override = today

    # ---- 时钟 --------------------------------------------------------------

    def today(self) -> str:
        return self._today_override or dt.date.today().isoformat()

    # ---- 人员档案 / 资格 / 回避 / 可用时段 ---------------------------------

    def register_person(self, principal: Principal, person_id: str, role: str,
                        name: str, profile: dict[str, Any] | None = None) -> Person:
        principal.require_coordinator()
        Role.of(role)
        person = Person(person_id, role, name, dict(profile or {}))
        with self.repo.conn:
            self.repo.upsert_person(person)
        return person

    def set_qualification(self, principal: Principal, person_id: str,
                          training_valid_until: str | None,
                          status: str = QualStatus.ACTIVE.value) -> Qualification:
        principal.require_coordinator()
        person = self.repo.get_person(person_id)
        if person is None:
            raise E.NotFound(f"人员不存在：{person_id}")
        qual = Qualification(person_id, person.role, status, training_valid_until)
        with self.repo.conn:
            self.repo.upsert_qualification(qual)
            # 资格撤销必须同步释放占用（见 revoke_qualification）。
            if status == QualStatus.REVOKED.value:
                self._release_person_occupancy(person_id, "qualification_revoked")
        return self.repo.get_qualification(person_id)  # type: ignore[return-value]

    def add_recusal(self, principal: Principal, person_id: str, vendor_id: str,
                    reason: str | None = None) -> None:
        principal.require_coordinator()
        if self.repo.get_person(person_id) is None:
            raise E.NotFound(f"人员不存在：{person_id}")
        with self.repo.conn:
            self.repo.add_recusal(Recusal(person_id, vendor_id, reason))

    def add_availability(self, principal: Principal, person_id: str, local_date: str,
                         start_minutes: int, end_minutes: int) -> None:
        principal.require_coordinator()
        if self.repo.get_person(person_id) is None:
            raise E.NotFound(f"人员不存在：{person_id}")
        # 校验时间窗（含跨午夜）合法性。
        TimeWindow(local_date, start_minutes, end_minutes)
        with self.repo.conn:
            self.repo.add_availability(Availability(person_id, local_date,
                                                    start_minutes, end_minutes))

    # ---- 活动 / 岗位 -------------------------------------------------------

    def create_event(self, principal: Principal, event_id: str, name: str,
                     local_timezone: str) -> Event:
        principal.require_coordinator()
        event = Event(event_id, name, local_timezone)
        with self.repo.conn:
            self.repo.upsert_event(event)
        return event

    def add_position(self, principal: Principal, event_id: str, title: str,
                     required_role: str, required_headcount: int,
                     local_date: str, start_hm: str, end_hm: str,
                     vendor_id: str | None = None,
                     min_rest_minutes: int = 12 * 60,
                     position_id: str | None = None) -> Position:
        principal.require_coordinator()
        event = self._require_event(event_id)
        if event.status != EventStatus.DRAFT.value:
            raise E.Rejection([E.reason(
                E.EVENT_ALREADY_CONFIRMED, "活动已确认，不能再修改岗位需求")])
        Role.of(required_role)
        if required_headcount < 1:
            raise ValueError("required_headcount 必须 >= 1")
        window = TimeWindow.of(local_date, start_hm, end_hm)
        pos = Position(position_id or _new_id("pos"), event_id, title, required_role,
                       required_headcount, window, vendor_id, min_rest_minutes)
        with self.repo.conn:
            self.repo.upsert_position(pos)
        return pos

    # ---- 拟排班（草稿阶段） ------------------------------------------------

    def propose_assignment(self, principal: Principal, event_id: str,
                           position_id: str, person_id: str) -> Assignment:
        principal.require_coordinator()
        event = self._require_event(event_id)
        position = self._require_position(position_id)
        if event.status != EventStatus.DRAFT.value:
            raise E.Rejection([E.reason(
                E.EVENT_ALREADY_CONFIRMED,
                "活动已确认，普通拟排班入口已关闭，请使用换班或紧急补位")])
        with self.repo.conn:
            assignment = self._assign_or_reject(
                event, position, person_id,
                status=AssignmentStatus.PLANNED, reference_date=self.today())
        return assignment

    # ---- 确认排班：冻结资格 + 休息间隔 + 人数 ------------------------------

    def confirm_event(self, principal: Principal, event_id: str) -> dict[str, Any]:
        principal.require_coordinator()
        event = self._require_event(event_id)
        if event.status == EventStatus.CONFIRMED.value:
            raise E.Rejection([E.reason(E.EVENT_ALREADY_CONFIRMED, "活动已经确认过")])

        positions = self.repo.positions_for_event(event_id)
        active = self.repo.active_assignments_for_event(event_id)

        # 1) 以"班次本地日期"为基准，逐条复核资格/培训/回避/时段/休息。
        all_reasons: list[dict[str, Any]] = []
        for a in active:
            position = self._position_map(positions)[a.position_id]
            reasons = self._evaluate(
                a.person_id, position,
                reference_date=position.window.local_date,
                ignore_assignment_id=a.id)
            for r in reasons:
                r.setdefault("detail", {})["assignment_id"] = a.id
                all_reasons.append(r)

        # 2) 人数检查。
        gaps = EL.headcount_gaps(positions, active)

        if all_reasons or gaps:
            for g in gaps:
                all_reasons.append(E.reason(
                    E.HEADCOUNT_SHORT,
                    f"岗位 {g['title']} 人数不足：需要 {g['required']} 人，"
                    f"已排 {g['filled']} 人，缺 {g['short']} 人",
                    **g))
            raise E.Rejection(all_reasons)

        # 3) 全部通过：冻结资格快照。
        snapshot = self._build_snapshot()
        version = self._next_snapshot_version()
        with self.repo.conn:
            for a in active:
                frozen = self._frozen_view_for(snapshot, a.person_id)
                self.repo.update_assignment_status(
                    a.id, AssignmentStatus.CONFIRMED.value, snapshot=frozen)
            self.repo.save_snapshot(event_id, version,
                                    dt.datetime.now(dt.timezone.utc).isoformat(), snapshot)
            event.status = EventStatus.CONFIRMED.value
            event.qualification_snapshot_version = version
            self.repo.upsert_event(event)

        return {
            "event_id": event_id,
            "status": EventStatus.CONFIRMED.value,
            "snapshot_version": version,
            "confirmed_assignments": [a.id for a in active],
        }

    # ---- 换班 --------------------------------------------------------------

    def swap_assignment(self, principal: Principal, assignment_id: str,
                        to_person_id: str) -> Assignment:
        """把一条占用中的排班换给另一人；同步更新占用，岗位人数不变。"""
        principal.require_coordinator()
        old = self.repo.get_assignment(assignment_id)
        if old is None:
            raise E.NotFound(f"排班不存在：{assignment_id}")
        if old.status not in (AssignmentStatus.PLANNED.value,
                              AssignmentStatus.CONFIRMED.value):
            raise E.Rejection([E.reason(
                E.ASSIGNMENT_NOT_CHANGEABLE, "该排班已释放或取消，不能换班")])
        if old.person_id == to_person_id:
            raise E.Rejection([E.reason(
                E.DUPLICATE_ASSIGNMENT, "换班目标与当前志愿者相同")])

        event = self._require_event(old.event_id)
        position = self._require_position(old.position_id)
        # 一进一出原子完成：新人不满足资格则原排班保留，绝不留下岗位缺口。
        with self.repo.conn:
            new_status = (AssignmentStatus.CONFIRMED
                          if event.status == EventStatus.CONFIRMED.value
                          else AssignmentStatus.PLANNED)
            new_a = self._assign_or_reject(
                event, position, to_person_id, status=new_status,
                reference_date=position.window.local_date,
                releasing_assignment_id=old.id)
            self.repo.update_assignment_status(
                old.id, AssignmentStatus.RELEASED.value)
        return new_a

    # ---- 请假 --------------------------------------------------------------

    def request_leave(self, principal: Principal, assignment_id: str) -> dict[str, Any]:
        """志愿者请假：释放占用；若活动已确认，返回由此产生的岗位缺口。"""
        old = self.repo.get_assignment(assignment_id)
        if old is None:
            raise E.NotFound(f"排班不存在：{assignment_id}")
        # 志愿者本人可以为自己请假；协调员可为任何人请假。
        if not (principal.is_coordinator or principal.person_id == old.person_id):
            raise E.Forbidden("只能为自己的排班申请请假")
        if old.status not in (AssignmentStatus.PLANNED.value,
                              AssignmentStatus.CONFIRMED.value):
            raise E.Rejection([E.reason(
                E.ASSIGNMENT_NOT_CHANGEABLE, "该排班不在占用状态，无需请假")])
        with self.repo.conn:
            self.repo.update_assignment_status(
                old.id, AssignmentStatus.RELEASED.value)
        return self._gap_report(old.event_id, old.position_id,
                                released_assignment_id=old.id)

    # ---- 资格撤销（同步更新占用） ------------------------------------------

    def revoke_qualification(self, principal: Principal, person_id: str) -> dict[str, Any]:
        principal.require_coordinator()
        if self.repo.get_qualification(person_id) is None:
            raise E.NotFound(f"资格记录不存在：{person_id}")
        with self.repo.conn:
            qual = self.repo.get_qualification(person_id)
            qual.status = QualStatus.REVOKED.value  # type: ignore[union-attr]
            self.repo.upsert_qualification(qual)  # type: ignore[arg-type]
            released = self._release_person_occupancy(person_id,
                                                       "qualification_revoked")
        gaps = []
        event_ids = {a.event_id for a in released}
        for eid in event_ids:
            positions = self.repo.positions_for_event(eid)
            gaps.extend(EL.headcount_gaps(
                positions, self.repo.active_assignments_for_event(eid)))
        return {"released_assignments": [a.id for a in released], "gaps": gaps}

    # ---- 紧急补位 ----------------------------------------------------------

    def emergency_fill(self, principal: Principal, event_id: str,
                       position_id: str) -> Assignment:
        """岗位出现缺口时，自动挑选一名当前合格的志愿者补位。

        选择是确定性的（按人员编号排序），并且对每位落选者都保留可解释原因，
        无任何人可补时抛出 NO_ELIGIBLE_CANDIDATE。
        """
        principal.require_coordinator()
        event = self._require_event(event_id)
        position = self._require_position(position_id)
        if position.event_id != event_id:
            raise E.NotFound("该岗位不属于此活动")

        active = self.repo.active_assignments_for_event(event_id)
        if EL.headcount_gaps([position], active) == []:
            raise E.Rejection([E.reason(E.POSITION_FULL, "该岗位没有缺口，无需补位")])

        occupied_person_ids = {a.person_id for a in active if a.position_id == position_id}
        # 已请假/换出该岗的人不作为补位人选回填。
        excluded = occupied_person_ids | self.repo.released_person_ids_for_position(position_id)
        candidates = sorted(
            (p for p in self.repo.list_people(position.required_role)
             if p.id not in excluded),
            key=lambda p: p.id,
        )

        candidate_reasons: dict[str, list[dict[str, Any]]] = {}
        for person in candidates:
            reasons = self._evaluate(
                person.id, position,
                reference_date=position.window.local_date)
            if not reasons:
                new_status = (AssignmentStatus.CONFIRMED
                              if event.status == EventStatus.CONFIRMED.value
                              else AssignmentStatus.PLANNED)
                with self.repo.conn:
                    return self._assign_or_reject(
                        event, position, person.id, status=new_status,
                        reference_date=position.window.local_date)
            candidate_reasons[person.id] = reasons

        raise E.Rejection([E.reason(
            E.NO_ELIGIBLE_CANDIDATE,
            f"岗位 {position.title} 无合格候选人可紧急补位",
            position_id=position_id,
            candidates=[{"person_id": pid, "reasons": rs}
                        for pid, rs in candidate_reasons.items()])])

    # ---- 查询 --------------------------------------------------------------

    def roster_view(self, principal: Principal, event_id: str) -> dict[str, Any]:
        event = self._require_event(event_id)
        positions = self.repo.positions_for_event(event_id)
        active = self.repo.active_assignments_for_event(event_id)
        gaps = EL.headcount_gaps(positions, active)
        people_cache: dict[str, Person] = {}

        def person_name(pid: str) -> str:
            if pid not in people_cache:
                p = self.repo.get_person(pid)
                people_cache[pid] = p  # type: ignore[assignment]
            return people_cache[pid].name if people_cache[pid] else pid

        return {
            "event_id": event_id,
            "status": event.status,
            "timezone": event.local_timezone,
            "positions": [
                {
                    "position_id": p.id,
                    "title": p.title,
                    "required_role": p.required_role,
                    "required_headcount": p.required_headcount,
                    "window": p.window.to_dict(),
                    "vendor_id": p.vendor_id,
                    "assigned": [
                        {
                            "assignment_id": a.id,
                            "person_id": a.person_id,
                            "person_name": person_name(a.person_id),
                            "status": a.status,
                        }
                        for a in active if a.position_id == p.id
                    ],
                }
                for p in positions
            ],
            "gaps": gaps,
        }

    def person_view(self, principal: Principal, person_id: str) -> dict[str, Any]:
        from .permissions import person_to_view

        person = self.repo.get_person(person_id)
        if person is None:
            raise E.NotFound(f"人员不存在：{person_id}")
        view = person_to_view(principal, person)
        if principal.can_view_full_profile(person_id):
            qual = self.repo.get_qualification(person_id)
            view["qualification"] = (
                None if qual is None
                else {"role": qual.role, "status": qual.status,
                      "training_valid_until": qual.training_valid_until,
                      "version": qual.version})
            view["recusals"] = [
                {"vendor_id": r.vendor_id, "reason": r.reason}
                for r in self.repo.recusals_for(person_id)]
            view["availabilities"] = [
                {"local_date": a.local_date, "start_minutes": a.start_minutes,
                 "end_minutes": a.end_minutes}
                for a in self.repo.availabilities_for(person_id)]
        return view

    # ---- 内部辅助 ----------------------------------------------------------

    def _require_event(self, event_id: str) -> Event:
        event = self.repo.get_event(event_id)
        if event is None:
            raise E.NotFound(f"活动不存在：{event_id}")
        return event

    def _require_position(self, position_id: str) -> Position:
        position = self.repo.get_position(position_id)
        if position is None:
            raise E.NotFound(f"岗位不存在：{position_id}")
        return position

    @staticmethod
    def _position_map(positions: list[Position]) -> dict[str, Position]:
        return {p.id: p for p in positions}

    def _evaluate(self, person_id: str, position: Position, *,
                  reference_date: str,
                  ignore_assignment_id: str | None = None) -> list[dict[str, Any]]:
        qual = self.repo.get_qualification(person_id)
        recusals = self.repo.vendor_ids_for(person_id)
        avail = self.repo.availabilities_for(person_id)
        existing = self.repo.active_assignments_for_person(person_id)
        return EL.evaluate_candidate(
            qualification=qual,
            recusal_vendor_ids=recusals,
            availabilities=avail,
            existing_assignments=existing,
            position=position,
            today=reference_date,
            ignore_assignment_id=ignore_assignment_id,
        )

    def _assign_or_reject(self, event: Event, position: Position, person_id: str,
                          *, status: AssignmentStatus,
                          reference_date: str,
                          releasing_assignment_id: str | None = None) -> Assignment:
        """校验并落库一条排班；人数满或任一项不合格都拒绝。

        ``releasing_assignment_id`` 用于换班：被替换的旧班在同一事务里随后释放，
        因此它当前占用的名额应视为可让给新人，避免"满员"误判。
        """
        if self.repo.get_person(person_id) is None:
            raise E.NotFound(f"人员不存在：{person_id}")

        active_for_position = [
            a for a in self.repo.active_assignments_for_position(position.id)
            if a.id != releasing_assignment_id
        ]
        if len(active_for_position) >= position.required_headcount:
            raise E.Rejection([E.reason(
                E.POSITION_FULL,
                f"岗位 {position.title} 已排满 {position.required_headcount} 人")])

        reasons = self._evaluate(
            person_id, position, reference_date=reference_date,
            ignore_assignment_id=releasing_assignment_id)
        if reasons:
            raise E.Rejection(reasons)

        snapshot = None
        if status == AssignmentStatus.CONFIRMED:
            snap = self.repo.get_snapshot(event.id)
            if snap is not None:
                snapshot = self._frozen_view_for(
                    {"qualifications": snap["qualifications"]}, person_id)
        assignment = Assignment(
            _new_id("asg"), event.id, position.id, person_id,
            position.window, status.value, position.vendor_id, snapshot)
        self.repo.insert_assignment(assignment)
        return assignment

    def _release_person_occupancy(self, person_id: str,
                                  reason_tag: str) -> list[Assignment]:
        released: list[Assignment] = []
        for a in self.repo.active_assignments_for_person(person_id):
            self.repo.update_assignment_status(
                a.id, AssignmentStatus.RELEASED.value)
            released.append(a)
        return released

    def _gap_report(self, event_id: str, position_id: str,
                    released_assignment_id: str) -> dict[str, Any]:
        positions = self.repo.positions_for_event(event_id)
        gaps = EL.headcount_gaps(positions,
                                 self.repo.active_assignments_for_event(event_id))
        this_gap = next((g for g in gaps if g["position_id"] == position_id), None)
        return {
            "released_assignment_id": released_assignment_id,
            "opened_gap": this_gap,
            "all_gaps": gaps,
        }

    def _build_snapshot(self) -> dict[str, Any]:
        qualifications = []
        recusals = []
        availabilities = []
        for p in self.repo.list_people():
            q = self.repo.get_qualification(p.id)
            if q is not None:
                qualifications.append({
                    "person_id": p.id, "role": q.role, "status": q.status,
                    "training_valid_until": q.training_valid_until,
                    "version": q.version,
                })
            for r in self.repo.recusals_for(p.id):
                recusals.append({"person_id": r.person_id, "vendor_id": r.vendor_id,
                                 "reason": r.reason})
            for a in self.repo.availabilities_for(p.id):
                availabilities.append({
                    "person_id": a.person_id, "local_date": a.local_date,
                    "start_minutes": a.start_minutes, "end_minutes": a.end_minutes,
                })
        return {"qualifications": qualifications, "recusals": recusals,
                "availabilities": availabilities}

    def _next_snapshot_version(self) -> int:
        row = self.repo.conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 AS v FROM qualification_snapshots"
        ).fetchone()
        return int(row["v"])

    @staticmethod
    def _frozen_view_for(snapshot: dict[str, Any], person_id: str) -> dict[str, Any]:
        qual = next((q for q in snapshot.get("qualifications", [])
                     if q["person_id"] == person_id), None)
        return {"qualification": qual}
