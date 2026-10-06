# Terminal-Bench 2.1 公开任务试验：public-gcp01

收据截至 2026-10-05T18:45:44.544029+00:00。已返回 30/30 个登记试次，其中 30 个官方流程无异常。这是已登记子集，不是完整 Terminal-Bench 排行榜成绩。

执行器：真实 Hermes 0.21.3 + devin/swe-2。6 个不同公开 task ID、5 组，每组合一次；没有增加随机种子。全部审查、续跑和重试共享 96 次上游请求及官方任务时间上限。并发配置：`1`；保留各任务官方 CPU / 内存限额。容器内 Debian APT 改 HTTPS，Harbor 0.24.0，宿主 Python 3.13.5，环境差异已披露。

## 官方结果

未运行或尚无结果记为 —；评分装置问题记为待审计。执行超时与已确认的控制器失败均计入端到端失败，但超时本身不是控制器缺陷的证据。

| 任务 | native | sg_v2 | repeat_goal | sg_no_context | sg_no_review |
|---|---:|---:|---:|---:|---:|
| configure-git-webserver | 1 | 1 | 1 | 1 | 1 |
| fix-code-vulnerability | 1 | 1 | 1 | 1 | 1 |
| sparql-university | 1 | 1 | 1 | 1 | 1 |
| write-compressor | 1 | 1 | 1 | 1 | 1 |
| dna-assembly | 0 | 1 | 0 | 0 | 0 |
| llm-inference-batching-scheduler | 1 | 1 | 1 | 1 | 1 |

## 机制与实测用量

| 任务 / 分组 | 执行轮次 | 审查轮次 | 实际请求 | 输入 / 输出 tokens | 在线终判 |
|---|---:|---:|---:|---:|---|
| configure-git-webserver / native | 1 | 0 | 13 | 65726 / 1761 | 无 |
| configure-git-webserver / sg_v2 | 1 | 1 | 18 | 95001 / 3644 | pass |
| configure-git-webserver / repeat_goal | 6 | 0 | 23 | 244474 / 3305 | 无 |
| configure-git-webserver / sg_no_context | 6 | 6 | 49 | 258066 / 8521 | fail |
| configure-git-webserver / sg_no_review | 1 | 0 | 8 | 36949 / 1543 | unverified |
| fix-code-vulnerability / native | 1 | 0 | 11 | 75569 / 2344 | 无 |
| fix-code-vulnerability / sg_v2 | 1 | 1 | 8 | 43920 / 1313 | pass |
| fix-code-vulnerability / repeat_goal | 6 | 0 | 17 | 177148 / 2893 | 无 |
| fix-code-vulnerability / sg_no_context | 1 | 1 | 9 | 51902 / 1298 | pass |
| fix-code-vulnerability / sg_no_review | 1 | 0 | 7 | 50281 / 1086 | unverified |
| sparql-university / native | 1 | 0 | 11 | 97783 / 2729 | 无 |
| sparql-university / sg_v2 | 1 | 1 | 15 | 136098 / 7461 | pass |
| sparql-university / repeat_goal | 6 | 0 | 20 | 219161 / 5482 | 无 |
| sparql-university / sg_no_context | 1 | 1 | 15 | 131996 / 3974 | pass |
| sparql-university / sg_no_review | 1 | 0 | 12 | 107436 / 4397 | unverified |
| write-compressor / native | 1 | 0 | 12 | 94747 / 16489 | 无 |
| write-compressor / sg_v2 | 1 | 1 | 15 | 115526 / 17631 | pass |
| write-compressor / repeat_goal | 6 | 0 | 25 | 282865 / 15652 | 无 |
| write-compressor / sg_no_context | 1 | 1 | 28 | 352655 / 33143 | pass |
| write-compressor / sg_no_review | 1 | 0 | 32 | 581035 / 33062 | unverified |
| dna-assembly / native | 1 | 0 | 16 | 153683 / 11932 | 无 |
| dna-assembly / sg_v2 | 1 | 1 | 55 | 824677 / 54786 | pass |
| dna-assembly / repeat_goal | 6 | 0 | 24 | 375945 / 21082 | 无 |
| dna-assembly / sg_no_context | 1 | 1 | 25 | 224826 / 25954 | pass |
| dna-assembly / sg_no_review | 1 | 0 | 30 | 454860 / 35143 | unverified |
| llm-inference-batching-scheduler / native | 1 | 0 | 41 | 1231962 / 27116 | 无 |
| llm-inference-batching-scheduler / sg_v2 | 1 | 1 | 46 | 852119 / 21195 | pass |
| llm-inference-batching-scheduler / repeat_goal | 6 | 0 | 35 | 769999 / 18580 | 无 |
| llm-inference-batching-scheduler / sg_no_context | 1 | 1 | 26 | 391110 / 17753 | pass |
| llm-inference-batching-scheduler / sg_no_review | 1 | 0 | 17 | 241001 / 13701 | unverified |

