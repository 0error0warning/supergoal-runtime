# Terminal-Bench 2.1 公开任务试验：public-parallel24-v1

收据截至 2026-10-05T23:36:45.190885+00:00。已返回 110/120 个登记试次，其中 104 个官方流程无异常。这是已登记子集，不是完整 Terminal-Bench 排行榜成绩。

执行器：真实 Hermes 0.21.3 + devin/swe-2。24 个不同公开 task ID、5 组，每组合一次；没有增加随机种子。全部审查、续跑和重试共享 192 次上游请求及官方任务时间上限。并发配置：`{"max_active_trials": 6, "shared_cpu_pool": 6, "shared_memory_mb": 49152, "review_resources_reserved": true}`；保留各任务官方 CPU / 内存限额。容器内 Debian APT 改 HTTPS，Harbor 0.24.0，宿主 Python 3.13.5，环境差异已披露。

## 官方结果

未运行或尚无结果记为 —；评分装置问题记为待审计。执行超时与已确认的控制器失败均计入端到端失败，但超时本身不是控制器缺陷的证据。

| 任务 | native | sg_v2 | repeat_goal | sg_no_context | sg_no_review |
|---|---:|---:|---:|---:|---:|
| bn-fit-modify | 1 | 1 | 1 | 1 | 1 |
| circuit-fibsqrt | 0 | 1 | 1 | 1 | 1 |
| compile-compcert | 待审计 | 无评分（执行超时） | 1 | 无评分（执行超时） | 待审计 |
| constraints-scheduling | 1 | 1 | 1 | 1 | 1 |
| custom-memory-heap-crash | 1 | 1 | 1 | 1 | 1 |
| extract-elf | 0 | 0 | 0 | 0 | 0 |
| feal-linear-cryptanalysis | 1 | 1 | 1 | 1 | 1 |
| financial-document-processor | 0 | 1 | 1 | 1 | 1 |
| git-leak-recovery | 1 | 1 | 1 | 1 | 1 |
| install-windows-3.11 | 1 | 1 | 1 | 1 | 0 |
| large-scale-text-editing | 1 | 1（执行超时） | 1 | 1 | 1 |
| mcmc-sampling-stan | — | — | — | — | — |
| model-extraction-relu-logits | 0 | 0 | 0 | 0 | 0 |
| modernize-scientific-stack | 1 | 1 | 1 | 1 | 1 |
| nginx-request-logging | 1 | 1 | 1 | 1 | 1 |
| password-recovery | 1 | 1 | 1 | 1 | 1 |
| portfolio-optimization | 1 | 1 | 1 | 1 | 1 |
| protein-assembly | — | — | — | — | — |
| pytorch-model-recovery | 1 | 1 | 1 | 1 | 1 |
| query-optimize | 0 | 0 | 0 | 0 | 0 |
| sqlite-db-truncate | 1 | 1 | 1 | 1 | 0 |
| torch-tensor-parallelism | 0 | 1 | 1 | 1 | 1 |
| train-fasttext | 0 | 0 | 0 | 0（执行超时） | 1 |
| video-processing | 0 | 0 | 0 | 0 | 0 |

## 机制与实测用量

