# Supergoal 维护审查：2026-10-04

本轮完成了腾讯云生产环境的只读核验、两份插件源码的比较，以及本地隔离复现。开发起点应先对齐服务器上的真实源码，再修复生命周期和证据判定，最后调整任务验收架构。

## 实际基线

| 对象 | 核验结果 |
| --- | --- |
| 腾讯云 Gateway | Hermes **v0.21.3**，服务 active/running |
| 运行发布目录 | `/var/lib/upi-hermes/.hermes/releases/hermes-v0.21.3-251bedc-mt2` |
| 运行 Python 环境 | `/var/lib/upi-hermes/.hermes/venvs/hermes-v0213-251bedc-mt2` |
| Gateway / serve 健康检查 | 分别返回 HTTP 200 |
| 安装的 Supergoal | manifest 版本 **1.1.1**，配置中已启用 |
| 服务器插件 Git 分支 | `codex/lifecycle-fix` |
| 服务器插件 HEAD | `d306e42b52e8ce123c38d44bdf865abcbefee09b` |
| 刚克隆的 GitHub main | **1.0.0**，`cd05a52f9918c9f9e9dbe5b3aab9abe3fd0438ac`，最后一次提交日期 2026-07-12 |

`C:\Users\h1477\Documents\hermes\docs\CURRENT_PRODUCTION.md` 中的 0.21.0 已不代表实时生产状态。

插件状态库以只读方式核验：16 个运行记录，其中 cleared 10、done 2、migrated 4；没有 active 记录，续跑 outbox 为 0 条。这些聚合结果不证明当前各聊天入口都能正常启动新任务。本轮没有通过生产聊天发起任务。

## 首先处理源码分叉

服务器 HEAD 比 GitHub main 多出 5 个提交，涉及状态隔离、策略误拦截、原子迁移、持久化续跑与生命周期安全，以及压缩后会话关联。净差异为 20 个文件，新增 2288 行、删除 227 行。

服务器工作区还有未提交修改：`supergoal_runtime/command.py`、`supergoal_runtime/plugin.py`、两份历史文档，以及两个源码备份文件。上述两个 Python 文件增加了宿主接口探测与不支持接口时的提示。因此，部署内容也不能仅由 HEAD 重建。

已在本机保留以下开发材料：

- 已提交历史：Git 引用 `refs/remotes/production/codex/lifecycle-fix`，指向上述服务器 HEAD。
- 服务器插件与相关宿主接口的源码快照：`.git/supergoal-audit/2026-10-04/snapshot/`。
- 两个 Python 文件相对服务器 HEAD 的补丁：`.git/supergoal-audit/2026-10-04/uncommitted-source.patch`。
- 复现输出：`.git/supergoal-audit/2026-10-04/probe-output.txt`。
- 快照校验清单：`.git/supergoal-audit/2026-10-04/SHA256SUMS.json`。

这些材料放在本地 Git 元数据目录中，便于核对服务器定制；审查时工作区在 main，后续本地修正已建立 `codex/generic-runtime`。已保存的生产源码不应在整理前直接发布到公开仓库。

## Hermes 兼容性结论

服务器的定制宿主包含 `hermes_cli/plugins_turn_control.py`，并从 `hermes_cli.plugins` 导出 `CommandContext`、`TurnControlContext` 和 `TurnDirective`。`PluginContext.register_command` 支持 `context_aware` 与 `busy_safe_subcommands`；`register_turn_controller` 支持 `continuation_provider`。

