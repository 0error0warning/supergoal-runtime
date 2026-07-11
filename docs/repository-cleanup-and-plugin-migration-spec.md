# Supergoal 仓库整理与 Hermes 插件化迁移规范

## 0. 文档状态

- 状态：拟实施规范
- 适用仓库：
  - Supergoal：`/root/supergoal-runtime`
  - Hermes core：`/root/.hermes/hermes-agent`
- 目标：消除双仓双源、停止巨型 overlay/patch 维护，把 Supergoal 变成唯一源码位于独立仓库的 Hermes 插件，同时保留 `/supergoal resume` 立即继续真实回合的原生体验。
- 本规范不授权直接删除历史、强推分支或切换生产运行代码。所有切换必须经过本文定义的验证门。

---

## 1. 已确认问题

当前实现同时存在于 Hermes core 和 `supergoal-runtime`：

1. Hermes core 是实际运行代码，包含大量 Supergoal 专用修改。
2. `supergoal-runtime` 保存同一实现的 overlay、patch、测试和文档。
3. 两边都可编辑，缺少明确的单一事实源。
4. overlay 已经漂移：部分文件与 core 一致，部分不同，且仓库存在未提交修改。
5. `patches/supergoal-runtime.patch` 基于旧 Hermes 提交，不能对当前生产 core 正向或反向验证。
6. Supergoal 直接修改 `gateway/run.py`、`hermes_cli/goals.py`、工具执行、审批和压缩路径，使 Hermes 更新必须手工重放大量产品代码。

### 1.1 根因

根因不是“用了两个仓库”，而是边界错误：

- 独立仓库不是可直接安装、独立测试的产品包，而是 Hermes 文件副本集合。
- Hermes core 没有满足长运行控制器所需的通用扩展接口。
- Supergoal 通过修改宿主内部实现获得命令上下文、回合续跑、压缩迁移、工具策略和状态 UI。
- 运行时状态与 Hermes `/goal` 的内部状态模型高度耦合。

### 1.2 禁止继续采用的模式

迁移开始后禁止：

- 同时手工修改 core 文件和 `overlay/` 对应文件。
- 重新生成并继续扩大 10k 行级别的全量 patch。
- 把新的 Supergoal 逻辑继续加入 `GoalManager` 的 `mode == "supergoal"` 分支。
- 在 `gateway/run.py`、`model_tools.py`、`agent/tool_executor.py` 中新增 Supergoal 专用 import。
- 用 cron 模拟 `/supergoal resume` 或连续回合。
- 让插件自行绕开 Hermes FIFO、消息抢占、审批或会话持久化路径。

---

## 2. 最终架构决定

### 2.1 单一事实源

迁移完成后：

- `supergoal-runtime` 是 Supergoal 产品代码、状态 schema、测试、文档和发布物的唯一事实源。
- Hermes core 不保存 Supergoal 产品实现。
- Hermes core 只提供通用、名称无关、可供其他插件复用的宿主接口。
- 生产通过 Hermes 插件系统安装并启用 `supergoal-runtime`。

### 2.2 目标依赖方向

正确依赖方向：

```text
Hermes generic plugin ABI
          ↑
supergoal-runtime plugin
```

禁止方向：

```text
Hermes core → import hermes_cli.supergoal
Hermes core → 检查 mode == "supergoal"
Hermes core → 读取 Supergoal 私有数据库/schema
```

### 2.3 目标运行流

```text
用户 /supergoal start <mission>
  → Hermes 解析插件命令
  → 传入 CommandContext(session_id, surface, source, enqueue API)
  → 插件创建 GoalRun，保存 session binding
  → 插件返回 kickoff directive
  → Hermes 使用原生 FIFO/pending-input 启动真实回合

Agent 完成一个回合
  → Hermes 完成正常 turn finalization
  → 插件 post_tool_call 已记录工具证据
  → Hermes 调用通用 post-turn controller
  → Supergoal controller 读取状态、运行 judge/critic/gates
  → 返回 continue / pause / done directive
  → 主回复先正常送达
  → Hermes 再发送状态通知并按原生队列入下一回合
  → 若真实用户消息已到达，用户消息优先
```