已观察到 7 个试次执行超过一轮；1 个试次的在线 pass 与官方失败冲突。这两个计数描述已返回的数据，不能单独作为增益或可靠性证明。

原生与完整 SG 的完整有效配对：`{"native_1_sg_1": 5, "native_0_sg_1": 1}`。以不同 task ID 为单位，不把模型调用数、审查次数或相同任务的不同组当成独立任务样本。

端到端配对（包含控制器失败）：`{"native_1_sg_1": 5, "native_0_sg_1": 1}`。
按 task ID 配对的统计：`{"paired_tasks": 6, "sg_only_success": 1, "native_only_success": 0, "success_rate_difference": 0.16666666666666666, "mcnemar_exact_two_sided_p": 1.0, "task_bootstrap_percentile_95": [0.0, 0.5], "bootstrap_resamples": 10000, "bootstrap_seed": 20261006, "bootstrap_degenerate": false, "scope": "Conditional on available paired, deliberately selected task IDs. Not a population-generalization guarantee; resampling calls no model."}`。小样本或相同差值可能产生退化的 bootstrap 区间；它不是通用能力的置信保证。
各组端到端统计：`{"native": {"observed_evaluable_trials": 6, "successes": 5, "controller_failures": 0, "execution_deadlines": 0}, "sg_v2": {"observed_evaluable_trials": 6, "successes": 6, "controller_failures": 0, "execution_deadlines": 0}, "repeat_goal": {"observed_evaluable_trials": 6, "successes": 5, "controller_failures": 0, "execution_deadlines": 0}, "sg_no_context": {"observed_evaluable_trials": 6, "successes": 5, "controller_failures": 0, "execution_deadlines": 0}, "sg_no_review": {"observed_evaluable_trials": 6, "successes": 5, "controller_failures": 0, "execution_deadlines": 0}}`。分母仅含已有可判定结果；待审计的评分装置问题与未启动试次单列。

## 解释边界

- 官方参考解只验证环境和评分器，不属于任何模型组的成功。旧候选、装置失败和原始零分全部保留。
- 题目由项目选择，公开题目存在训练污染可能；结果不能单独证明通用性或跨天自治。开发与冻结后任务的划分见登记。
- 在线审查器尚未完成独立校准。只读文件系统快照不保留服务进程，可能无法判断运行状态。
- 此入口使用实际 v2 内核，但不等于生产网关插件端到端研究；生产默认引擎未切换。
- 各组请求和时间上限相同，实际 token 用量不同；总输入 token 未设硬上限。
- 模型轨迹和官方日志位于 supergoal-gcp；快照 ID 不代表镜像仍存在。快照可用性与独立存档收据单列，缺少实测记录时为未知；公开导出提供散列和必要评分日志。

原始数据：[结果与评分日志](../experiments/results/public-gcp01-audited-bundle.json)。方法：[候选协议](../experiments/public_benchmarks/PUBLIC-GCP01.md)、[预登记](../experiments/public_benchmarks/registration-public-gcp01.json)。
