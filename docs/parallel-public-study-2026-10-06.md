# 公开任务扩展与真实恢复实验

## 已落实的设计

- Terminal-Bench 2.1 固定 24 个新 task ID，13 类，5 组，共 120 个登记试次。官方参考解逐题验收后按已冻结候选执行，不根据模型成绩替换题目。
- AgentIF-OneDay 固定 6 个多阶段任务，原生 Hermes、普通重发目标、完整 v2 三组，共 18 个试次。包括日程调整、报名分析、Rust 教程网站、企业名称匹配、工业数据分析、销售仪表盘原型。
- 新队列共享 6 CPU / 48 GiB / 最多 6 个活动试次，审查资源计入预留。Terminal-Bench 参考解检查期间，OneDay 单个试次先行；参考解结束后 OneDay 上限为两个，与 TB 共用资源池。
- 本机只用于编辑和 SSH；Docker、Hermes 及所有模型试次在 GCP。模型通道由腾讯云直接转发，实验不依赖 Windows 保持在线。

正式试次在源码冻结后启动。改配 OneDay 评分器沿用上游文件解析、提示、逐条规则解析及最终汇总，只将模型传输接到 SWE2；它不是 Gemini 官方排行榜分数。官方题目、规则、参考答案与执行器隔离，最终评分看实际产物，分组名称不提供给评分器。

## 已经观察到的结果

开发任务的真实故障注入：固定请求序号附近杀死 SDK 子进程，原生单次执行未完成产物，普通工作区重启和 v2 均通过预设产物检查；中途切断响应流时三组都完成了检查。这支持恢复链路可用，也说明简单重启和 Hermes 原有重试必须保留为对照。它没有证明 v2 比重启更强。

内核实际子进程以退出码 73 突然结束后，另一进程从 SQLite 恢复；旧令牌被拒绝，中断回合计入预算。8 个操作系统进程争用资源池时，只有两个各占 3 CPU 的请求获准，总量保持 6 CPU，结束后全部释放。完整 Harbor 控制器或 VM 重启的恢复仍未证明。

SWE2 合成并发探针在 4、6 路各自全部完成；小样本中 6 路吞吐未高于 4 路，不能把并发数当作加速倍数。一次合成图片识别通过。OneDay 文件评分校准中，空提交 0/7、官方 HTML 参考产物 7/7，经过实际文件解析与截图流程。早期汇总错误及无参考答案的校准样例保留在 calibration01；修正为直接调用官方汇总后，calibration02 才用作启动门槛。

首个 OneDay 任务 `taskif_83` 三组已返回：原生 7/11（9 次请求）、普通重发目标 8/11（20 次请求）、完整 v2 10/11（16 次请求）。这只是单个任务，暂不能作为总体增益结论。后续状态以收据为准。

24 项 TB 参考解检查结束，22 项通过。`protein-assembly` 的参考脚本遇到约束求解错误，`mcmc-sampling-stan` 的参考环境缺少 RStan 依赖；这两项共 10 行标为不可用，原分数保留为参考解零分，不能作为模型零分。其余 110 行进入实际队列；首个 `bn-fit-modify / native` 已完成官方评分 1.0。

本地相关检查为 41 passed、1 skipped；跳过项因本机没有 Hermes 模块。两项真实宿主 ABI 断言已在 GCP 的固定 Hermes 0.21.3 源码环境中另行通过，使用临时 HOME，没有向固定运行时安装 pytest。这是单独执行测试函数的 ABI 检查，不能记为本地 pytest 的通过项。

## 储存运行时的额外审计项

ABI 检查触发 Hermes 的 SQLite 提示：当前 Python 绑定 SQLite 3.49.1，Hermes 自身回退到 DELETE journal。v2 Kernel 的冻结代码仍设置 WAL。SQLite 官方说明该旧版本存在需要同一数据库上多连接并发写入/检查点才会触发的 [WAL-reset 问题](https://sqlite.org/wal.html#walresetbug)。依据当前适配器源码，每试次数据库独立，Kernel 调用在同一事件循环中同步执行；本批没有安排同一 Kernel 数据库上的并行写入，这是代码路径审计判断，而非对该 SQLite 缺陷的修复。晋升为通用多进程部署前仍须修复 SQLite 版本或日志模式兼容；没有在冻结批次中途热改运行时，也不以本次试验宣称完成了任意并发下的储存可靠性验证。

## 费用与截止

GCP 保持 2026-10-06 22:24:06 北京时间自动 STOP。运行中修改截止时间被 GCP 拒绝，未停掉正在运行的实验。求解及评分需要的剩余时限不足时不再接收新任务；数据盘保留。后续是否延长应在任务排空后处理。

## 可复现记录

- [TB 方法](../experiments/public_benchmarks/PUBLIC-PARALLEL24-V1.md) 与 [登记](../experiments/public_benchmarks/registration-public-parallel24-v1.json)
- [OneDay 原登记](../experiments/public_benchmarks/registration-oneday-transfer01.json) 与 [执行前修订](../experiments/public_benchmarks/registration-oneday-transfer01-amendment02.json)
- [真实 worker 故障](../experiments/results/worker-fault-dev05.json)、[内核跨进程恢复](../experiments/results/kernel-process-crash-dev05.json)、[资源池进程竞争](../experiments/results/resource-pool-smoke01.json)
- [真实宿主 ABI 断言](../experiments/results/v2-real-host-abi-dev05.json)
- [TB 参考解验收快照](../experiments/results/tb-oracle-parallel24-receipt.json)、[OneDay 原始执行快照](../experiments/results/oneday-transfer01-receipt.json)、[评分与解析审计](../experiments/results/oneday-transfer01-analysis.json)

本地收据是拉取时快照。GCP 的 `/var/lib/supergoal-lab/setup` 保存实时收据；`exports/experiments/results` 保存可分享的汇总。最终报告不自动补跑、重评分或挑选更高分。