### 2.4 `/goal` 边界

- 普通 `/goal` 保持 Hermes 上游实现。
- Supergoal 不再继承或嵌入 `GoalManager` 的大段内部逻辑。
- 允许在插件中保留一个窄的 `compat/hermes_goal.py` 兼容适配器，仅用于检测普通 `/goal` 是否活跃以及避免两个自主循环并发。
- 兼容适配器必须是单向依赖：插件依赖 Hermes 的公开/稳定接口；Hermes 不依赖插件。
- 若 Hermes 后续提供通用 session control lease，删除该兼容适配器。

---

## 3. Hermes core 最小扩展 ABI

纯插件迁移需要三个必需接口。接口必须通用，命名中不得出现 `supergoal`。

## 3.1 Context-aware slash command

### 目标

让插件命令获得当前会话和宿主队列上下文，同时保持旧 `handler(raw_args)` 插件兼容。

### 建议类型

```python
@dataclass(frozen=True)
class CommandContext:
    surface: str                 # cli | gateway | tui
    session_id: str
    platform: str
    source: Any | None
    task_id: str
    metadata: Mapping[str, Any]
    enqueue_followup: Callable[[str], Awaitable[bool]]
```

### 注册兼容规则

插件系统同时支持：

```python
def old_handler(raw_args: str) -> str | None: ...

def new_handler(ctx: CommandContext, raw_args: str) -> str | CommandResult | None: ...
```

推荐通过签名检查或显式 `context_aware=True` 区分，不得破坏现有插件。

### 安全约束

- `enqueue_followup` 必须走宿主原生 CLI pending-input 或 Gateway FIFO。
- 插件不能直接操作 `_running_agents`、adapter 私有队列或 SessionStore 私有字段。
- enqueue 必须绑定当前 `session_id`，会话已经旋转时应 fail closed。
- 命令 handler 超时、异常不得使 Gateway 崩溃。

## 3.2 Post-turn controller directive

### 目标

允许插件在完整 Agent 回合结束后决定是否继续，但由 Hermes 宿主执行续跑和投递。

### 建议注册接口

```python
ctx.register_turn_controller(
    name="supergoal-runtime",
    handler=after_turn,
    priority=100,
)
```

### 建议上下文

```python
@dataclass(frozen=True)
class TurnControlContext:
    surface: str
    session_id: str
    platform: str
    source: Any | None
    task_id: str
    turn_id: str
    user_message: str
    final_response: str
    interrupted: bool
    background_processes: Sequence[Mapping[str, Any]]
```

### 建议返回值

```python
@dataclass(frozen=True)
class TurnDirective:
    action: Literal["noop", "continue", "pause", "done"]
    continuation_prompt: str | None = None
    notice: str | None = None
    silent: bool = False
    dedupe_key: str | None = None
    state_version: int | None = None
```

### 执行顺序

1. Agent 正常产生最终回复。
2. `post_llm_call` 等观察型 hook 正常执行。
3. 宿主调用 turn controllers；controller 只能返回 directive，不直接投递。
4. 当前 Agent 主回复先完成投递。
5. 宿主处理 notice。
6. `action == continue` 时，通过原生 FIFO/pending-input 入队。
7. 已排队的真实用户消息优先于自动 continuation。

### 宿主不变量

- 每个物理回合最多接受一个 continuation directive。
- stale `session_id`、重复 `dedupe_key`、旧 `state_version` 不得再次入队。
- controller 异常等价于 `noop`，但必须记录错误。
- controller 不得修改历史消息或系统提示，避免破坏 prompt cache。
- `pause`、`done`、`noop` 不得产生隐藏的自续跑。
- Gateway、CLI、TUI 必须共享同一 directive 语义。

## 3.3 Session rotation hook

### 目标