对照官方固定标签源码，v0.21.3（`v2026.9.14`）和 v0.21.5（`v2026.9.24`）的 `hermes_cli/plugins.py` 均没有上述 turn-controller 接口，`register_command` 也没有这两个参数。对应的 [PR #63208](https://github.com/NousResearch/hermes-agent/pull/63208) 在核验时仍为 Open。

来源：[官方 v0.21.3 插件接口](https://raw.githubusercontent.com/NousResearch/hermes-agent/v2026.9.14/hermes_cli/plugins.py)、[官方 v0.21.5 插件接口](https://raw.githubusercontent.com/NousResearch/hermes-agent/v2026.9.24/hermes_cli/plugins.py)。

因此，兼容性取决于宿主能力和携带的定制补丁，不能只靠 `hermes_min_version` 判断。服务器未提交的降级逻辑会在缺少 turn-controller 时只注册 hooks，并在缺少命令上下文时返回不可用提示；这属于明确的功能限制，不能算完整自主续跑兼容。

当前 CI 在最新官方 main 上强行 cherry-pick 旧的三份 ABI 提交，尚未表达真实生产基线，也没有分别验证官方宿主和带定制接口的宿主。

## 已复现的问题

以下四项均在 GitHub 1.0.0 和下载的服务器 1.1.1 源码中复现。每次使用独立临时数据库、合成工具结果和本地 evaluator，没有调用真实模型或生产任务。

### P1：真实工具证据的投影依赖助手正文

复现：记录一次成功写文件和一次成功测试的 `post_tool_call`，然后用 `Task is complete.` 作为最终响应，judge 返回 done。

结果：证据表中已有两个 `tool_evidence_observed`，运行状态的 `evidence_layers` 却为空，G3 仍 pending，返回 continue。

原因：`RuntimeManager.after_turn` 只有在助手正文被解析出新的观察事件时，才投影持久化证据。1.0.0 位于 `supergoal_runtime/runtime.py:283`，服务器源码位于同文件的 `after_turn` 中。

修复方向：每个有效回合都先投影尚未处理的持久化工具事件；助手正文不能决定工具证据是否可见。补充不含验证关键词、文件路径或研究词的最终响应回归。

### P1：重复回合不具备持久化幂等性

复现：对同一运行连续两次调用 `after_turn`，传入相同的 `turn_id`。

结果：`turns_used` 从 0 变为 2；两个返回值的 dedupe key 和 state version 都不同。服务器的 revision/CAS 能解决并发覆盖，但没有拦截顺序重放的同一个回合。

修复方向：把 `(goal_run_id, turn_id)` 作为事务内的唯一处理标识，重复回合不增加预算、不重复调用 evaluator、不生成第二次续跑。需要定义空 turn ID 的处理约定。

### P2：clear 后同一会话不能开始新任务

复现：`start first task` → `clear` → `start second task`。

结果：第二次 start 返回 `A /sgx supergoal is already bound to this session.`。

原因：clear 保留当前 session binding，而 start 对任何已绑定状态都拒绝，包括 cleared。完成状态的复用也需要纳入同一生命周期设计。

修复方向：在清理/替换事务中关闭旧绑定和续跑，保留历史运行；新任务创建新的逻辑运行 ID，并绑定当前会话。

### P2：通用任务被关键词套入交易验收

复现：为 `Build an edge AI scheduler` 生成默认 gates。

结果：得到 G1、G2、SG-1、SG-2、SG-3、SG-4、G3、G4；其中 SG gates 要求策略假设、基线实验和 no-edge 归因。

原因：`supergoal_runtime/gates.py:54` 通过 `edge`、`strategy`、`hypothesis` 等子串判断策略任务。

修复方向：通用任务使用明确的验收合同；交易研究等领域要求由显式任务类型或领域策略提供。不要让一个单词决定整套阻塞条件。

## 架构债务

1. **两套决策路径。** `SupergoalController` 描述 observe/project/evaluate/reconcile/decide/render，但实际插件调用 `RuntimeManager.after_turn`；运行代码未调用该 Controller，只有直接单元测试覆盖它。应统一实际决策路径，让回放、测试和生产执行同一条管线。
2. **领域逻辑进入通用内核。** 研究充分性、策略假设数量、交易归因和基础设施惯性等规则混在通用 gates、投影和 domain 中，增加任务误分类与完成判定成本。保留一个轻量控制器，通过明确合同选择验收策略。
3. **状态库版本不足以表达结构。** 两个插件版本都声明数据库 schema 2，但服务器额外拥有 `continuation_outbox` 和 revision 相关逻辑。旧版本的版本检查不能识别完整能力差异；新设计需要明确结构迁移和降级边界。
4. **宿主适配分散。** 命令参数过滤、旧 enqueue 回退、TurnDirective 回退和压缩 lineage 查询散落在不同位置。集中为小型宿主适配层，在注册阶段给出能力诊断，保留已具备的 outbox、CAS 和会话隔离。

## 本地验证与局限

- 本地环境：Windows、Python 3.13.5、pytest 9.0.1。
- GitHub main：使用 UTF-8 模式运行 unit/integration/replay，**51 passed，3 deselected**。
- 服务器源码快照：使用 UTF-8 模式运行 unit/integration，**51 passed，3 deselected**。这与上一项的测试范围不同，数量相同不代表覆盖相同。
- 两个 deselected 用例需要完整 Hermes 的 profile ContextVar 接口；本机该目录是运维工作区，没有安装完整宿主测试环境。
- PID 等待用例在首次 Windows 测试中触发 KeyboardInterrupt。`_pid_alive` 使用 `os.kill(pid, 0)`，需要 Windows/POSIX 分别实现只读进程检查；其后测试排除了该用例。
- 默认编码运行还复现了 GBK 解码 UTF-8 JSONL fixture 失败。测试读取应显式指定 UTF-8；`python -X utf8` 是本轮的运行规避。
- 未运行完整宿主加载、Gateway/CLI/TUI 端到端任务或 ruff。ruff 当前未安装，本轮没有修改全局 Python 环境。
- 生产健康端点为 200；配置启用和文件核验不等同于所有入口的实际命令/续跑验收通过。

## 建议实施顺序

1. **整理真实开发基线。** 从保存的生产提交建立本地开发分支，逐项纳入两份未提交源码改动，记录宿主接口与插件版本；把生产已有的安全修复保留为可重建提交。
2. **修复验收和生命周期。** 先处理证据投影、回合幂等性、clear/完成后的会话复用，补充针对真实 RuntimeManager 的回归。修复 Windows 开发环境的 PID 检查和 UTF-8 fixture 读取。
3. **统一内核与宿主适配。** 保留持久化续跑、事务与 CAS；合并重复决策路径，集中宿主能力探测。领域验收从通用控制逻辑中移出。
4. **建立固定版本兼容验证。** 先验证实际 v0.21.3 定制宿主；对纯官方宿主明确记录能力缺失，随后在隔离候选中验证更新目标。CI 使用可重建输入和接口行为测试。
5. **交付候选版本。** 状态迁移、正常完成、暂停抢占、重启恢复、压缩关联和各聊天入口通过后，生成具体候选与回滚材料，再安排生产发布。

本轮交付是审查报告、生产源码保全和本地复现证据；生产发布、插件文件和数据库未变更。

## 后续本地修正：通用验收

用户指出上述领域规则本身违反通用架构边界。随后已从生产提交和保存的接口补丁建立本地候选，移除内置领域流程，增加显式证据要求、旧 gate 退役、Critic 合同保护和工具账本独立投影。详情与未处理事项见 [通用任务验收修正](generic-acceptance.md)。上文的复现结果描述修正前基线；不能视为修正后行为。
