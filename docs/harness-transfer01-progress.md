# Terminal-Bench 2.1 公开任务试验：harness-transfer01

收据截至 2026-10-05T22:24:14.970791+00:00。已返回 12/12 个登记试次，其中 10 个官方流程无异常。这是已登记子集，不是完整 Terminal-Bench 排行榜成绩。

执行器：Hermes 0.21.3; actual upstream mini-SWE-agent 2.4.6; actual upstream LongHorizon 0.1.7 with Hermes role backend; all devin/swe-2。3 个不同公开 task ID、4 组，每组合一次；没有增加随机种子。全部审查、续跑和重试共享 192 次上游请求及官方任务时间上限。并发配置：`{"new_cohort_cpu_peak": 2, "workers": 2, "memory_mb": 12288, "other_pool_cpus": 6}`；保留各任务官方 CPU / 内存限额。容器内 Debian APT 改 HTTPS，Harbor 0.24.0，宿主 Python 3.13.5，环境差异已披露。

## 官方结果

未运行或尚无结果记为 —；评分装置问题记为待审计。执行超时与已确认的控制器失败均计入端到端失败，但超时本身不是控制器缺陷的证据。

| 任务 | native | sg_v2 | mini_swe | longhorizon |
|---|---:|---:|---:|---:|
| adaptive-rejection-sampler | 0 | 1 | 1 | 1 |
| polyglot-rust-c | 1 | 1 | 1 | 1 |
| gcode-to-text | 0 | 1 | 0（执行超时） | 0（执行超时） |

## 机制与实测用量

| 任务 / 分组 | 执行轮次 | 审查轮次 | 实际请求 | 输入 / 输出 tokens | 在线终判 |
|---|---:|---:|---:|---:|---|
| adaptive-rejection-sampler / native | 1 | 0 | 26 | 294482 / 10774 | 无 |
| adaptive-rejection-sampler / sg_v2 | 1 | 1 | 24 | 237185 / 9680 | pass |
| adaptive-rejection-sampler / mini_swe | 1 | 0 | 58 | 1419875 / 27722 | 无 |
| adaptive-rejection-sampler / longhorizon | 1 | 1 | 27 | 346640 / 16822 | pass |
| polyglot-rust-c / native | 1 | 0 | 6 | 26814 / 6267 | 无 |
| polyglot-rust-c / sg_v2 | 1 | 1 | 17 | 91566 / 14301 | pass |
| polyglot-rust-c / mini_swe | 1 | 0 | 10 | 26926 / 10705 | 无 |
| polyglot-rust-c / longhorizon | 1 | 1 | 25 | 227200 / 30174 | pass |
| gcode-to-text / native | 1 | 0 | 42 | 996181 / 19571 | 无 |
| gcode-to-text / sg_v2 | 1 | 1 | 74 | None / None | pass |
| gcode-to-text / mini_swe | 1 | 0 | 57 | 1948298 / 27318 | 无 |
| gcode-to-text / longhorizon | 1 | 1 | 85 | None / None | 无 |

已观察到 0 个试次执行超过一轮；0 个试次的在线 pass 与官方失败冲突。这两个计数描述已返回的数据，不能单独作为增益或可靠性证明。

原生与完整 SG 的完整有效配对：`{"native_0_sg_1": 2, "native_1_sg_1": 1}`。以不同 task ID 为单位，不把模型调用数、审查次数或相同任务的不同组当成独立任务样本。

端到端配对（包含控制器失败）：`{"native_0_sg_1": 2, "native_1_sg_1": 1}`。
按 task ID 配对的统计：`{"paired_tasks": 3, "sg_only_success": 2, "native_only_success": 0, "success_rate_difference": 0.6666666666666666, "mcnemar_exact_two_sided_p": 0.5, "task_bootstrap_percentile_95": [0.0, 1.0], "bootstrap_resamples": 10000, "bootstrap_seed": 20261006, "bootstrap_degenerate": false, "scope": "Conditional on available paired, deliberately selected task IDs. Not a population-generalization guarantee; resampling calls no model."}`。小样本或相同差值可能产生退化的 bootstrap 区间；它不是通用能力的置信保证。
各组端到端统计：`{"native": {"observed_evaluable_trials": 3, "successes": 1, "controller_failures": 0, "execution_deadlines": 0}, "sg_v2": {"observed_evaluable_trials": 3, "successes": 3, "controller_failures": 0, "execution_deadlines": 0}, "mini_swe": {"observed_evaluable_trials": 3, "successes": 2, "controller_failures": 0, "execution_deadlines": 1}, "longhorizon": {"observed_evaluable_trials": 3, "successes": 2, "controller_failures": 0, "execution_deadlines": 1}}`。分母仅含已有可判定结果；待审计的评分装置问题与未启动试次单列。

