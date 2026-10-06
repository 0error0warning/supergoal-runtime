# SCBench 多阶段研究结果

数据截至 2026-10-05T21:27:50.779758+00:00。计划 3 个不同问题、9 条轨迹。当前批次状态：`complete_with_itemized_outcomes`。

各检查点属于同一个问题，不能当作独立样本。未完成的组不与不同任务组成的其他组直接排名。

| 问题 | 组 | 状态 | 已评分 / 计划阶段 | 严格测试平均通过率 | 请求 | 全部阶段严格通过 | 在线 pass 但严格未全过 | SDK 未完成阶段 |
|---|---|---|---:|---:|---:|---|---:|---:|
| file_backup | native | returned | 4/4 | 66.05% | 51 | 否 | — | 0 |
| database_migration | sg_v2 | returned | 5/5 | 95.75% | 108 | 否 | 3 | 0 |
| dag_execution | mini_swe | returned | 3/3 | 50.96% | 149 | 否 | — | 0 |
| file_backup | sg_v2 | returned | 4/4 | 75.70% | 86 | 否 | 4 | 0 |
| database_migration | mini_swe | returned | 5/5 | 93.47% | 145 | 否 | — | 0 |
| dag_execution | native | returned | 3/3 | 0.00% | 15 | 否 | — | 3 |
| file_backup | mini_swe | returned | 4/4 | 55.97% | 77 | 否 | — | 0 |
| database_migration | native | returned | 5/5 | 91.85% | 72 | 否 | — | 0 |
| dag_execution | sg_v2 | returned | 3/3 | 84.11% | 100 | 否 | 3 | 0 |

## 逐阶段诊断

| 问题 / 组 / 阶段 | 执行 / 审查轮数 | 在线判断 | 严格通过率 | 旧阶段测试通过 / 总数 |
|---|---:|---|---:|---:|
| file_backup / native / checkpoint_1 | 1 / 0 | 无 | 65.62% | 0/0 |
| file_backup / native / checkpoint_2 | 1 / 0 | 无 | 68.00% | 21/32 |
| file_backup / native / checkpoint_3 | 1 / 0 | 无 | 67.65% | 34/50 |
| file_backup / native / checkpoint_4 | 1 / 0 | 无 | 62.92% | 38/68 |
| database_migration / sg_v2 / checkpoint_1 | 1 / 1 | pass | 100.00% | 0/0 |
| database_migration / sg_v2 / checkpoint_2 | 1 / 1 | pass | 98.39% | 39/39 |
| database_migration / sg_v2 / checkpoint_3 | 1 / 1 | unknown | 97.67% | 60/61 |
| database_migration / sg_v2 / checkpoint_4 | 1 / 1 | pass | 92.24% | 84/86 |
| database_migration / sg_v2 / checkpoint_5 | 1 / 1 | pass | 90.44% | 107/116 |
| dag_execution / mini_swe / checkpoint_1 | 1 / 0 | 无 | 24.24% | 0/0 |
| dag_execution / mini_swe / checkpoint_2 | 1 / 0 | 无 | 56.10% | 18/33 |
| dag_execution / mini_swe / checkpoint_3 | 1 / 0 | 无 | 72.55% | 27/41 |
| file_backup / sg_v2 / checkpoint_1 | 1 / 1 | pass | 81.25% | 0/0 |
| file_backup / sg_v2 / checkpoint_2 | 1 / 1 | pass | 78.00% | 26/32 |
| file_backup / sg_v2 / checkpoint_3 | 1 / 1 | pass | 75.00% | 39/50 |
| file_backup / sg_v2 / checkpoint_4 | 1 / 1 | pass | 68.54% | 43/68 |
| database_migration / mini_swe / checkpoint_1 | 1 / 0 | 无 | 100.00% | 0/0 |
| database_migration / mini_swe / checkpoint_2 | 1 / 0 | 无 | 93.55% | 39/39 |
| database_migration / mini_swe / checkpoint_3 | 1 / 0 | 无 | 94.19% | 57/61 |
| database_migration / mini_swe / checkpoint_4 | 1 / 0 | 无 | 91.38% | 81/86 |
| database_migration / mini_swe / checkpoint_5 | 1 / 0 | 无 | 88.24% | 106/116 |
| dag_execution / native / checkpoint_1 | 1 / 0 | 无 | 0.00% | 0/0 |
| dag_execution / native / checkpoint_2 | 1 / 0 | 无 | 0.00% | 0/33 |
| dag_execution / native / checkpoint_3 | 1 / 0 | 无 | 0.00% | 0/41 |
| file_backup / mini_swe / checkpoint_1 | 1 / 0 | 无 | 56.25% | 0/0 |
| file_backup / mini_swe / checkpoint_2 | 1 / 0 | 无 | 56.00% | 18/32 |
| file_backup / mini_swe / checkpoint_3 | 1 / 0 | 无 | 58.82% | 28/50 |
| file_backup / mini_swe / checkpoint_4 | 1 / 0 | 无 | 52.81% | 32/68 |
| database_migration / native / checkpoint_1 | 1 / 0 | 无 | 100.00% | 0/0 |
| database_migration / native / checkpoint_2 | 1 / 0 | 无 | 93.55% | 39/39 |
| database_migration / native / checkpoint_3 | 1 / 0 | 无 | 94.19% | 57/61 |
| database_migration / native / checkpoint_4 | 1 / 0 | 无 | 86.21% | 81/86 |
| database_migration / native / checkpoint_5 | 1 / 0 | 无 | 85.29% | 100/116 |
| dag_execution / sg_v2 / checkpoint_1 | 1 / 1 | pass | 93.94% | 0/0 |
| dag_execution / sg_v2 / checkpoint_2 | 1 / 1 | pass | 87.80% | 31/33 |
| dag_execution / sg_v2 / checkpoint_3 | 1 / 1 | pass | 70.59% | 36/41 |

## 解释范围

- 原始官方分数保留；按已评分阶段计算的平均分不代表完整问题成功。执行或评分异常在原始记录中单列。
- SDK 未完成可以不抛出 Harbor 异常；需结合控制器状态和服务故障审计，不把这类零分直接解释为模型求解能力。
- core 检查通常只是基础子集，不能替代 strict 全部测试。新阶段的平均分下降，也不自动等于旧功能退化；同名旧测试的转换另行核对。
- 请求包含执行、审查、传输重试及不确定预留。角色之间共享上限；相同上限并不意味着相同实际 token 用量。
- 上游会逐步提供新需求；该流程不等于一次委托后的跨天自主规划。
- 上游保留代码质量诊断历史；适配器不把评分附加到提示，但这不是所有评分信息在环境层面完全隔离的配置。
- 冻结候选不根据本批次结果修改，不能把三种执行器的各阶段或不同任务种类重复计算为更多独立样本。

[完整观测与用量](../experiments/results/research-evidence-20261006-0530/setup/scb-transfer01-observation-212749.json)；[协议、预登记与解释边界](scb-transfer-study-2026-10-06.md)。