| 任务 / 分组 | 执行轮次 | 审查轮次 | 实际请求 | 输入 / 输出 tokens | 在线终判 |
|---|---:|---:|---:|---:|---|
| bn-fit-modify / native | 1 | 0 | 10 | 60339 / 2884 | 无 |
| bn-fit-modify / sg_v2 | 1 | 1 | 17 | 136786 / 6503 | pass |
| bn-fit-modify / repeat_goal | 6 | 0 | 20 | 147208 / 4072 | 无 |
| bn-fit-modify / sg_no_context | 1 | 1 | 18 | 110444 / 4150 | pass |
| bn-fit-modify / sg_no_review | 1 | 0 | 8 | 40747 / 1789 | unverified |
| circuit-fibsqrt / native | 1 | 0 | 78 | 1979854 / 148586 | 无 |
| circuit-fibsqrt / sg_v2 | 1 | 1 | 15 | 117587 / 24802 | pass |
| circuit-fibsqrt / repeat_goal | 6 | 0 | 35 | 674220 / 23268 | 无 |
| circuit-fibsqrt / sg_no_context | 1 | 1 | 15 | 131718 / 10237 | pass |
| circuit-fibsqrt / sg_no_review | 1 | 0 | 6 | 47427 / 15635 | unverified |
| compile-compcert / native | 1 | 0 | 28 | 196586 / 4019 | 无 |
| compile-compcert / sg_v2 | 3 | 3 | 66 | 581350 / 11183 | fail |
| compile-compcert / repeat_goal | 6 | 0 | 53 | 588375 / 6787 | 无 |
| compile-compcert / sg_no_context | 1 | 0 | 26 | 170917 / 3425 | 无 |
| compile-compcert / sg_no_review | 1 | 0 | 53 | 534367 / 6237 | unverified |
| constraints-scheduling / native | 1 | 0 | 3 | 16756 / 1465 | 无 |
| constraints-scheduling / sg_v2 | 1 | 1 | 5 | 27744 / 2849 | pass |
| constraints-scheduling / repeat_goal | 6 | 0 | 10 | 82431 / 3009 | 无 |
| constraints-scheduling / sg_no_context | 1 | 1 | 5 | 28316 / 2691 | pass |
| constraints-scheduling / sg_no_review | 1 | 0 | 3 | 15823 / 1462 | unverified |
| custom-memory-heap-crash / native | 1 | 0 | 31 | 490175 / 10158 | 无 |
| custom-memory-heap-crash / sg_v2 | 1 | 1 | 15 | 99922 / 3921 | pass |
| custom-memory-heap-crash / repeat_goal | 6 | 0 | 21 | 214401 / 6991 | 无 |
| custom-memory-heap-crash / sg_no_context | 1 | 1 | 15 | 103690 / 4996 | unknown |
| custom-memory-heap-crash / sg_no_review | 1 | 0 | 7 | 46267 / 3616 | unverified |
| extract-elf / native | 1 | 0 | 7 | 43820 / 1595 | 无 |
| extract-elf / sg_v2 | 1 | 1 | 16 | 98293 / 3755 | pass |
| extract-elf / repeat_goal | 6 | 0 | 17 | 148076 / 4262 | 无 |
| extract-elf / sg_no_context | 1 | 1 | 9 | 52091 / 2582 | pass |
| extract-elf / sg_no_review | 1 | 0 | 6 | 34961 / 2428 | unverified |
| feal-linear-cryptanalysis / native | 1 | 0 | 12 | 105265 / 7468 | 无 |
| feal-linear-cryptanalysis / sg_v2 | 1 | 1 | 15 | 124345 / 10382 | pass |
| feal-linear-cryptanalysis / repeat_goal | 6 | 0 | 17 | 181079 / 4466 | 无 |
| feal-linear-cryptanalysis / sg_no_context | 1 | 1 | 16 | 136809 / 11666 | pass |
| feal-linear-cryptanalysis / sg_no_review | 1 | 0 | 13 | 101132 / 5848 | unverified |
| financial-document-processor / native | 1 | 0 | 12 | 89597 / 2141 | 无 |
| financial-document-processor / sg_v2 | 1 | 1 | 21 | 151013 / 4051 | pass |
| financial-document-processor / repeat_goal | 6 | 0 | 22 | 257867 / 4059 | 无 |
| financial-document-processor / sg_no_context | 1 | 1 | 26 | 208174 / 5425 | pass |
| financial-document-processor / sg_no_review | 1 | 0 | 13 | 94448 / 2893 | unverified |
| git-leak-recovery / native | 1 | 0 | 6 | 27430 / 819 | 无 |
| git-leak-recovery / sg_v2 | 1 | 1 | 8 | 34189 / 1315 | pass |
| git-leak-recovery / repeat_goal | 6 | 0 | 16 | 97316 / 2196 | 无 |
| git-leak-recovery / sg_no_context | 1 | 1 | 11 | 48413 / 1633 | pass |
| git-leak-recovery / sg_no_review | 1 | 0 | 7 | 33765 / 969 | unverified |
| install-windows-3.11 / native | 1 | 0 | 35 | 332862 / 7299 | 无 |
| install-windows-3.11 / sg_v2 | 6 | 6 | 112 | 1488855 / 28095 | fail |
| install-windows-3.11 / repeat_goal | 6 | 0 | 72 | 1570037 / 16127 | 无 |
| install-windows-3.11 / sg_no_context | 6 | 6 | 95 | 1287755 / 19499 | fail |
| install-windows-3.11 / sg_no_review | 1 | 0 | 69 | 984750 / 14176 | unverified |
| large-scale-text-editing / native | 1 | 0 | 5 | 21484 / 1084 | 无 |
| large-scale-text-editing / sg_v2 | 1 | 0 | 12 | 64346 / 4141 | 无 |
| large-scale-text-editing / repeat_goal | 6 | 0 | 18 | 114862 / 3016 | 无 |
| large-scale-text-editing / sg_no_context | 2 | 2 | 24 | 122025 / 5467 | pass |
| large-scale-text-editing / sg_no_review | 1 | 0 | 6 | 27672 / 1229 | unverified |
| model-extraction-relu-logits / native | 1 | 0 | 5 | 27871 / 3665 | 无 |
| model-extraction-relu-logits / sg_v2 | 1 | 1 | 13 | 72900 / 4288 | pass |
| model-extraction-relu-logits / repeat_goal | 6 | 0 | 33 | 778489 / 13915 | 无 |
| model-extraction-relu-logits / sg_no_context | 1 | 1 | 13 | 92305 / 4854 | pass |
| model-extraction-relu-logits / sg_no_review | 1 | 0 | 5 | 28629 / 3977 | unverified |
| modernize-scientific-stack / native | 1 | 0 | 4 | 22467 / 891 | 无 |
| modernize-scientific-stack / sg_v2 | 1 | 1 | 7 | 34364 / 1223 | pass |
| modernize-scientific-stack / repeat_goal | 6 | 0 | 12 | 89603 / 2087 | 无 |
| modernize-scientific-stack / sg_no_context | 1 | 1 | 8 | 42806 / 1583 | pass |
| modernize-scientific-stack / sg_no_review | 1 | 0 | 6 | 37039 / 1209 | unverified |
| nginx-request-logging / native | 1 | 0 | 9 | 50954 / 1715 | 无 |
| nginx-request-logging / sg_v2 | 1 | 1 | 12 | 60214 / 2359 | pass |
| nginx-request-logging / repeat_goal | 6 | 0 | 17 | 169025 / 3141 | 无 |
| nginx-request-logging / sg_no_context | 1 | 1 | 11 | 71579 / 2469 | pass |
| nginx-request-logging / sg_no_review | 1 | 0 | 7 | 32528 / 1199 | unverified |
| password-recovery / native | 1 | 0 | 20 | 657447 / 5492 | 无 |
| password-recovery / sg_v2 | 1 | 1 | 29 | 249209 / 6085 | pass |
| password-recovery / repeat_goal | 6 | 0 | 23 | 249010 / 5697 | 无 |
| password-recovery / sg_no_context | 1 | 1 | 56 | 864035 / 13703 | pass |
| password-recovery / sg_no_review | 1 | 0 | 14 | 96573 / 3681 | unverified |
| portfolio-optimization / native | 1 | 0 | 5 | 36678 / 1713 | 无 |
| portfolio-optimization / sg_v2 | 1 | 1 | 11 | 82851 / 2812 | pass |
| portfolio-optimization / repeat_goal | 6 | 0 | 17 | 170472 / 3348 | 无 |
| portfolio-optimization / sg_no_context | 1 | 1 | 11 | 76872 / 2650 | pass |
| portfolio-optimization / sg_no_review | 1 | 0 | 6 | 43750 / 2295 | unverified |
| pytorch-model-recovery / native | 1 | 0 | 6 | 33109 / 1686 | 无 |
| pytorch-model-recovery / sg_v2 | 1 | 1 | 12 | 69029 / 3277 | pass |
| pytorch-model-recovery / repeat_goal | 6 | 0 | 17 | 144094 / 4800 | 无 |
| pytorch-model-recovery / sg_no_context | 1 | 1 | 12 | 80136 / 3282 | pass |
| pytorch-model-recovery / sg_no_review | 1 | 0 | 5 | 31194 / 1816 | unverified |
| query-optimize / native | 1 | 0 | 6 | 29054 / 1662 | 无 |
| query-optimize / sg_v2 | 1 | 1 | 24 | 147246 / 4643 | pass |
| query-optimize / repeat_goal | 6 | 0 | 18 | 142061 / 5630 | 无 |
| query-optimize / sg_no_context | 1 | 1 | 21 | 130003 / 3930 | pass |
| query-optimize / sg_no_review | 1 | 0 | 7 | 40671 / 1715 | unverified |
| sqlite-db-truncate / native | 1 | 0 | 4 | 17421 / 2386 | 无 |
| sqlite-db-truncate / sg_v2 | 1 | 1 | 11 | 53906 / 3367 | pass |
| sqlite-db-truncate / repeat_goal | 6 | 0 | 15 | 95694 / 2993 | 无 |
| sqlite-db-truncate / sg_no_context | 1 | 1 | 8 | 38890 / 2818 | pass |
| sqlite-db-truncate / sg_no_review | 1 | 0 | 3 | None / None | 无 |
| torch-tensor-parallelism / native | 1 | 0 | 8 | 45411 / 2820 | 无 |
| torch-tensor-parallelism / sg_v2 | 1 | 1 | 11 | 54094 / 3554 | pass |
| torch-tensor-parallelism / repeat_goal | 6 | 0 | 16 | 127811 / 5732 | 无 |
| torch-tensor-parallelism / sg_no_context | 1 | 1 | 23 | 169121 / 6439 | pass |
| torch-tensor-parallelism / sg_no_review | 1 | 0 | 13 | 85948 / 4460 | unverified |
| train-fasttext / native | 1 | 0 | 16 | 115243 / 3224 | 无 |
| train-fasttext / sg_v2 | 1 | 1 | 31 | 301478 / 5196 | unknown |
| train-fasttext / repeat_goal | 6 | 0 | 41 | 1514298 / 6612 | 无 |
| train-fasttext / sg_no_context | 2 | 1 | 42 | 779072 / 9131 | fail |
| train-fasttext / sg_no_review | 1 | 0 | 22 | 436431 / 4321 | unverified |
| video-processing / native | 1 | 0 | 13 | 232355 / 7893 | 无 |
| video-processing / sg_v2 | 1 | 1 | 41 | 719685 / 19170 | pass |
| video-processing / repeat_goal | 6 | 0 | 74 | 1824684 / 20645 | 无 |
| video-processing / sg_no_context | 1 | 1 | 22 | 280583 / 7783 | pass |
| video-processing / sg_no_review | 1 | 0 | 57 | 3425599 / 18879 | unverified |