在 compression、resume migration 或其他物理 session 轮换后，让插件把新 session 绑定到原逻辑 run。

### 建议 hook

```python
on_session_rotate(
    old_session_id: str,
    new_session_id: str,
    reason: str,
    parent_session_id: str | None,
    surface: str,
)
```

### 触发规则

- 新 session 已成功持久化后触发。
- 下一次模型回合开始前触发完成。
- compression 必须触发。
- `/new` 不继承任务，继续使用现有 `on_session_reset` 语义；不得错误绑定旧 Supergoal。
- branch/fork 是否继承由 `reason` 和插件策略决定，默认不继承，除非显式请求。

## 3.4 非第一阶段必需：结构化命令 UI

原生 Telegram/Discord 状态卡不应阻塞插件提取。

第一阶段：

- `/supergoal status` 返回紧凑文本。
- pause/resume/clear 使用普通命令。

第二阶段可新增通用返回协议：

```python
@dataclass
class CommandResult:
    text: str
    visibility: Literal["reply", "silent"] = "reply"
    controls: Sequence[CommandControl] = ()
    update_key: str | None = None
```

该接口必须是平台中立的，adapter 自行渲染；不得增加 Supergoal 专用 adapter hook。

---

## 4. Supergoal 插件仓库目标结构

保留仓库名 `supergoal-runtime`，避免无收益的远程仓库迁移。仓库根目录直接符合 Hermes directory plugin 规范。

```text
supergoal-runtime/
├── plugin.yaml
├── __init__.py
├── pyproject.toml
├── README.md
├── LICENSE
├── supergoal_runtime/
│   ├── __init__.py
│   ├── plugin.py
│   ├── command.py
│   ├── controller.py
│   ├── domain.py
│   ├── evaluators.py
│   ├── evidence.py
│   ├── gates.py
│   ├── policy.py
│   ├── projection.py
│   ├── prompts.py
│   ├── store.py
│   ├── migration.py
│   ├── config.py
│   ├── rendering.py
│   └── compat/
│       ├── __init__.py
│       └── hermes_goal.py
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── replay/
│   ├── contract/
│   └── fixtures/
├── docs/
│   ├── architecture.md
│   ├── principles.md
│   ├── configuration.md
│   ├── state-schema.md
│   ├── host-contract.md
│   ├── migration.md
│   └── run-analysis.md
├── scripts/
│   ├── verify_against_hermes.py
│   ├── migrate_legacy_state.py
│   └── smoke_test.py
└── .github/workflows/
    └── ci.yml
```

### 4.1 根 `__init__.py`

只做轻量注册转发：

```python
from supergoal_runtime.plugin import register

__all__ = ["register"]
```

不得在 import 阶段：

- 打开数据库；
- 调用 LLM；
- 修改 Hermes 全局变量；
- 导入 Gateway 私有实现；
- 启动线程或任务。

### 4.2 `plugin.yaml`

必须声明：

- 插件名称与版本；
- 提供的 hooks；
- 不提供新的模型工具，避免每次 API 调用增加工具 schema；
- Hermes 最低兼容版本/ABI 版本；
- 无需秘密环境变量。

示例方向：

```yaml
name: supergoal-runtime
version: 1.0.0
description: Long-running mission controller for Hermes Agent
provides_hooks:
  - pre_tool_call
  - post_tool_call
  - on_session_rotate
```

slash command 和 turn controller 由 `register(ctx)` 注册。

---

## 5. 插件内部职责边界

## 5.1 `domain.py`

只包含：

- dataclass / enum / typed result；
- 无 I/O；
- 无 Hermes import；
- 无全局状态。

核心对象：

- `GoalRun`
- `IntentContract`
- `PlanStep`
- `GoalGate`
- `EvidenceRef`
- `ResearchFinding`
- `HypothesisRecord`
- `ActionProposal`
- `ControllerDecision`
- `PermissionContract`

## 5.2 `store.py`

Supergoal 使用独立、profile-scoped 数据库：

