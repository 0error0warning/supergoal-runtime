# v2 插件入口与验收审查

2026-10-05 起的候选实现，2026-10-06 补充可选工作策略。历史 r3 代码归档和成绩保持不变；代码尚未切入生产。各研究的源码与成绩见[研究索引](research-index.md)。

## 选择引擎

在兼容 Hermes 的独立 profile 中设置：

```yaml
plugins:
  enabled:
    - supergoal-runtime
  entries:
    supergoal-runtime:
      settings:
        engine: v2
```

插件仍由 `supergoal_runtime.plugin.register` 加载，注册原有的 `/supergoal`、`/sgx`、`/sgoal`。省略配置时仍使用 v1。v2 要求 context-aware 命令和 continuation-provider ABI；没有接口时不启动任务。正式主机是带通用接口扩展的 Hermes v0.21.3，不能只看上游版本号判断兼容。

状态位于当前 profile 的 `supergoal/v2.sqlite3`。v1 数据库不自动转换；有 v1 绑定的会话需先在 v1 中 clear，或为 v2 使用新会话。不要直接编辑生产不可变 release。

## 日常命令

| 命令 | 行为 |
| --- | --- |
| `/supergoal start /absolute/path/policy.json` | 读取操作者提供的契约、运行正反例校准，再创建目标并排队 |
| `/supergoal status` | 显示状态、轮数、契约版本散列与已声明的验收范围 |
| `/supergoal pause` | 暂停并使旧派工失效；在途动作会标记为结果不明 |
| `/supergoal resume` | 继续已暂停且不存在未解决执行/验收问题的目标 |
| `/supergoal wait input-name` | 在已结束的执行边界等待输入，不调用模型轮询 |
| `/supergoal wake input-name` | 匹配输入后恢复并排队；重复事件不重复派工 |
| `/supergoal dispute 具体理由` | 进入验收争议状态，停止自动派工 |
| `/supergoal amend /absolute/path/revised-policy.json` | 重新校准，记录旧/新规则后暂停；随后可 resume |
| `/supergoal reconcile 已停止旧执行器并核对副作用的证据` | 记录操作者的执行核对；不直接批准任务成功 |
| `/supergoal clear` | 取消并解除会话绑定，保留所有审计历史 |

`pause` 使后续控制提交失效，不是终止任意操作系统进程的命令。在途/后台动作必须通过宿主停止并核对后才能 reconcile。不能把“人工声明已核对”当成程序证明外部副作用恰好发生一次。

## 契约格式

契约、校准样例、可信检查代码均放在任务工作区之外。下面示例只检验文本内容，是接口说明，不是研究基准：

```json
{
  "schema": 1,
  "outcome": "在 result.txt 中交付 ready",
  "scope": "只检查 result.txt 的完整文本；不包含其他语义质量",
  "workspace": "../work",
  "max_turns": 12,
  "criteria": {"text": "result.txt 的内容为 ready"},
  "artifacts": ["result.txt"],
  "checks": [{
    "id": "text-check",
    "covers": ["text"],
    "argv": ["/usr/bin/python3", "{policy_dir}/check.py"],
    "source_files": ["check.py"],
    "timeout": 2
  }],
  "calibration": [
    {"path": "fixtures/correct", "expect": "pass"},
    {"path": "fixtures/incorrect", "expect": "fail"}
  ]
}
```

两个 fixture 目录各包含 `result.txt`，内容分别是 `ready`、`wrong`。`check.py`：

```python
from pathlib import Path
raise SystemExit(0 if Path("result.txt").read_text() == "ready" else 1)
```

可选的 `work_policy: "evidence-v1"` 将意图、研究决策和实际观察的工作指导加入每次
派工；`verification_unknown: "continue"` 允许明确标记 `retryable=true` 的未知结果
继续工作。两项省略时保留原行为，未知不会因此成为通过。模型生成简报和逐项审查
目前仅由实验适配器自动执行；完整边界见[意图与证据策略](intent-and-evidence-policy.md)。