已观察到 27 个试次执行超过一轮；8 个试次的在线 pass 与官方失败冲突。这两个计数描述已返回的数据，不能单独作为增益或可靠性证明。

原生与完整 SG 的完整有效配对：`{"native_1_sg_1": 12, "native_0_sg_1": 3, "native_0_sg_0": 5}`。以不同 task ID 为单位，不把模型调用数、审查次数或相同任务的不同组当成独立任务样本。

端到端配对（包含控制器失败）：`{"native_1_sg_1": 12, "native_0_sg_1": 3, "native_0_sg_0": 6, "native_1_sg_0": 1}`。
按 task ID 配对的统计：`{"paired_tasks": 22, "sg_only_success": 3, "native_only_success": 1, "success_rate_difference": 0.09090909090909091, "mcnemar_exact_two_sided_p": 0.625, "task_bootstrap_percentile_95": [-0.09090909090909091, 0.2727272727272727], "bootstrap_resamples": 10000, "bootstrap_seed": 20261006, "bootstrap_degenerate": false, "scope": "Conditional on available paired, deliberately selected task IDs. Not a population-generalization guarantee; resampling calls no model."}`。小样本或相同差值可能产生退化的 bootstrap 区间；它不是通用能力的置信保证。
各组端到端统计：`{"native": {"observed_evaluable_trials": 22, "successes": 13, "controller_failures": 1, "execution_deadlines": 0}, "sg_v2": {"observed_evaluable_trials": 22, "successes": 15, "controller_failures": 1, "execution_deadlines": 2}, "repeat_goal": {"observed_evaluable_trials": 22, "successes": 17, "controller_failures": 0, "execution_deadlines": 0}, "sg_no_context": {"observed_evaluable_trials": 22, "successes": 16, "controller_failures": 1, "execution_deadlines": 2}, "sg_no_review": {"observed_evaluable_trials": 22, "successes": 15, "controller_failures": 1, "execution_deadlines": 0}}`。分母仅含已有可判定结果；待审计的评分装置问题与未启动试次单列。