```text
${HERMES_HOME}/supergoal/state.db
```

禁止：

- 写插件源码目录；
- 继续把新状态写入 Hermes `state_meta`；
- 使用 import-time 固定默认路径；
- 在测试中接触真实 `~/.hermes`。

建议 SQLite 表：

```text
schema_meta
runs
session_bindings
events
```

最低要求：

- WAL；
- foreign_keys=ON；
- 显式事务；
- schema version；
- 幂等 migration；
- `session_id -> goal_run_id` 唯一绑定；
- event append 与 run state 更新在同一事务边界或有明确恢复规则。

## 5.3 `controller.py`

唯一负责：

```text
Observe → Project → Evaluate → Reconcile → Decide → Persist
```

controller 不得：

- 直接向 Telegram/Discord 发送消息；
- 直接访问 Gateway FIFO；
- 修改 Hermes transcript；
- 隐式启动下一轮。

它只返回宿主 `TurnDirective`。

## 5.4 `policy.py`

通过 Hermes `pre_tool_call` hook 实现。

要求：

- supervised 模式 fail closed；
- full_auto 不得绕过 Hermes 硬安全边界；
- 插件策略只能进一步限制或请求审批，不能取消宿主明确要求的审批；
- 返回 Hermes 已支持的 block/approve directive；
- 不再修改 `agent/tool_executor.py`、`model_tools.py`、`tools/approval.py`。

## 5.5 `evidence.py`

通过 `post_tool_call` 记录：

- session_id / goal_run_id；
- turn_id / tool_call_id；
- tool name / args 的安全摘要；
- status / error_type；
- artifact path / locator；
- evidence hash；
- trust level；
- observed_at。

禁止把完整秘密、token 或未脱敏工具输出写入数据库。

## 5.6 `evaluators.py`

通过 `ctx.llm` 调用宿主模型，不自行管理 API key。

角色分离：

- completion judge；
- strategic critic；
- 可选 artifact verifier。

要求：

- typed result；
- schema validation；
- timeout；
- parse failure 计数；
- judge/critic 失败默认继续或暂停的策略必须显式配置；
- deterministic gates 优先于 LLM 自报完成。

## 5.7 `command.py`

实现：

```text
/supergoal
/supergoal start <mission>
/supergoal status
/supergoal pause
/supergoal resume
/supergoal wait <pid|session|duration>
/supergoal unwait
/supergoal replan
/supergoal clear
```

必须满足：

- plain `/supergoal <text>` 不自动启动长任务；必须显式 `start`；
- `resume` 不仅修改状态，还必须通过 `CommandContext.enqueue_followup()` 入真实下一轮；
- active `/goal` 与 active Supergoal 冲突时拒绝启动并给出明确提示；
- 幂等 pause/clear；
- status 不触发 Agent 回合；
- 所有命令都绑定当前 session，而不是全局最近任务。

---

## 6. 状态与迁移规范

## 6.1 新状态身份

- `goal_run_id`：逻辑任务身份，由插件生成并永久稳定。
- `session_id`：Hermes 物理上下文版本，可因 compression 变化。
- `session_bindings`：把多个物理 session 绑定到一个逻辑 run。

## 6.2 旧状态来源

旧状态可能位于 Hermes `state.db` 的：

```text
goal_run:{goal_run_id}
goal_session:{session_id}
goal_events:{goal_run_id}
goal:{session_id}
```

## 6.3 一次性迁移器

`scripts/migrate_legacy_state.py` 必须：

1. 只读打开 Hermes 旧数据库。
2. 备份目标插件数据库。
3. 解析旧 JSON 状态。
4. 写入新 schema。
5. 保留原 `goal_run_id`。
6. 重建 session bindings 和 event ledger。
7. 对每个 run 输出结构化迁移结果，不打印敏感内容。
8. 写 migration marker，使重复执行安全。
9. 默认不删除旧键。

## 6.4 保留期

