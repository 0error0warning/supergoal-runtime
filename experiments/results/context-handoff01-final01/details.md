# 上下文消融：用量、耗时与旧功能回归

固定观测：`2026-10-06T00:16:50.120166+00:00`。以下不改变原分数，不增加模型调用。

| 问题 | 条件 | 请求 | 输入 / 输出 tokens | 求解及在线审查 / 官方评分（分钟） | 派发至结束（分钟） |
|---|---|---:|---:|---:|---:|
| file_backup | carry_ledger | 81 | 2,371,737.00 / 33,229.00 | 13.06 / 0.96 | 14.44 |
| database_migration | carry_current | 98 | 3,112,646.00 / 44,242.00 | 17.15 / 1.30 | 18.87 |
| dag_execution | fresh_ledger | 109 | — / — | 22.57 / 0.64 | 23.56 |
| file_backup | carry_current | 71 | 1,467,489.00 / 27,758.00 | 12.78 / 0.91 | 14.10 |
| database_migration | fresh_ledger | 114 | 2,701,360.00 / 63,687.00 | 22.01 / 1.58 | 24.29 |
| dag_execution | fresh_current | 138 | — / — | 27.36 / 17.11 | 44.95 |
| file_backup | fresh_ledger | 69 | 1,074,659.00 / 25,306.00 | 10.09 / 0.89 | 11.46 |
| database_migration | fresh_current | 104 | 2,377,433.00 / 59,272.00 | 19.34 / 1.49 | 21.45 |
| dag_execution | carry_ledger | 103 | 3,244,204.00 / 47,797.00 | 16.44 / 0.77 | 17.69 |
| file_backup | fresh_current | 68 | 881,604.00 / 24,480.00 | 9.89 / 1.01 | 11.33 |
| database_migration | carry_ledger | 76 | 2,658,449.00 / 43,518.00 | 15.02 / 1.27 | 16.68 |
| dag_execution | carry_current | 114 | 4,209,931.00 / 53,719.00 | 16.21 / 0.68 | 17.31 |

token 回执不完整时总量留空，JSON 中另外保留观测小计。派发至结束包括环境、评分、清理等开销，不含等待资源派发的时间；多个任务共享主机，时间差不是纯模型速度差。

