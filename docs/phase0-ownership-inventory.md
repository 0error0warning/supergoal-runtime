# Phase 0 文件所有权与双仓对账

审计时间：2026-07-11T14:16:07Z  
Hermes core：`/root/.hermes/hermes-agent` @ `4cbe58ce889ec683266cd017a1aaaad620c47bde`  
Supergoal runtime：`/root/supergoal-runtime` @ `094a3fab357284ed34b7b1410eebc59098ea6de2` + 当前 dirty worktree

## 结论

- 生产行为唯一参考仍是 Hermes core。
- 当前 7 个 tracked dirty 文件全部可解释，没有未知修改。
- dirty overlay 中的 terminal-blocker 修复已经进入生产 core；继续双写只会制造漂移。
- overlay 独立测试基线不是绿色：`249 passed, 22 failed, 10 errors`。主要原因是 Hermes API/测试夹具漂移，而不是当前生产 core 回归。
- 因此当前 overlay 应冻结为 `legacy overlay final snapshot`，后续新代码只进入插件化迁移分支。

## Dirty 文件归属

| 当前文件 | 当前修改含义 | 最终所有权 | 迁移动作 |
|---|---|---|---|
| `README.md` | 记录 `full_auto` policy 已知限制 | 插件仓库文档 | 保留并在插件实现后更新；旧 overlay 描述最终删除 |
| `overlay/hermes_cli/goals.py` | terminal blocker 不再映射为成功 done | **混合**：普通 Goal 属于 host；Supergoal 适配属于插件 | 不整体复制。普通 `/goal` 留在 Hermes；Supergoal 状态、judge/critic/controller 迁入插件；只保留窄 compat adapter |
| `overlay/hermes_cli/supergoal/controller.py` | done 前识别 terminal blocker | 插件 | 迁入插件 runtime/controller；删除 host 内 Supergoal 专用实现 |
| `overlay/hermes_cli/supergoal/domain.py` | `partial_blocked` 与 blocker 分类 | 插件 | 迁入插件 domain，保持纯模块、无 Gateway 依赖 |
| `overlay/hermes_cli/supergoal/gates.py` | G4 不接受被 policy 阻断的伪 done | 插件 | 迁入插件 gates；当前内容与生产 core 字节一致 |
| `overlay/tests/hermes_cli/test_goals.py` | terminal-blocker 回归测试 | **拆分** | 普通 Goal/host ABI contract tests 留 core；Supergoal 产品行为测试迁入插件 |
| `overlay/tests/supergoal_replay/conftest.py` | profile 隔离测试夹具 | 插件测试 | 迁入插件顶层测试夹具；当前放在子目录导致 sibling tests 找不到 fixture |
| `docs/repository-cleanup-and-plugin-migration-spec.md` | 插件迁移正式规范 | 插件仓库文档 | 保留，作为后续 Phase 1–7 的执行规范 |
| `docs/phase0-ownership-inventory.md` | 本次审计/所有权清单 | 插件仓库文档 | 保留并随迁移更新 |

## Overlay 与生产 core 语义差异

| 文件对 | 状态 | 关键差异 |
|---|---|---|
| `hermes_cli/goals.py` | 漂移 | overlay 3634 行，core 4507 行；core 额外包含 GoalContract、wait/session barrier、background process、migration 等上游能力；不能再整文件覆盖 |
| `supergoal/controller.py` | 漂移 | 同一 controller 的 `_evaluate` / `_reconcile_and_decide` 已继续演进 |
| `supergoal/domain.py` | 轻微漂移 | `TurnContext` 有差异；terminal-blocker 语义两边均存在 |
| `supergoal/gates.py` | 一致 | 当前字节完全一致 |
| `tests/hermes_cli/test_goals.py` | 严重漂移 | core 额外覆盖 contract、wait barrier、session trigger、migration；overlay 测试仍假设旧 judge 三元返回值 |
| `tests/supergoal_replay/conftest.py` | 一致 | 当前字节完全一致，但 fixture 作用域位置不适合独立 overlay suite |

详细 AST 对账和逐文件 diff：

- `/root/.hermes/backups/supergoal-plugin-migration/20260711T141607Z/semantic-reconciliation.json`
- `/root/.hermes/backups/supergoal-plugin-migration/20260711T141607Z/reconciliation-diffs/`

## Focused tests 基线

### 生产 Hermes core

```text
tests/hermes_cli/test_goals.py + tests/supergoal_replay/
175 passed in 46.89s

Gateway/registry/model-tools/hardline focused set
251 passed in 19.64s

py_compile
passed
```

合计：**426 passed，0 failed，0 errors**。

### 当前 dirty overlay 直接运行

```text
249 passed, 22 failed, 10 errors in 67.92s
```

已分类问题：

1. overlay tests 仍按 judge/parse 三元返回值断言，当前 Hermes 已返回扩展结果；
2. compression replay 的 `goal_run_id` 行为与当前 host 不一致；
3. `moa_tools` legacy map、YOLO approval 语义已随 host 演进；
4. `_isolate_hermes_home` 放在 `tests/supergoal_replay/conftest.py`，无法供 sibling tests 使用。

这组失败作为“停止继续维护 overlay patch”的基线证据，不应通过继续复制 core 文件来修补。

## Host ABI 所有权

以下能力属于 Hermes host 的通用扩展面，不属于 Supergoal 产品源码：

1. context-aware plugin command；
2. post-turn controller / continuation directive；
3. session rotation/compression hook；
4. FIFO 用户消息优先、stale directive 去重等 host contract tests。

接口和测试命名不得出现 `supergoal`。Supergoal 仅作为这些 ABI 的一个独立插件消费者。

## 删除清单（仅在 Phase 6/7 验证门通过后）

- `overlay/`
- `patches/`
- `scripts/apply.sh`
- Hermes core 内所有 Supergoal 专用 import、条件分支和产品测试

现在不得删除。历史由 `archive/legacy-overlay` 与 `legacy-overlay-final` 永久保留。