- 旧 Hermes 状态至少保留两个稳定插件版本。
- 插件稳定运行并通过恢复演练后，才可另行决定清理旧键。
- 清理必须独立命令和独立确认，不包含在正常安装或更新中。

---

## 7. 当前仓库整理流程

## 7.1 冻结规则

从迁移开始至完成：

- Hermes core 是当前生产行为参考。
- `supergoal-runtime` 旧 overlay 不再作为自动可应用补丁。
- 新功能只进入插件架构分支，不再扩展旧 patch。
- 旧 overlay 仅用于对账和回归证据。

## 7.2 先保存现状

在两个仓库分别建立：

```text
backup/pre-supergoal-plugin-migration-<timestamp>
```

在 `supergoal-runtime`：

- 保存当前 7 个未提交文件的 patch；
- 逐文件与生产 core 做语义对账；
- 形成一个“legacy overlay final snapshot”提交；
- 打 tag：`legacy-overlay-final`。

不得把未知 dirty worktree 直接 reset 或覆盖。

## 7.3 分支模型

`supergoal-runtime`：

```text
main                         # 最终插件主线
archive/legacy-overlay       # 旧 overlay/patch 最终快照
migration/plugin-runtime     # 插件化开发分支
```

Hermes core：

```text
local-hermes-customizations
feature/plugin-turn-control-abi
backup/pre-supergoal-extraction-<timestamp>
```

## 7.4 删除旧目录的时机

以下条件全部满足前，不删除：

- shadow 插件通过集成测试；
- 生产 smoke test 通过；
- legacy state migration 验证通过；
- `/supergoal resume` 真实续跑通过；
- compression 绑定通过；
- rollback 演练通过。

满足后，在 `supergoal-runtime/main` 删除：

```text
overlay/
patches/
scripts/apply.sh
```

历史通过 `archive/legacy-overlay` 和 `legacy-overlay-final` 保留，不继续污染主线。

---

## 8. 分阶段实施计划

## Phase 0：审计与快照

目标：停止丢失信息，不改变生产行为。

任务：

1. 备份两个仓库分支/tag。
2. 导出两个仓库 dirty patch。
3. 对账 7 个未提交 overlay 文件。
4. 更新文件所有权清单：哪些迁入插件，哪些属于 host ABI，哪些删除。
5. 记录当前 focused tests 基线。

退出标准：

- 两仓 worktree 状态可解释；
- 无未知未提交改动；
- 可以从备份恢复。

## Phase 1：宿主 ABI spike

目标：先验证插件能否完成原生续跑，不先搬全部代码。

只做最小实验：

1. context-aware `/sgx start` 插件命令能取得 session_id。
2. 插件 turn controller 能在一次回合后返回 continuation。
3. Gateway 使用原生 FIFO 启动第二个真实回合。
4. 用户消息与 continuation 同时存在时，用户优先。
5. compression 后 `on_session_rotate` 被调用。

spike 放在临时分支，结论必须写：

```text
VALIDATED | PARTIAL | INVALIDATED
```

退出标准：上述五项全部 VALIDATED，否则不得继续大规模迁移。

## Phase 2：插件骨架与独立存储

TDD 顺序：

1. 写 plugin discovery 失败测试。
2. 建 `plugin.yaml`、根 `__init__.py`、`plugin.py`。
3. 写 profile isolation 失败测试。
4. 实现独立 SQLite store。
5. 写 legacy state migration 测试。
6. 实现幂等迁移器。

退出标准：

- 临时 `HERMES_HOME` 下可安装/启用插件；
- 不写真实 Hermes home；
- 两个 profile 数据完全隔离。

## Phase 3：纯领域模块迁移

迁移顺序：

1. domain
2. gates
3. evidence model
4. projection
5. controller
6. evaluators
7. prompts/rendering

每迁移一个模块：

- 先复制对应测试并让 import 失败；
- 修改 import 到插件包；
- 保持测试绿色；
- 删除对 `hermes_cli.goals` 私有函数的依赖；
- 需要宿主数据时通过明确 adapter/context 注入。

