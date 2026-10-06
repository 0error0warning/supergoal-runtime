# Terminal-Bench 2.1 开发试验：public-dev04

收据截至 2026-10-05T11:59:15.912105+00:00。已返回 16/30 个登记试次，其中 15 个已完成官方测试。不是完整 Terminal-Bench 排行榜成绩，也不是保留集结论。

模型为真实 Hermes 0.21.3 + devin/swe-2。六个不同公开 task ID、五组，每组合一次；没有增加随机种子。全部审查/续跑/重试共享 96 次实际上游请求和官方任务时间上限。并发 1，任务保留官方 1 CPU / 2 GiB；容器内 Debian APT 改 HTTPS，Harbor 0.24.0，宿主 Python 3.13.5，环境差异已披露。

## 官方结果

未运行或尚无结果记为 —。第 16 个试次的租约异常已经核验为运行失败，官方评分缺失；保留在端到端评估分母中。其他待审计异常需要区分运行故障与评分装置错误。

| 任务 | 原生 Hermes | 完整 SG | 重发目标 | 无状态补充 | 无审查 |
|---|---:|---:|---:|---:|---:|
| cancel-async-tasks | 0 | 1 | 0 | 1 | 0 |
| db-wal-recovery | 1 | 0 | 1 | 1 | 1 |
| raman-fitting | 0 | 0 | 0 | 0 | 0 |
| schemelike-metacircular-eval | — | — | — | 运行失败（未评分） | — |
| configure-git-webserver | — | — | — | — | — |
| multi-source-data-merger | — | — | — | — | — |

## 机制与实测用量

| 任务 / 分组 | 执行轮次 | 审查轮次 | 实际请求 | 输入 / 输出 tokens | 在线终判 |
|---|---:|---:|---:|---:|---|
| cancel-async-tasks / native | 1 | 0 | 3 | 12346 / 1136 | 无 |
| cancel-async-tasks / sg_v2 | 1 | 1 | 11 | 55026 / 2257 | pass |
| cancel-async-tasks / repeat_goal | 6 | 0 | 18 | 115702 / 3097 | 无 |
| cancel-async-tasks / sg_no_context | 1 | 1 | 9 | 42142 / 2472 | pass |
| cancel-async-tasks / sg_no_review | 1 | 0 | 4 | 16497 / 1350 | unverified |
| db-wal-recovery / native | 1 | 0 | 5 | 22517 / 628 | 无 |
| db-wal-recovery / sg_v2 | 6 | 6 | 83 | 1072933 / 17115 | fail |
| db-wal-recovery / repeat_goal | 6 | 0 | 17 | 112605 / 2282 | 无 |
| db-wal-recovery / sg_no_context | 1 | 1 | 10 | 50799 / 1353 | pass |
| db-wal-recovery / sg_no_review | 1 | 0 | 7 | 33787 / 1451 | unverified |
| raman-fitting / native | 1 | 0 | 66 | 1254699 / 26265 | 无 |
| raman-fitting / sg_v2 | 2 | 2 | 36 | 236334 / 13732 | pass |
| raman-fitting / repeat_goal | 6 | 0 | 39 | 553861 / 13317 | 无 |
| raman-fitting / sg_no_context | 1 | 1 | 20 | 116544 / 6162 | pass |
| raman-fitting / sg_no_review | 1 | 0 | 25 | 217815 / 9134 | unverified |
| schemelike-metacircular-eval / sg_no_context | 1 | 1 | 93 | 2606938 / 64121 | pass |

已观察到 5 个试次执行超过一轮；2 个试次的在线 pass 与官方失败冲突。这两个计数描述已返回的数据，不能单独作为增益或可靠性证明。

原生与完整 SG 的完整有效配对：`{"native_0_sg_1": 1, "native_1_sg_0": 1, "native_0_sg_0": 1}`。以不同 task ID 为单位，不把模型调用数、审查次数或相同任务的不同组当成独立任务样本。

## 解释边界

- 官方参考解只验证环境和评分器，不属于任何模型组的成功。旧候选、装置失败和原始零分全部保留。
- 这是开发样本，规模小、公开题目存在训练污染可能；不作显著性、通用性或跨天自治结论。
- 在线审查器尚未完成独立校准。只读文件系统快照不保留服务进程，可能无法判断运行状态。
- 此入口使用实际 v2 内核，但不等于生产网关插件端到端研究；生产默认引擎未切换。
- 各组请求和时间上限相同，实际 token 用量不同；总输入 token 未设硬上限。
- 评分前文件系统快照、完整模型轨迹和官方日志保留在 grok-bot；公开导出提供散列和必要评分日志。

原始数据：[结果与评分日志](../experiments/results/public-benchmarks-2026-10-05.json)。方法：[候选协议](../experiments/public_benchmarks/PUBLIC-DEV04.md)、[预登记](../experiments/public_benchmarks/registration-public-dev04.json)。

运行状态与已核验故障：[开发试点发现](public-benchmark-findings-2026-10-05.md)。此处运行故障标注来自[独立审计](../experiments/results/public-dev04-interruption-audit.json)，原始奖励保持缺失，未改写为官方零分。
