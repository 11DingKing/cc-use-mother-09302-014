# 活动志愿者排班资格

本项目维护活动志愿者排班资格的领域约定、角色边界与样例数据，并提供一套完整后端，覆盖教师、家长、学生助理三类志愿者的资格、培训、回避、可用时段与岗位需求管理。

## 目录

- `domain/contract.json`：领域角色、状态、约束和样例。
- `src/domain_contract/`：契约读取与确定性校验。
- `src/roster_backend/`：排班后端服务（领域模型、资格评估、权限隔离、内存仓储）。
- `tools/check_contract.py`：命令行摘要检查。
- `tools/demo_scenario.py`：开场前排班处置流程演示。
- `tests/`：契约与后端回归测试。

## 后端能力（`roster_backend`）

- **档案管理**：注册教师/家长/学生助理志愿者，维护角色资格、培训记录（含有效期）、回避关系（供应商/岗位）、本地时区可用时段。
- **岗位与班次**：岗位声明所需角色、所需培训与关联供应商；班次按本地日期与起止时刻创建，结束时刻不晚于开始时刻时自动按跨午夜顺延到次日（`zoneinfo` 本地时区，不做 UTC 换算）。
- **确认排班**：`assign` 原子完成资格评估、冻结资格快照（`QualificationSnapshot`）并占用名额；校验角色资格、培训有效期、回避关系、可用时段并集覆盖、休息间隔（默认 480 分钟，可配置）与人数上限。
- **可解释拒绝**：`Decision` 汇总全部违反项（`RejectionCode` + 中文说明 + 结构化细节），`explain()` 输出人可读理由。
- **占用同步**：换班（`swap`）、请假（`request_leave`）、资格撤销（`revoke_qualification`，级联释放）、紧急补位（`emergency_backfill`，可指定或自动搜寻候选人，可显式放宽休息间隔并留痕）均同步更新班次 `filled`，并写入审计事件。
- **权限隔离**：`profile_view` 按查看者身份裁剪个人资料——协调员/本人可见全部字段，其他志愿者仅见姓名与角色，公众仅见姓名。

## 快速示例

```python
from datetime import date, datetime, time
from roster_backend import Role, RosterService

service = RosterService(tz_name="Asia/Shanghai")
service.create_position("检票岗", Role.PARENT, required_trainings=("safety",), vendor_id="VEN-FOOD", position_id="P-GATE")
service.create_shift("P-GATE", date(2026, 10, 1), time(22, 0), time(2, 0), headcount=1, shift_id="S-1")  # 跨午夜
service.register_volunteer("王芳", [Role.PARENT], volunteer_id="V-WANG")
result = service.assign("V-WANG", "S-1")
print(result.decision.explain())  # 拒绝：缺少岗位「检票岗」要求的安全培训记录
```

## 验证

测试命令：`python3 -m unittest discover -s tests -v`

编译命令：`python3 -m compileall -q src tools tests`

命令行检查：`python3 tools/check_contract.py domain/contract.json`

场景演示：`python3 tools/demo_scenario.py`