退出标准：

- 纯模块可在不导入 Hermes Gateway 的情况下运行；
- replay tests 在插件仓库内通过。

## Phase 4：工具 policy/evidence 脱钩

1. 用 `pre_tool_call` 接管 policy。
2. 用 `post_tool_call` 接管 evidence。
3. 加单次触发、并发会话和敏感数据脱敏测试。
4. 在 shadow 模式确认工具调用产生相同 gate evidence。

退出标准：

- 插件不需要修改 tool executor/model_tools/approval；
- evidence 和 policy 行为与现有实现等价或更严格。

## Phase 5：命令与 controller 闭环

shadow 阶段使用临时命令：

```text
/sgx start
/sgx status
/sgx pause
/sgx resume
/sgx clear
```

原因：内置 `/supergoal` 尚在 core，插件命令冲突会被拒绝。

验证：

- kickoff；
- post-turn continue；
- pause；
- resume 立即真实续跑；
- wait barrier；
- terminal blocked outcome；
- done；
- user preemption；
- gateway restart 后状态恢复。

## Phase 6：生产切换

切换前：

1. 暂停所有 active Supergoal。
2. 备份 Hermes `state.db`、插件 DB、config 和两个 git 分支。
3. 部署并启用 host ABI。
4. 以 shadow `/sgx` 完成生产 smoke。
5. 运行 legacy state migration dry-run。
6. 执行正式迁移。

切换：

1. 删除 core 中内置 `/supergoal` 命令和产品实现。
2. 插件从 `/sgx` 切到 `/supergoal`，保留 `/sgoal` alias。
3. 重启 Gateway。
4. 验证 start/status/pause/resume/clear/compression。
5. 保留旧状态和 rollback branch。

## Phase 7：清理与上游化

1. 从 core 删除所有 Supergoal 专用 import、条件和测试。
2. 更新 `LOCAL_NOTES.md`，把 52+ Supergoal patch queue 替换为：
   - 通用 host ABI 小提交；
   - 外置插件版本/安装方式。
3. 向 Hermes 上游分别提交三个小型通用 ABI PR；不要提交整套 Supergoal 产品。
4. 插件 CI 增加最新 upstream main 兼容测试。
5. 删除 `supergoal-runtime/main` 的 overlay/patch/apply 脚本。

---

## 9. 测试规范

## 9.1 单元测试

必须覆盖：

- state serialization/schema migration；
- deterministic gates；
- evidence trust 分类；
- policy allow/block/approve；
- controller continue/pause/done；
- stale directive 去重；
- wait barrier 不计回合；
- terminal blockers 不映射为成功 done；
- normal no-edge outcome。

## 9.2 宿主 contract tests

Hermes core：

- old plugin command signature 仍可用；
- context-aware handler 获得正确 session_id；
- async/sync handler 均可用；
- turn controller 异常隔离；
- 每回合最多一个 continuation；
- FIFO 用户优先；
- stale session 不入队；
- session rotate hook 在 compression 后触发。

## 9.3 插件集成测试

使用临时 `HERMES_HOME` 和真实 plugin discovery：

- install/enable/disable；
- 命令注册；
- 独立 DB；
- pre/post tool hooks；
- ctx.llm fake provider；
- Gateway fake adapter FIFO；
- CLI pending input；
- compression session binding。

## 9.4 Replay tests

保留现有真实失败 trace，并迁入：

```text
tests/replay/
tests/fixtures/
```

Replay 输出只断言状态与 gate 不变量，不依赖完整文本快照。

## 9.5 CI 兼容矩阵

至少：

```text
Hermes pinned-compatible-ref
Hermes latest origin/main
Python supported minimum
Python current production version
```

CI 流程：

1. clone 指定 Hermes ref；
2. 创建临时 HERMES_HOME；
3. 安装/链接插件；
4. 启用插件；
5. 跑 host contract + plugin integration + replay tests；
6. `python -m compileall`；
7. `git diff --check`。