| 问题 | 条件 | 阶段转换 | 同名旧测试通过→失败 | 通过→错误 | 失败→通过 |
|---|---|---|---:|---:|---:|
| file_backup | carry_ledger | checkpoint_1 → checkpoint_2 | 0 | 0 | 0 |
| file_backup | carry_ledger | checkpoint_2 → checkpoint_3 | 0 | 0 | 0 |
| file_backup | carry_ledger | checkpoint_3 → checkpoint_4 | 8 | 0 | 0 |
| database_migration | carry_current | checkpoint_1 → checkpoint_2 | 0 | 0 | 0 |
| database_migration | carry_current | checkpoint_2 → checkpoint_3 | 0 | 0 | 0 |
| database_migration | carry_current | checkpoint_3 → checkpoint_4 | 0 | 0 | 0 |
| database_migration | carry_current | checkpoint_4 → checkpoint_5 | 0 | 0 | 0 |
| dag_execution | fresh_ledger | checkpoint_1 → checkpoint_2 | 0 | 0 | 0 |
| dag_execution | fresh_ledger | checkpoint_2 → checkpoint_3 | 0 | 0 | 2 |
| file_backup | carry_current | checkpoint_1 → checkpoint_2 | 0 | 0 | 0 |
| file_backup | carry_current | checkpoint_2 → checkpoint_3 | 0 | 0 | 0 |
| file_backup | carry_current | checkpoint_3 → checkpoint_4 | 8 | 0 | 0 |
| database_migration | fresh_ledger | checkpoint_1 → checkpoint_2 | 0 | 0 | 0 |
| database_migration | fresh_ledger | checkpoint_2 → checkpoint_3 | 0 | 0 | 0 |
| database_migration | fresh_ledger | checkpoint_3 → checkpoint_4 | 0 | 0 | 0 |
| database_migration | fresh_ledger | checkpoint_4 → checkpoint_5 | 0 | 0 | 0 |
| dag_execution | fresh_current | checkpoint_1 → checkpoint_2 | 0 | 0 | 0 |
| dag_execution | fresh_current | checkpoint_2 → checkpoint_3 | 0 | 0 | 0 |
| file_backup | fresh_ledger | checkpoint_1 → checkpoint_2 | 0 | 0 | 0 |
| file_backup | fresh_ledger | checkpoint_2 → checkpoint_3 | 0 | 0 | 0 |
| file_backup | fresh_ledger | checkpoint_3 → checkpoint_4 | 8 | 0 | 0 |
| database_migration | fresh_current | checkpoint_1 → checkpoint_2 | 0 | 0 | 0 |
| database_migration | fresh_current | checkpoint_2 → checkpoint_3 | 0 | 0 | 0 |
| database_migration | fresh_current | checkpoint_3 → checkpoint_4 | 0 | 0 | 0 |
| database_migration | fresh_current | checkpoint_4 → checkpoint_5 | 0 | 0 | 0 |
| dag_execution | carry_ledger | checkpoint_1 → checkpoint_2 | 1 | 0 | 12 |
| dag_execution | carry_ledger | checkpoint_2 → checkpoint_3 | 0 | 0 | 1 |
| file_backup | fresh_current | checkpoint_1 → checkpoint_2 | 0 | 0 | 0 |
| file_backup | fresh_current | checkpoint_2 → checkpoint_3 | 0 | 0 | 0 |
| file_backup | fresh_current | checkpoint_3 → checkpoint_4 | 15 | 0 | 0 |
| database_migration | carry_ledger | checkpoint_1 → checkpoint_2 | 0 | 0 | 0 |
| database_migration | carry_ledger | checkpoint_2 → checkpoint_3 | 0 | 0 | 0 |
| database_migration | carry_ledger | checkpoint_3 → checkpoint_4 | 0 | 0 | 0 |
| database_migration | carry_ledger | checkpoint_4 → checkpoint_5 | 0 | 0 | 0 |
| dag_execution | carry_current | checkpoint_1 → checkpoint_2 | 0 | 0 | 0 |
| dag_execution | carry_current | checkpoint_2 → checkpoint_3 | 0 | 0 | 0 |

下表只看各问题最后阶段，按上游测试模块文件名区分本阶段与先前阶段。模块归属不是完整语义需求映射；匿名跳过项不猜测归属。同名测试回归也受阶段环境变化影响，不能单独归因于产物改动。

| 问题 | 条件 | 最后阶段 | 先前模块通过 / 已报告 | 本阶段模块通过 / 已报告 |
|---|---|---|---|---|
| file_backup | carry_ledger | checkpoint_4 | 34 / 68 | 16 / 21 |
| database_migration | carry_current | checkpoint_5 | 100 / 116 | 16 / 20 |
| dag_execution | fresh_ledger | checkpoint_3 | 25 / 41 | 1 / 10 |
| file_backup | carry_current | checkpoint_4 | 37 / 68 | 18 / 21 |
| database_migration | fresh_ledger | checkpoint_5 | 108 / 116 | 17 / 20 |
| dag_execution | fresh_current | checkpoint_3 | 32 / 41 | 0 / 10 |
| file_backup | fresh_ledger | checkpoint_4 | 38 / 68 | 19 / 21 |
| database_migration | fresh_current | checkpoint_5 | 96 / 116 | 16 / 20 |
| dag_execution | carry_ledger | checkpoint_3 | 30 / 41 | 9 / 10 |
| file_backup | fresh_current | checkpoint_4 | 28 / 68 | 16 / 21 |
| database_migration | carry_ledger | checkpoint_5 | 102 / 116 | 15 / 20 |
| dag_execution | carry_current | checkpoint_3 | 20 / 41 | 5 / 10 |

[主要结果及干预核对](context-handoff01-progress.md)、[方法](context-handoff-study-2026-10-06.md)、[补充分析和来源散列](../experiments/results/context-handoff01-details.json)。
