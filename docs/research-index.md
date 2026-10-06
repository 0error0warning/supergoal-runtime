# 研究归档索引：2026-10-06

本轮登记的研究已经结束。能力结果、失败记录和工程修复一并归档；本次提交不代表生产部署。实验 GCP 实例已于北京时间 2026-10-06 09:31:15 停止，两块持久磁盘保留。

先读[最终研究结论](research-findings-2026-10-06.md)。不同研究采用不同任务、验收与统计单位；不要跨基准汇成一个成功率，也不要把阶段数当作独立任务数。

项目去留与范围判断另见[项目价值复审](project-direction-2026-10-06.md)；该文的建议与已经取得的实验结果分开表述。

## 保留的结论

- 最大公开对照中，SG 为 15/22，原生 Hermes 为 13/22，普通重发为 17/22；SG 对原生多用约 56% 的物理请求，稳定收益尚未建立。
- 完成判定是明确的薄弱点。共同起点研究的 18 次在线 pass 全部仍有严格测试失败；这属于三个开发问题的诊断，不能直接估计总体误判率。
- 保留执行历史、重放旧需求的结果混合。早期没有实际触发的消融、后来的 2×2 干预及共同起点补充对照分别报告。
- 事务、租约、预算、故障恢复与检查点生命周期具有具体工程证据。它们不证明开放任务语义验收正确或跨天自治可靠。
- 研究中的模型审查路径与生产候选的操作者配置检查器不同。配置、校准和接口测试不等于检查器完整覆盖用户目标。

## 各批次的最终入口

| 研究 | 规模与口径 | 结果入口 |
| --- | --- | --- |
| 原始合成任务与分阶段等待 | 过程与故障机制检查；重复种子不增加独立任务类型 | [早期报告](research-study-2026-10-05.md)、[来源说明](../experiments/results/README.md#earlier-synthetic-study) |
| public-gcp01 | 6 个任务 × 5 组 | [逐单元结果](public-gcp01-study.md) |
| public-parallel24-v1 | 22 个可执行配对任务 × 5 组；另 2 个环境不可用 | [完整对照](public-parallel24-v1-progress.md)、[最终清单](../experiments/results/public-parallel24-v1-final01/manifest.json) |
| AgentIF-OneDay | 6 个任务 × 3 组；产物均分，含单列的内存修复补评 | [完整分析](../experiments/results/oneday-transfer01-final-analysis.json)、[归档](../experiments/results/oneday-transfer01-final01/manifest.json) |
| mechanism01 | 4 个开发任务 × 7 组 | [机制触发与结果](mechanism01-progress.md) |
| 四框架迁移 | 3 个新任务 × 4 组 | [对照结果](harness-transfer01-progress.md)、[归档](../experiments/results/harness-transfer01-final01/manifest.json) |
| SlopCodeBench | 3 个问题 × 3 组，36 个相关阶段 | [逐阶段结果](scb-transfer01-progress.md) |
| context-handoff01 | 同 3 个开发问题 × 4 组，48 个相关阶段 | [2×2 对照](context-handoff01-progress.md)、[用量与回归](context-handoff01-details.md) |
| context-prefix01 | 同 3 个问题、相同起点 × 2 组，18 个后续阶段 | [配对结果](context-prefix01-progress.md)、[最终归档](../experiments/results/context-prefix01-final01/manifest.json) |

原始失败、超时、缺失回执和更早的观测都保留在 [results 来源说明](../experiments/results/README.md)指向的清单中。文件名含 `progress` 不代表对应批次还在运行；以上入口已包含最终结果。

## 代码位置与验证边界

| 范围 | 代码 / 文档 | 当前含义 |
| --- | --- | --- |
| 默认 v1 候选 | `supergoal_runtime/`；[通用验收](generic-acceptance.md) | 退役内置领域规则，使用用户目标与显式合同；保留迁移与宿主兼容 |
| 可选 v2 候选 | `supergoal_runtime/v2/`；[使用指南](v2-plugin-and-acceptance.md) | 事务状态、租约、预算、派工、验收校准和争议流程；默认配置仍为 v1 |
| 研究执行适配器 | `experiments/public_benchmarks/` | 实际容器身份、终端会话、模型传输、累计预算、快照与上下文干预 |
| 操作与故障探针 | `experiments/infrastructure/` | 实验启动、冻结、回收及故障验证；包含特定实验主机配置 |
| 评分与分析 | `experiments/analysis/`、`experiments/public_benchmarks/analyze_*.py` | 分开记录产物得分、运行异常、物理请求及机制是否触发 |
| 冻结版本与原始收据 | `experiments/baselines/`、`experiments/results/` | 复核历史结果的依据；后来的修复不会重写旧成绩 |

快照存档覆盖镜像与文件系统，不覆盖任意进程内存、挂载卷或整机恢复。当前仓库保存收据和源码归档；远端的 Docker 内容存档没有复制进 Git。

## 本次整理与本地复核

本次只整理入口、修正过期状态描述、保留研究字节和增加归档校验；没有重新调用模型、重评产物或切换生产。历史宿主验证仍按其原有源码版本解释。

在仓库根目录执行 `python scripts/verify_research_archive.py`，检查各归档清单的 SHA256 与已记录的字节数。这是文件完整性检查，不能替代任务评分。检查脚本也验证清单明确注明的迁移存放路径。

整理时验证了 **37 份清单、730 个文件引用、696 个唯一文件**，全部匹配。活动代码的 Ruff 检查和 wheel 构建通过；独立的分析器回归另外 **3 项通过**。归档中的历史源码与启动脚本保留原样，不参与活动代码的风格修正。

本地 Windows / Python 3.13.5 的 unit、integration、replay 回归为 **207 passed、6 skipped、3 deselected**。两项 profile 测试需要完整 Hermes；Windows 上的既有 PID 检查会中断进程，因此单列排除。跳过项包括真实宿主 ABI 和 POSIX 专用检查。该结果不等同于完整 Linux 宿主回归；已有的 [39 项真实宿主检查](../experiments/results/plugin-v2-checks02-evidence/evidence-manifest.json)仍独立保留。

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit tests/integration tests/replay -q -o addopts= `
  --deselect tests/unit/test_store.py::test_state_path_prefers_context_override_over_environment `
  --deselect tests/unit/test_store.py::test_concurrent_context_profiles_do_not_cross_write `
  --deselect tests/unit/test_phase4_phase5_runtime.py::test_wait_barrier_uses_live_pid_and_releases_dead_pid
```

初次未排除 PID 用例的本地运行在该已知边界处中断；没有把它记录为完整通过。默认 CI 在 Linux 执行该 PID 用例。本地虚拟环境、缓存与 wheel 输出均被 Git 忽略；原始观测文件不按临时缓存处理。

提交 `45e4544` 的 [GitHub CI](https://github.com/0error0warning/supergoal-runtime/actions/runs/37430966315) 在 Linux / Python 3.11 与 3.13 上均得到 **216 passed、1 skipped、2 deselected**，含上述 3 项分析器回归；两组 Ruff、730 个归档引用校验、编译与 wheel 构建全部通过。真实宿主 ABI 因未安装 Hermes 而跳过，两个 profile 用例按原 CI 配置排除。

同次 CI 的 `latest-hermes-main-compatibility` **失败**：旧通用接口补丁 `8efcb2a68` 在当前上游 Hermes 的命令、插件、压缩及网关文件发生 cherry-pick 冲突，尚未执行插件测试。这条失败记录保留，未改成允许失败或静默跳过。当前提交不能声称兼容最新原生 Hermes；更新宿主接口补丁属于独立的兼容性工作。
