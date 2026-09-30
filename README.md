# 活动志愿者排班资格

本项目维护活动志愿者排班资格的领域约定，并提供一套**仅依赖 Python 标准库**的完整后端：
保存教师、家长、学生助理的角色资格、安全培训有效期、回避关系、可用时段与岗位需求；
确认排班时冻结资格并检查休息间隔与人数；换班、请假、资格撤销、紧急补位同步更新占用。

## 领域不变量

- **资格快照**：确认排班时冻结全员资格/培训/回避/可用时段，历史决定可审计。
- **回避关系**：志愿者与岗位关联供应商存在回避关系时不得排该岗。
- **休息间隔**：相邻班次必须满足岗位最小休息时长；跨午夜班次按**活动本地时区**
  在连续分钟轴上计算（`end_minutes` 可超过 1440）。
- **紧急补位**：岗位出现缺口时自动挑选当前合格者，落选原因可解释。
- 其余规则：安全培训须在有效期内、可用时段须完整覆盖班次、角色须匹配、
  人数须满足岗位需求；任何拒绝都返回**结构化、可逐条解释**的原因列表。
- **资料按权限隔离**：协调员与本人可见完整资料，其他志愿者只见排班必需的最小字段。

## 目录

- `domain/contract.json`：领域角色、状态、约束和样例。
- `src/domain_contract/`：契约读取与确定性校验。
- `src/volunteer_scheduling/`：排班后端。
  - `models.py`：角色、实体与本地时区时间窗（支持跨午夜）。
  - `errors.py`：拒绝原因码与 `Rejection`（可解释决定）。
  - `eligibility.py`：纯领域规则（资格/培训/回避/时段/休息/人数）。
  - `repository.py`：SQLite 持久化。
  - `permissions.py`：身份模型与个人资料隔离。
  - `services.py`：排班、确认冻结、换班、请假、撤销、紧急补位用例。
  - `http_app.py`：JSON HTTP API（标准库实现）。
- `tools/check_contract.py`：命令行摘要检查。
- `tests/`：契约回归 + 领域用例 + HTTP 端到端测试。

## 验证

```bash
# 领域 + 排班 + HTTP 测试
python3 -m unittest discover -s tests -v

# 编译检查
python3 -m compileall -q src tools tests

# 契约摘要
python3 tools/check_contract.py domain/contract.json
```

## 运行 HTTP 服务

```bash
PYTHONPATH=src python3 -c \
  "from volunteer_scheduling.http_app import run; run(database='scheduling.sqlite3', port=8000)"
```

请求头 `X-Principal: coordinator` 或 `X-Principal: volunteer:<person_id>` 标识身份。
业务拒绝统一返回 `422 {"error":{"code":"REJECTED","reasons":[...]}}`。

### 典型接口

| 方法 & 路径 | 说明 |
| --- | --- |
| `POST /people` | 登记教师/家长/学生助理 |
| `POST /people/{id}/qualification` | 设置资格与安全培训有效期（`status=revoked` 即撤销） |
| `POST /people/{id}/recusals` | 登记与供应商的回避关系 |
| `POST /people/{id}/availabilities` | 登记本地时区可用时段（分钟，可跨午夜） |
| `POST /events` / `POST /events/{id}/positions` | 建活动 / 岗位需求 |
| `POST /events/{id}/assignments` | 拟排班（草稿阶段） |
| `POST /events/{id}/confirm` | 确认排班：冻结资格 + 休息间隔 + 人数校验 |
| `POST /assignments/{id}/swap` | 换班（原子校验，不合格不留缺口） |
| `POST /assignments/{id}/leave` | 请假（本人或协调员），返回岗位缺口 |
| `POST /people/{id}/revoke` | 撤销资格并同步释放占用 |
| `POST /events/{id}/emergency-fill` | 紧急补位，自动挑选合格者 |
| `GET /events/{id}/roster` / `GET /people/{id}` | 值守表 / 按权限隔离的个人资料 |