## 解释边界

- 官方参考解只验证环境和评分器，不属于任何模型组的成功。旧候选、装置失败和原始零分全部保留。
- 题目由项目选择，公开题目存在训练污染可能；结果不能单独证明通用性或跨天自治。开发与冻结后任务的划分见登记。
- 在线审查器尚未完成独立校准。只读文件系统快照不保留服务进程，可能无法判断运行状态。
- 工具适配器已发现的接入缺陷、修复验证及冻结候选范围见[研究报告](research-findings-2026-10-06.md)。原成绩保留；不能把包含接入差异的比较解释为纯 harness 因果效应。
- 此入口使用实际 v2 内核，但不等于生产网关插件端到端研究；生产默认引擎未切换。
- 各组请求和时间上限相同，实际 token 用量不同；总输入 token 未设硬上限。
- 物理请求有记账但缺少 token 回执时，该任务的完整 token 总量记为未知；原始观测小计另存。token 用量的配对比较只包含双方回执完整的任务，不把服务失败后的观测零值当成真实零消耗。
- 模型轨迹和官方日志位于 supergoal-gcp；快照 ID 不代表镜像仍存在。快照可用性与独立存档收据单列，缺少实测记录时为未知；公开导出提供散列和必要评分日志。

原始数据：[结果与评分日志](../experiments/results/public-parallel24-v1-audited-bundle.json)。方法：[候选协议](../experiments/public_benchmarks/PUBLIC-PARALLEL24-V1.md)、[预登记](../experiments/public_benchmarks/registration-public-parallel24-v1.json)。

