# 按现有服务器安排公开基准

2026-10-05。用户提供的 `grok-bot` 足以开始串行 CLI 研究；不需要先购置更大的机器。仅通过 Tailscale 连接，账号 `box`。本机不使用 Docker。

## 实测资源与安排

首次只读检查：Debian 13 x86_64、8 个逻辑 CPU、约 15.64 GiB 总内存、约 4.7–5.0 GiB 可用内存、约 109 GiB 空闲磁盘。内存随已有应用负载变化，不能把总内存全部分给实验。保留原有应用，实验并发固定为 1。

Terminal-Bench 2.1 固定源 `7131e4375048a0e408a8fb404b5f499d726b695b`，89 个官方任务的资源元数据已逐个核验 Git blob SHA。68 个声明 1 CPU、2048 MB 内存；放宽到 2 CPU、4096 MB 后共 81 个，覆盖 16 类，其中 26 个上游标为 hard。轻量环境不等于简单任务。这些数字只说明声明资源匹配，不能代替实测和官方 oracle 验证。

- 先预检 1 CPU / 2048 MB 的任务，保留其原始 CPU、内存、超时及最终评分器。
- 每次运行前检查空闲内存；任务限额、控制器开销和系统余量必须同时容纳。4 GB/8 GB 任务等实际余量允许后再评估。
- 镜像按需准备，逐任务运行。初期以约 30 GiB 的实验工作空间为规划额度，包含镜像、临时层及归档；这是可调整的预算，不是官方最低要求。
- 保留轨迹、结果、散列和失败记录。只清理本研究明确创建且已结束的容器/镜像，不使用全机 prune。
- 模型调用远程 `devin/swe-2`，不在该服务器部署模型权重，无需推理 GPU。

详见 [完整资源目录](../experiments/public_benchmarks/terminal-bench-2-1-catalog.json) 和 [复查脚本](../experiments/public_benchmarks/task_resources.py)。`storage_mb=10240` 不包含镜像下载、解压及构建峰值；实际磁盘余量仍须单独检查。

## 隔离环境

Docker、Compose 与 Harbor 安装在远程实验机。实验 Docker 使用独立数据目录 `/var/lib/supergoal-lab/docker`、专用 Unix socket 和桥接网络。该机自身位于 overlay 文件系统内，普通 overlay2 初始化失败，改用 `fuse-overlayfs`；需将存储驱动计入环境收据和性能解释。Harbor 固定为 `0.24.0`，与 LongHorizon 历史入口的 `0.18.0` 不同，结果必须如实标注。

环境启动、资源限额、网络、官方参考解及评分流程的成功都要各自核验；安装完成不代表基准复现完成。最终模型试次之前冻结任务目录散列、镜像 digest、控制器版本、预算和分组。

## 桌面基准单独评估

WeaveBench 的 LongHorizon 入口要求至少 32 GB RAM，完整 114 任务建议至少 150 GB 空闲磁盘；OSWorld 入口涉及大容量 VM 镜像和版本匹配。此前提出的 16 核 / 64 GB / 500 GB 是多套环境和并发的宽裕配置，不是开展 Supergoal 研究的必要门槛。

当前先推进 Terminal-Bench；AgentIF-OneDay 按任务核验已有 LibreOffice、浏览器及媒体工具。暂不把 WeaveBench/OSWorld 的完整 GUI 环境列为必须完成的服务器前置项。KVM 仅适用于相关桌面虚拟机，不是 CLI 研究要求。首次检查 `/dev/kvm` 存在，但 `box` 无访问权限，未修改该权限。

来源：[Terminal-Bench 2.1 任务源](https://github.com/harbor-framework/terminal-bench-2-1/tree/7131e4375048a0e408a8fb404b5f499d726b695b/tasks)、[WeaveBench 说明](https://github.com/AMAP-ML/LongHorizon-Harness/blob/a1dd930614972b92361c1b9cd6aac441a6db5a65/eval/WeaveBench-harness/README.md)、[OSWorld 说明](https://github.com/AMAP-ML/LongHorizon-Harness/blob/a1dd930614972b92361c1b9cd6aac441a6db5a65/eval/OSWorldv2-harness/README.md)。