## 解释边界

- 官方参考解只验证环境和评分器，不属于任何模型组的成功。旧候选、装置失败和原始零分全部保留。
- 题目由项目选择，公开题目存在训练污染可能；结果不能单独证明通用性或跨天自治。开发与冻结后任务的划分见登记。
- 在线审查器尚未完成独立校准。只读文件系统快照不保留服务进程，可能无法判断运行状态。
- 工具适配器已发现的接入缺陷、修复验证及冻结候选范围见[研究报告](research-findings-2026-10-06.md)。原成绩保留；不能把包含接入差异的比较解释为纯 harness 因果效应。
- 此入口使用实际 v2 内核，但不等于生产网关插件端到端研究；生产默认引擎未切换。
- 各组请求和时间上限相同，实际 token 用量不同；总输入 token 未设硬上限。
- 物理请求有记账但缺少 token 回执时，该任务的完整 token 总量记为未知；原始观测小计另存。token 用量的配对比较只包含双方回执完整的任务，不把服务失败后的观测零值当成真实零消耗。
- 模型轨迹和官方日志位于 supergoal-gcp；快照 ID 不代表镜像仍存在。快照可用性与独立存档收据单列，缺少实测记录时为未知；公开导出提供散列和必要评分日志。

原始数据：[结果与评分日志](../experiments/results/harness-transfer01-audited-bundle.json)。方法：[候选协议](harness-transfer-study-2026-10-06.md)、[预登记](../experiments/public_benchmarks/registration-harness-transfer01.json)。

## 补充任务配对比较

各行只使用两组都有端到端结果的相同 task ID；不同对照行的任务集合可能不同。待运行和评分装置未解决的单元没有填零。用量只汇总这些结果配对中两侧都有该指标的任务，缺失用量不填零。这是事后补充的描述性比较，不是新增预登记检验或总体排名。

| 对照组 | 配对任务 | 仅 SG 全过 | 仅对照全过 | 平均分差（SG − 对照） | 配对请求合计（SG / 对照；任务数） |
|---|---:|---:|---:|---:|---|
| native | 3 | 2 | 0 | +0.6667 | 115 / 74；3 |
| mini_swe | 3 | 1 | 0 | +0.3333 | 115 / 125；3 |
| longhorizon | 3 | 1 | 0 | +0.3333 | 115 / 137；3 |

## 上游角色调用

管理、格式修复与最终答复也计入总请求及时间，不能仅按执行/审查轮数估计开销。以下是角色 episode 数，不是物理请求数。

| 任务 / 组 | 上游角色 episode 计数 |
|---|---|
| gcode-to-text / longhorizon | {"manager": 2, "cli_executor": 1, "cli_auditor": 1} |
| polyglot-rust-c / longhorizon | {"manager": 2, "cli_executor": 1, "cli_auditor": 1, "final_response": 1} |
| adaptive-rejection-sampler / longhorizon | {"manager": 2, "cli_executor": 1, "cli_auditor": 1, "final_response": 1} |

## 已登记机制对照（开发样本，仅描述性）

历史清空只有在对应清空组实际进入第二轮执行时才生效。对照组发生续跑，不代表清空组的干预也已触发；首轮结果差异不能归因于历史清空。unknown 重审也必须实际出现第二次审查。

| 组 | 已返回 | 第二轮执行 | 历史清空实际触发 | 有限重审实际触发 | 错误在线 pass |
|---|---:|---:|---:|---:|---:|
| native | 3 | 0 | 0 | 0 | 0 |
| sg_v2 | 3 | 0 | 0 | 0 | 0 |
| mini_swe | 3 | 0 | 0 | 0 | 0 |
| longhorizon | 3 | 0 | 0 | 0 | 0 |