禁止继续用“把巨型 patch apply 到固定旧提交”作为主 CI。

---

## 10. 生产验收门

全部通过才允许宣布迁移完成：

### 仓库边界

- [ ] `supergoal-runtime` 是唯一产品源码。
- [ ] Hermes core 产品代码不包含 `supergoal` 专用实现。
- [ ] core 不 import 插件包。
- [ ] 插件禁用后 Hermes 正常启动和运行。
- [ ] `overlay/` 和 patch 不在插件主线。

### 行为

- [ ] `/supergoal start` 启动真实首轮。
- [ ] `/supergoal resume` 立即触发真实下一轮，不使用 cron。
- [ ] 用户消息优先于自动 continuation。
- [ ] pause/wait 不消耗回合、不调用 judge。
- [ ] clear 幂等并清理待续跑事件。
- [ ] done 不重复写完成事件。
- [ ] blocked/needs-user 进入 paused/partial-blocked，不是假成功。
- [ ] compression 后保持同一 goal_run_id。
- [ ] Gateway restart 后能恢复 active/paused 状态。

### 安全与证据

- [ ] policy 走标准 pre_tool_call。
- [ ] evidence 走标准 post_tool_call。
- [ ] full_auto 不绕过宿主硬安全边界。
- [ ] 数据库不保存完整 secret/tool token。
- [ ] deterministic gates 不能被 critic prose 绕过。

### 更新维护

- [ ] Hermes 更新不再需要重放 Supergoal 产品提交。
- [ ] host ABI 提交小、通用、可单独测试。
- [ ] 插件 CI 对 latest upstream main 通过。
- [ ] rollback 演练通过。

---

## 11. 回滚方案

生产切换必须保留：

- core `backup/pre-supergoal-extraction-*`；
- plugin `legacy-overlay-final`；
- Hermes 旧 `state.db`；
- 新插件 DB 备份；
- 切换前 config 备份。

回滚步骤：

1. 停止新 Supergoal continuation 入队。
2. 禁用 `supergoal-runtime` 插件。
3. 恢复 core backup branch。
4. 恢复切换前 config。
5. 重启 Gateway。
6. 验证旧 `/supergoal status` 和普通聊天。
7. 不删除新插件 DB，保留用于问题分析。

回滚不得依赖反向应用旧巨型 patch。

---

## 12. 提交拆分规范

Hermes core 建议提交：

```text
feat(plugins): add context-aware slash command handlers
feat(plugins): add post-turn controller directives
feat(plugins): emit session rotation hooks
refactor(local): remove embedded supergoal runtime
```

Supergoal repo 建议提交：

```text
chore(repo): snapshot final legacy overlay state
refactor(plugin): establish standalone Hermes plugin skeleton
feat(store): add profile-scoped supergoal database
feat(migration): import legacy Hermes goal state
refactor(runtime): move mission domain and gates into plugin
feat(runtime): add plugin turn controller
feat(policy): enforce permissions through pre-tool hook
feat(evidence): observe tool evidence through post-tool hook
feat(commands): add context-aware supergoal commands
feat(ci): test plugin against pinned and latest Hermes
chore(repo): retire overlay patch distribution
```

规则：

- 一个提交只表达一个架构主题。
- 先测试后实现。
- 不把 host ABI、产品迁移和旧代码删除塞进一个提交。
- 不用 `wip` 作为最终提交标题。
- 不在同一提交中改无关 Hermes 本地功能。

---

## 13. 完成定义

本项目不是在“插件能 import”时完成，而是在以下状态完成：

```text
Hermes 可正常升级
+ Supergoal 只有一份源码
+ /supergoal resume 仍是原生真实续跑
+ 状态跨 compression/restart 稳定
+ policy/evidence 使用通用 hook
+ 禁用插件即可完全移除功能
+ 不再维护巨型 patch
```

在此之前，旧 overlay 只能被视为迁移输入和回归资料，不能继续被称为正式分发方式。