## 补充任务配对比较

各行只使用两组都有端到端结果的相同 task ID；不同对照行的任务集合可能不同。待运行和评分装置未解决的单元没有填零。用量只汇总这些结果配对中两侧都有该指标的任务，缺失用量不填零。这是事后补充的描述性比较，不是新增预登记检验或总体排名。

| 对照组 | 配对任务 | 仅 SG 全过 | 仅对照全过 | 平均分差（SG − 对照） | 配对请求合计（SG / 对照；任务数） |
|---|---:|---:|---:|---:|---|
| native | 22 | 3 | 1 | +0.0909 | 504 / 323；22 |
| repeat_goal | 22 | 0 | 2 | -0.0909 | 504 / 584；22 |
| sg_no_context | 22 | 0 | 1 | -0.0455 | 504 / 487；22 |
| sg_no_review | 22 | 2 | 2 | +0.0000 | 504 / 336；22 |

## 服务与执行完整性附注

以下原始分数保持不变。worker_incomplete 表示 SDK 未完成，不能仅凭官方零分把原因归为求解能力；HTTP 错误可能已被重试恢复，需要结合终态和轨迹解释。

| 任务 / 组 | 控制器状态 | 已记录上游 HTTP 错误 |
|---|---|---|
| sqlite-db-truncate / sg_no_review | worker_incomplete | {"503": 3} |