```json
[
  {
    "control": "native",
    "treatment": "sg_v2",
    "hypothesis": "whole_framework",
    "paired_tasks": 3,
    "pairs": [
      {
        "task_id": "adaptive-rejection-sampler",
        "reward_difference": 1.0,
        "request_difference": -2,
        "second_episode_observed": false,
        "control_second_episode_observed": false,
        "treatment_second_episode_observed": false,
        "treatment_history_reset_exercised": false,
        "treatment_bounded_reaudit_exercised": false,
        "unknown_audit_observed": false
      },
      {
        "task_id": "gcode-to-text",
        "reward_difference": 1.0,
        "request_difference": 32,
        "second_episode_observed": false,
        "control_second_episode_observed": false,
        "treatment_second_episode_observed": false,
        "treatment_history_reset_exercised": false,
        "treatment_bounded_reaudit_exercised": false,
        "unknown_audit_observed": false
      },
      {
        "task_id": "polyglot-rust-c",
        "reward_difference": 0.0,
        "request_difference": 11,
        "second_episode_observed": false,
        "control_second_episode_observed": false,
        "treatment_second_episode_observed": false,
        "treatment_history_reset_exercised": false,
        "treatment_bounded_reaudit_exercised": false,
        "unknown_audit_observed": false
      }
    ],
    "mean_reward_difference": 0.6666666666666666,
    "interpretation": "Exploratory, deliberately selected task units; activation subsets are descriptive and post-treatment."
  },
  {
    "control": "mini_swe",
    "treatment": "sg_v2",
    "hypothesis": "external_harness",
    "paired_tasks": 3,
    "pairs": [
      {
        "task_id": "adaptive-rejection-sampler",
        "reward_difference": 0.0,
        "request_difference": -34,
        "second_episode_observed": false,
        "control_second_episode_observed": false,
        "treatment_second_episode_observed": false,
        "treatment_history_reset_exercised": false,
        "treatment_bounded_reaudit_exercised": false,
        "unknown_audit_observed": false
      },
      {
        "task_id": "gcode-to-text",
        "reward_difference": 1.0,
        "request_difference": 17,
        "second_episode_observed": false,
        "control_second_episode_observed": false,
        "treatment_second_episode_observed": false,
        "treatment_history_reset_exercised": false,
        "treatment_bounded_reaudit_exercised": false,
        "unknown_audit_observed": false
      },
      {
        "task_id": "polyglot-rust-c",
        "reward_difference": 0.0,
        "request_difference": 7,
        "second_episode_observed": false,
        "control_second_episode_observed": false,
        "treatment_second_episode_observed": false,
        "treatment_history_reset_exercised": false,
        "treatment_bounded_reaudit_exercised": false,
        "unknown_audit_observed": false
      }
    ],
    "mean_reward_difference": 0.3333333333333333,
    "interpretation": "Exploratory, deliberately selected task units; activation subsets are descriptive and post-treatment."
  },
  {
    "control": "longhorizon",
    "treatment": "sg_v2",
    "hypothesis": "external_harness",
    "paired_tasks": 3,
    "pairs": [
      {
        "task_id": "adaptive-rejection-sampler",
        "reward_difference": 0.0,
        "request_difference": -3,
        "second_episode_observed": false,
        "control_second_episode_observed": false,
        "treatment_second_episode_observed": false,
        "treatment_history_reset_exercised": false,
        "treatment_bounded_reaudit_exercised": false,
        "unknown_audit_observed": false
      },
      {
        "task_id": "gcode-to-text",
        "reward_difference": 1.0,
        "request_difference": -11,
        "second_episode_observed": false,
        "control_second_episode_observed": false,
        "treatment_second_episode_observed": false,
        "treatment_history_reset_exercised": false,
        "treatment_bounded_reaudit_exercised": false,
        "unknown_audit_observed": false
      },
      {
        "task_id": "polyglot-rust-c",
        "reward_difference": 0.0,
        "request_difference": -8,
        "second_episode_observed": false,
        "control_second_episode_observed": false,
        "treatment_second_episode_observed": false,
        "treatment_history_reset_exercised": false,
        "treatment_bounded_reaudit_exercised": false,
        "unknown_audit_observed": false
      }
    ],
    "mean_reward_difference": 0.3333333333333333,
    "interpretation": "Exploratory, deliberately selected task units; activation subsets are descriptive and post-treatment."
  }
]
```
