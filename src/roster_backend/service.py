"""活动志愿者排班后端服务。

覆盖教师、家长、学生助理三类志愿者的角色资格、培训有效期、回避关系、
可用时段与岗位需求管理；确认排班时冻结资格快照并校验休息间隔与人数；
换班、请假、资格撤销与紧急补位同步更新班次占用；所有拒绝决定均可解释。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from .decisions import Decision, Rejection, RejectionCode
from .errors import NotFoundError, StateError
from .models import (
    ROLE_LABELS,
    TRAINING_LABELS,
    Assignment,
    AssignmentStatus,
    Event,
    PersonalProfile,
    Position,
    Qualification,
    QualificationSnapshot,
    QualificationStatus,
    RecusalRelation,
    RecusalTargetType,
    Role,
    SchedulingPolicy,
    Shift,
    TrainingRecord,
    Volunteer,
)
from .permissions import Viewer, profile_view
from .repository import InMemoryRepository
from .timeutil import TimeWindow, covers, gap_minutes, local_window, overlaps


def _fmt(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M")


@dataclass(frozen=True)
class AssignmentResult:
    decision: Decision
    assignment: Assignment | None


@dataclass(frozen=True)
class SwapResult:
    decision: Decision
    previous: Assignment
    assignment: Assignment | None


@dataclass(frozen=True)
class LeaveResult:
    assignment: Assignment
    shift_id: str
    gap: int


@dataclass(frozen=True)
class RevokeResult:
    volunteer_id: str
    role: Role
    released_assignment_ids: tuple[str, ...]
    affected_shift_ids: tuple[str, ...]


@dataclass(frozen=True)
class CandidateAttempt:
    volunteer_id: str
    decision: Decision


@dataclass(frozen=True)
class BackfillResult:
    decision: Decision
    assignment: Assignment | None
    attempts: tuple[CandidateAttempt, ...]


class RosterService:
    """排班领域服务，所有变更操作同步维护班次占用。"""

    def __init__(
        self,
        repository: InMemoryRepository | None = None,
        policy: SchedulingPolicy | None = None,
        tz_name: str = "Asia/Shanghai",
    ) -> None:
        self.repo = repository or InMemoryRepository()
        self.policy = policy or SchedulingPolicy()
        self.tz = ZoneInfo(tz_name)

    # ---- 基础查询 ----

    def _now(self, now: datetime | None) -> datetime:
        if now is None:
            return datetime.now(self.tz)
        if now.tzinfo is None:
            raise ValueError("now 必须携带时区信息")
        return now

    def _volunteer(self, volunteer_id: str) -> Volunteer:
        try:
            return self.repo.volunteers[volunteer_id]
        except KeyError:
            raise NotFoundError(f"志愿者不存在：{volunteer_id}") from None

    def _position(self, position_id: str) -> Position:
        try:
            return self.repo.positions[position_id]
        except KeyError:
            raise NotFoundError(f"岗位不存在：{position_id}") from None

    def _shift(self, shift_id: str) -> Shift:
        try:
            return self.repo.shifts[shift_id]
        except KeyError:
            raise NotFoundError(f"班次不存在：{shift_id}") from None

    def _assignment(self, assignment_id: str) -> Assignment:
        try:
            return self.repo.assignments[assignment_id]
        except KeyError:
            raise NotFoundError(f"班次占用不存在：{assignment_id}") from None

    def _emit(self, event_type: str, at: datetime, **payload: object) -> None:
        self.repo.events.append(Event(event_type, at, payload))

    def _confirmed_assignments_of(self, volunteer_id: str) -> list[Assignment]:
        return [
            item
            for item in self.repo.assignments.values()
            if item.volunteer_id == volunteer_id and item.status is AssignmentStatus.CONFIRMED
        ]

    # ---- 志愿者档案 ----

    def register_volunteer(
        self,
        name: str,
        roles: list[Role],
        *,
        volunteer_id: str | None = None,
        profile: PersonalProfile | None = None,
        now: datetime | None = None,
    ) -> Volunteer:
        now = self._now(now)
        vid = volunteer_id or self.repo.next_id("V")
        if vid in self.repo.volunteers:
            raise StateError(f"志愿者编号已存在：{vid}")
        volunteer = Volunteer(volunteer_id=vid, name=name, profile=profile or PersonalProfile())
        for role in roles:
            volunteer.qualifications[role] = Qualification(
                role=role, status=QualificationStatus.ACTIVE, granted_at=now
            )
        self.repo.volunteers[vid] = volunteer
        self._emit("volunteer_registered", now, volunteer_id=vid, roles=[r.value for r in roles])
        return volunteer

    def record_training(
        self,
        volunteer_id: str,
        training_type: str,
        *,
        completed_at: datetime,
        valid_until: datetime,
        now: datetime | None = None,
    ) -> TrainingRecord:
        volunteer = self._volunteer(volunteer_id)
        if completed_at.tzinfo is None or valid_until.tzinfo is None:
            raise ValueError("培训时间必须携带时区信息")
        if valid_until <= completed_at:
            raise ValueError("培训有效期必须晚于完成时间")
        record = TrainingRecord(training_type, completed_at, valid_until)
        volunteer.trainings.append(record)
        self._emit(
            "training_recorded",
            self._now(now),
            volunteer_id=volunteer_id,
            training_type=training_type,
            valid_until=valid_until.isoformat(),
        )
        return record

    def add_availability(
        self,
        volunteer_id: str,
        day: date,
        start: time,
        end: time,
        *,
        now: datetime | None = None,
    ) -> TimeWindow:
        """按本地日期登记可用时段；结束时刻不晚于开始时刻时视为跨午夜。"""
        volunteer = self._volunteer(volunteer_id)
        window = local_window(day, start, end, self.tz)
        volunteer.availability.append(window)
        self._emit(
            "availability_added",
            self._now(now),
            volunteer_id=volunteer_id,
            start=window.start.isoformat(),
            end=window.end.isoformat(),
        )
        return window

    def declare_recusal(
        self,
        volunteer_id: str,
        target_type: RecusalTargetType,
        target_id: str,
        reason: str,
        *,
        now: datetime | None = None,
    ) -> RecusalRelation:
        volunteer = self._volunteer(volunteer_id)
        recusal = RecusalRelation(target_type, target_id, reason)
        volunteer.recusals.append(recusal)
        self._emit(
            "recusal_declared",
            self._now(now),
            volunteer_id=volunteer_id,
            target_type=target_type.value,
            target_id=target_id,
        )
        return recusal

    # ---- 岗位与班次 ----

    def create_position(
        self,
        name: str,
        required_role: Role,
        *,
        required_trainings: tuple[str, ...] = (),
        vendor_id: str | None = None,
        position_id: str | None = None,
        now: datetime | None = None,
    ) -> Position:
        pid = position_id or self.repo.next_id("P")
        if pid in self.repo.positions:
            raise StateError(f"岗位编号已存在：{pid}")
        position = Position(pid, name, required_role, tuple(required_trainings), vendor_id)
        self.repo.positions[pid] = position
        self._emit(
            "position_created",
            self._now(now),
            position_id=pid,
            required_role=required_role.value,
            vendor_id=vendor_id,
        )
        return position

    def create_shift(
        self,
        position_id: str,
        day: date,
        start: time,
        end: time,
        *,
        headcount: int,
        shift_id: str | None = None,
        now: datetime | None = None,
    ) -> Shift:
        """创建班次；跨午夜时段按本地时区顺延到次日。"""
        self._position(position_id)
        if headcount < 1:
            raise ValueError("班次需求人数至少为 1")
        sid = shift_id or self.repo.next_id("S")
        if sid in self.repo.shifts:
            raise StateError(f"班次编号已存在：{sid}")
        shift = Shift(sid, position_id, local_window(day, start, end, self.tz), headcount)
        self.repo.shifts[sid] = shift
        self._emit(
            "shift_created",
            self._now(now),
            shift_id=sid,
            position_id=position_id,
            start=shift.window.start.isoformat(),
            end=shift.window.end.isoformat(),
            headcount=headcount,
        )
        return shift

    # ---- 资格评估（可解释） ----

    def evaluate(
        self,
        volunteer_id: str,
        shift_id: str,
        *,
        excluding_assignment_id: str | None = None,
        relax_rest: bool = False,
    ) -> Decision:
        """评估志愿者能否占用班次，返回包含全部原因的解释性决定。"""
        volunteer = self._volunteer(volunteer_id)
        shift = self._shift(shift_id)
        position = self._position(shift.position_id)
        reasons: list[Rejection] = []
        overrides: list[str] = []

        qualification = volunteer.qualifications.get(position.required_role)
        role_label = ROLE_LABELS[position.required_role]
        if qualification is None:
            reasons.append(
                Rejection(
                    RejectionCode.ROLE_NOT_QUALIFIED,
                    f"未持有岗位「{position.name}」要求的{role_label}资格",
                    {"required_role": position.required_role.value},
                )
            )
        elif qualification.status is QualificationStatus.REVOKED:
            revoked_at = _fmt(qualification.revoked_at) if qualification.revoked_at else "未知时间"
            reasons.append(
                Rejection(
                    RejectionCode.QUALIFICATION_REVOKED,
                    f"{role_label}资格已于 {revoked_at} 撤销（原因：{qualification.revoke_reason}）",
                    {"revoke_reason": qualification.revoke_reason},
                )
            )

        check_moment = (
            shift.window.end if self.policy.training_must_cover_shift_end else shift.window.start
        )
        for training_type in position.required_trainings:
            label = TRAINING_LABELS.get(training_type, training_type)
            records = [r for r in volunteer.trainings if r.training_type == training_type]
            if not records:
                reasons.append(
                    Rejection(
                        RejectionCode.TRAINING_MISSING,
                        f"缺少岗位「{position.name}」要求的{label}记录",
                        {"training_type": training_type},
                    )
                )
            elif not any(r.valid_at(check_moment) for r in records):
                latest = max(r.valid_until for r in records)
                reasons.append(
                    Rejection(
                        RejectionCode.TRAINING_EXPIRED,
                        f"{label}有效期至 {_fmt(latest)}，早于班次要求时点 {_fmt(check_moment)}",
                        {
                            "training_type": training_type,
                            "valid_until": latest.isoformat(),
                            "required_at": check_moment.isoformat(),
                        },
                    )
                )

        for recusal in volunteer.recusals:
            if (
                recusal.target_type is RecusalTargetType.VENDOR
                and position.vendor_id
                and recusal.target_id == position.vendor_id
            ):
                reasons.append(
                    Rejection(
                        RejectionCode.RECUSAL_CONFLICT,
                        f"与岗位关联供应商「{recusal.target_id}」存在回避关系（{recusal.reason}）",
                        {"target_type": "vendor", "target_id": recusal.target_id},
                    )
                )
            elif (
                recusal.target_type is RecusalTargetType.POSITION
                and recusal.target_id == position.position_id
            ):
                reasons.append(
                    Rejection(
                        RejectionCode.RECUSAL_CONFLICT,
                        f"与岗位「{position.name}」存在回避关系（{recusal.reason}）",
                        {"target_type": "position", "target_id": recusal.target_id},
                    )
                )

        if not covers(volunteer.availability, shift.window):
            reasons.append(
                Rejection(
                    RejectionCode.AVAILABILITY_GAP,
                    f"申报的可用时段未覆盖班次 {_fmt(shift.window.start)}~{_fmt(shift.window.end)}",
                    {
                        "shift_start": shift.window.start.isoformat(),
                        "shift_end": shift.window.end.isoformat(),
                    },
                )
            )

        for other in self._confirmed_assignments_of(volunteer_id):
            if other.assignment_id == excluding_assignment_id:
                continue
            other_window = self._shift(other.shift_id).window
            if overlaps(other_window, shift.window):
                reasons.append(
                    Rejection(
                        RejectionCode.SHIFT_OVERLAP,
                        f"与已确认班次 {other.shift_id}"
                        f"（{_fmt(other_window.start)}~{_fmt(other_window.end)}）时间重叠",
                        {"conflict_shift_id": other.shift_id},
                    )
                )
                continue
            gap = gap_minutes(other_window, shift.window)
            if gap < self.policy.min_rest_minutes:
                note = (
                    f"与已确认班次 {other.shift_id} 间隔仅 {gap:.0f} 分钟，"
                    f"不足规定的 {self.policy.min_rest_minutes} 分钟休息"
                )
                if relax_rest:
                    overrides.append(note + "（紧急补位放宽）")
                else:
                    reasons.append(
                        Rejection(
                            RejectionCode.REST_INTERVAL_VIOLATION,
                            note,
                            {
                                "conflict_shift_id": other.shift_id,
                                "gap_minutes": gap,
                                "min_rest_minutes": self.policy.min_rest_minutes,
                            },
                        )
                    )

        effective_filled = shift.filled - (1 if excluding_assignment_id else 0)
        if effective_filled >= shift.headcount_needed:
            reasons.append(
                Rejection(
                    RejectionCode.HEADCOUNT_FULL,
                    f"班次 {shift.shift_id} 已满员（{shift.headcount_needed} 人），无法继续占用",
                    {"headcount_needed": shift.headcount_needed},
                )
            )

        return Decision(approved=not reasons, reasons=tuple(reasons), overrides=tuple(overrides))

    # ---- 确认排班（冻结资格快照并占用名额） ----

    def assign(
        self,
        volunteer_id: str,
        shift_id: str,
        *,
        emergency: bool = False,
        relax_rest: bool = False,
        now: datetime | None = None,
    ) -> AssignmentResult:
        now = self._now(now)
        decision = self.evaluate(volunteer_id, shift_id, relax_rest=relax_rest)
        if not decision.approved:
            self._emit(
                "assignment_rejected",
                now,
                volunteer_id=volunteer_id,
                shift_id=shift_id,
                reasons=[code.value for code in decision.codes],
            )
            return AssignmentResult(decision, None)
        volunteer = self._volunteer(volunteer_id)
        shift = self._shift(shift_id)
        snapshot = QualificationSnapshot(
            volunteer_id=volunteer_id,
            active_roles=volunteer.active_roles(),
            trainings=tuple(volunteer.trainings),
            recusals=tuple(volunteer.recusals),
            frozen_at=now,
        )
        assignment = Assignment(
            assignment_id=self.repo.next_id("A"),
            shift_id=shift_id,
            volunteer_id=volunteer_id,
            snapshot=snapshot,
            confirmed_at=now,
            emergency=emergency,
            rest_override=bool(decision.overrides),
        )
        self.repo.assignments[assignment.assignment_id] = assignment
        shift.filled += 1  # 同步更新占用
        self._emit(
            "assignment_confirmed",
            now,
            assignment_id=assignment.assignment_id,
            volunteer_id=volunteer_id,
            shift_id=shift_id,
            filled=shift.filled,
            emergency=emergency,
        )
        return AssignmentResult(decision, assignment)

    def _release(self, assignment: Assignment, now: datetime, reason: str) -> None:
        assignment.status = AssignmentStatus.RELEASED
        assignment.released_at = now
        assignment.release_reason = reason
        shift = self._shift(assignment.shift_id)
        shift.filled -= 1  # 同步更新占用
        self._emit(
            "assignment_released",
            now,
            assignment_id=assignment.assignment_id,
            volunteer_id=assignment.volunteer_id,
            shift_id=assignment.shift_id,
            filled=shift.filled,
            reason=reason,
        )

    # ---- 换班、请假、资格撤销、紧急补位 ----

    def swap(
        self,
        assignment_id: str,
        new_volunteer_id: str,
        *,
        now: datetime | None = None,
    ) -> SwapResult:
        """换班：先验证接替者，再原子地释放旧占用并确认新占用。"""
        now = self._now(now)
        previous = self._assignment(assignment_id)
        if previous.status is not AssignmentStatus.CONFIRMED:
            raise StateError(f"班次占用 {assignment_id} 已释放，不能换班")
        if previous.volunteer_id == new_volunteer_id:
            raise StateError("换班对象不能是原志愿者")
        decision = self.evaluate(
            new_volunteer_id, previous.shift_id, excluding_assignment_id=assignment_id
        )
        if not decision.approved:
            self._emit(
                "swap_rejected",
                now,
                assignment_id=assignment_id,
                new_volunteer_id=new_volunteer_id,
                reasons=[code.value for code in decision.codes],
            )
            return SwapResult(decision, previous, None)
        self._release(previous, now, f"换班：由 {new_volunteer_id} 接替")
        result = self.assign(new_volunteer_id, previous.shift_id, now=now)
        return SwapResult(decision, previous, result.assignment)

    def request_leave(
        self,
        assignment_id: str,
        reason: str,
        *,
        now: datetime | None = None,
    ) -> LeaveResult:
        """请假：释放占用并报告由此产生的岗位缺口。"""
        now = self._now(now)
        assignment = self._assignment(assignment_id)
        if assignment.status is not AssignmentStatus.CONFIRMED:
            raise StateError(f"班次占用 {assignment_id} 已释放，不能重复请假")
        self._release(assignment, now, f"请假：{reason}")
        shift = self._shift(assignment.shift_id)
        return LeaveResult(
            assignment=assignment,
            shift_id=shift.shift_id,
            gap=shift.headcount_needed - shift.filled,
        )

    def revoke_qualification(
        self,
        volunteer_id: str,
        role: Role,
        reason: str,
        *,
        now: datetime | None = None,
    ) -> RevokeResult:
        """撤销资格，并级联释放依赖该资格的已确认占用。"""
        now = self._now(now)
        volunteer = self._volunteer(volunteer_id)
        qualification = volunteer.qualifications.get(role)
        if qualification is None:
            raise NotFoundError(f"志愿者 {volunteer_id} 未持有{ROLE_LABELS[role]}资格")
        if qualification.status is QualificationStatus.REVOKED:
            raise StateError(f"志愿者 {volunteer_id} 的{ROLE_LABELS[role]}资格已撤销")
        volunteer.qualifications[role] = Qualification(
            role=role,
            status=QualificationStatus.REVOKED,
            granted_at=qualification.granted_at,
            revoked_at=now,
            revoke_reason=reason,
        )
        released: list[str] = []
        affected: list[str] = []
        for assignment in self._confirmed_assignments_of(volunteer_id):
            position = self._position(self._shift(assignment.shift_id).position_id)
            if position.required_role is role:
                self._release(assignment, now, f"资格撤销：{reason}")
                released.append(assignment.assignment_id)
                affected.append(assignment.shift_id)
        self._emit(
            "qualification_revoked",
            now,
            volunteer_id=volunteer_id,
            role=role.value,
            reason=reason,
            released_assignments=released,
        )
        return RevokeResult(volunteer_id, role, tuple(released), tuple(affected))

    def emergency_backfill(
        self,
        shift_id: str,
        *,
        volunteer_id: str | None = None,
        allow_rest_override: bool = False,
        now: datetime | None = None,
    ) -> BackfillResult:
        """紧急补位：指定或自动搜寻候选人，必要时可显式放宽休息间隔。"""
        now = self._now(now)
        shift = self._shift(shift_id)
        if shift.filled >= shift.headcount_needed:
            raise StateError(f"班次 {shift_id} 无缺口，无需补位")
        position = self._position(shift.position_id)
        if volunteer_id is not None:
            candidates = [volunteer_id]
        else:
            candidates = sorted(
                vid
                for vid, vol in self.repo.volunteers.items()
                if (q := vol.qualifications.get(position.required_role)) is not None
                and q.status is QualificationStatus.ACTIVE
            )
        attempts: list[CandidateAttempt] = []
        for candidate_id in candidates:
            result = self.assign(candidate_id, shift_id, emergency=True, now=now)
            attempts.append(CandidateAttempt(candidate_id, result.decision))
            if result.assignment is not None:
                return BackfillResult(result.decision, result.assignment, tuple(attempts))
            if allow_rest_override and result.decision.codes and all(
                code is RejectionCode.REST_INTERVAL_VIOLATION for code in result.decision.codes
            ):
                relaxed = self.assign(candidate_id, shift_id, emergency=True, relax_rest=True, now=now)
                attempts.append(CandidateAttempt(candidate_id, relaxed.decision))
                if relaxed.assignment is not None:
                    return BackfillResult(relaxed.decision, relaxed.assignment, tuple(attempts))
        summary = Decision(
            approved=False,
            reasons=tuple(
                Rejection(
                    RejectionCode.NO_ELIGIBLE_CANDIDATE,
                    f"候选人 {attempt.volunteer_id} 不合格：{attempt.decision.explain()}",
                    {"volunteer_id": attempt.volunteer_id},
                )
                for attempt in attempts
            ),
        )
        return BackfillResult(summary, None, tuple(attempts))

    # ---- 查询视图 ----

    def shift_status(self, shift_id: str) -> dict:
        shift = self._shift(shift_id)
        return {
            "shift_id": shift.shift_id,
            "position_id": shift.position_id,
            "start": shift.window.start.isoformat(),
            "end": shift.window.end.isoformat(),
            "headcount_needed": shift.headcount_needed,
            "filled": shift.filled,
            "gap": shift.headcount_needed - shift.filled,
            "assignments": [
                {
                    "assignment_id": item.assignment_id,
                    "volunteer_id": item.volunteer_id,
                    "status": item.status.value,
                    "emergency": item.emergency,
                }
                for item in self.repo.assignments.values()
                if item.shift_id == shift_id
            ],
        }

    def profile_view(self, volunteer_id: str, viewer: Viewer) -> dict:
        """按查看者权限裁剪后的个人资料视图。"""
        return profile_view(self._volunteer(volunteer_id), viewer)