`{policy_dir}` 在开始时解析，`{workspace}` 在每次实际检查时解析。检查器以被检查的工作区为 cwd。检查器应以 0 表示接受，非零表示未接受；进程无法启动、超时、代码变化、产物在检查期间变化均不能判为成功。可信脚本及其被导入的本地文件应全部列入 `source_files`；系统依赖的版本须由实验环境另外冻结。

每个验收标准都必须被检查覆盖。多个检查器声明验证同一标准却给出不同结果时，进入 `contract_disputed`；应当分别满足的不同子条件使用不同标准 ID。所有检查超时总和至多 15 秒，校准案例总超时预算至多 20 秒，以适配 Hermes 命令/控制回调的期限。复杂办公/浏览器评估应在外部隔离评测器中运行，不能塞进这个同步回调。

## 异议与版本控制

Agent 可以在最终回复中提出：

```text
<supergoal-dispute>具体的矛盾、证据与需要核对的验收条目</supergoal-dispute>
```

这只会请求审查，不能改规则或直接完成。即使已配置检查通过，该请求仍暂停自动推进。明确修订契约需要用户的 amend 命令；新规则重新校准，旧规则、结果与修订事件都保留。修订不能更换目标/工作区，也不能重置或增大已登记的轮数预算。

校准证明的是检查器在所给样例上的行为；不能证明语义覆盖完整、没有其他误判。`succeeded` 的含义仍是该版本产物通过了该版本的配置检查。公开基准的最终评分与运行中的验收分开。

## 恢复与边界

ready 工作项本身就是持久派工记录。宿主在真实派工前，通过 provider 原子领取；派工信封带目标、工作项及状态版本绑定的 token。完成结果和下一项在同一 SQLite 事务提交，提交后进程退出时可以重建待派工信封。运行中的租约过期会隔离为 `needs_reconciliation`；不会用重新派工来假装已经恢复未知副作用。

会话压缩仅按宿主提供的 compression lineage 转移绑定，普通分支/委派不会自动继承目标。仅将数据写在工作区外不构成安全隔离：生产插件与宿主工具通常共享账号权限。针对不可信任务，必须由外部执行环境限制控制数据库、可信检查器、隐藏评分器及参考答案的访问。新基准环境将继续使用独立 UID/容器/VM，而不是声称路径散列可以替代沙箱。

## 已做的验证

本地内核、验收与生命周期测试 24 项通过；腾讯云的隔离候选目录在真实 Hermes v0.21.3 插件注册器上共 26 项通过（含 2 项实际命令/控制 ABI 测试）。覆盖已知正确结果被错误规则拒绝、恒真检查器、检查代码被改、检查器冲突、跨进程待派工恢复、在途动作不盲目重放、预算不因修订重置和完成后不再恢复派工。这些是功能测试，不是新一轮模型实验或生产切换证明。

最终候选在相同隔离宿主环境完成完整回归：**131 项通过，0 失败**。最初测试传输包曾漏掉任务模块和目录插件根入口；补全测试包后整套重跑通过，未把传输错误记为模型失败。详见 [检查收据](../experiments/results/plugin-v2-candidate-checks-2026-10-05.json) 和 [完整测试日志](../experiments/results/plugin-v2-candidate-tests.log)。

2026-10-06 06:48（北京时间）追加检查：与上述 131 项检查时的生产模块逐一比较后，仅 `v2/kernel.py` 因 SQLite 日志兼容修复而变化。在 GCP 的固定 Hermes v0.21.3 源码环境重新执行当前内核、验收工作流、SQLite 兼容与真实插件接口测试，**39 项通过，0 失败、0 跳过，其中 2 项使用实际宿主注册器与命令/控制 ABI**。Python 绑定 SQLite 3.49.1 时确认使用 DELETE journal。34 个暂存源码文件均与本地候选散列一致。

这次检查使用独立测试 HOME 和临时数据库，派工由测试替身接收，没有模型调用或生产 Gateway 切换。原收据的 `private_network` 字段只是启动环境标记；没有保存当时的实际网络命名空间，因此不据此声称验证了网络隔离。原收据、JUnit、日志、源码和这一限制一并保留在[证据清单](../experiments/results/plugin-v2-checks02-evidence/evidence-manifest.json)中。39 项是变更后的针对性回归，不能改写为当前源码重新通过了全部 131 项。
