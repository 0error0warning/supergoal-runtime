# GCP 临时实验机

2026-10-05 创建。GCP CLI 587.0.0 已在 Windows 用户目录安装并完成 OAuth 登录。
CLI 单独使用本机 HTTP 代理 `127.0.0.1:7900`；继承的旧代理端口 7897 导致首次凭据交换失败，修复后重新授权成功。

**当前状态（2026-10-06 09:31 北京时间）：本轮已登记研究全部完成并归档，实例已提前停止，控制面为 `TERMINATED`。** 40 GiB 启动盘、200 GiB 数据盘与实验数据保留；没有删除实例或磁盘。实验模型隧道已停止，腾讯云生产 Hermes 服务状态未变。下方“已启动”描述是启动时的历史记录。[完整结果](research-findings-2026-10-06.md)、[存档校验](../experiments/results/lab-research-close01/manifest.json)、[停机收据](../experiments/results/lab-service-stop01/manifest.json)。

## 实际配置

| 项目 | 值 |
| --- | --- |
| GCP project | `gen-lang-client-0258124817` |
| 实例 | `supergoal-lab-20261005` |
| Zone | `us-central1-a` |
| 机型 | `n2-highmem-8`，8 vCPU / 64 GiB |
| CPU | Intel Cascade Lake |
| 镜像 | `ubuntu-2404-noble-amd64-v20260918`，Ubuntu 24.04 x86_64 |
| 系统盘 | 40 GiB `pd-balanced` |
| 实验数据盘 | 200 GiB `pd-balanced`，ext4，挂载 `/var/lib/supergoal-lab` |
| 嵌套虚拟化 | 已启用，并实际启动、关闭一个 KVM Linux guest |
| 自动停机 | 2026-10-06 22:24:06 北京时间 / 14:24:06 UTC，动作 STOP |
| VM 服务账号 | 无 |
| 管理入口 | 专用 VPC，仅允许 Google IAP 网段访问 SSH |
| Tailscale | 1.102.4，设备名 `supergoal-gcp`，已授权在线，`100.97.233.53` |

创建前实际配额：全项目 `CPUS_ALL_REGIONS=12`，区域 `N2_CPUS=32`，`SSD_TOTAL_GB=250`，使用量均为零。40+200=240 GiB，留 10 GiB 平衡盘配额。实例进入 RUNNING 后的资源收据见下方链接。

数据盘设为不随实例删除；停止实例也会保留两块磁盘。按已核对的 us-central1 按需价，计算、240 GiB 磁盘和一个使用中的 IPv4 合计约 $0.562/小时，24 小时约 $13.49，不含出站流量、快照和其他服务。停机后保留磁盘仍约 $0.79/天。API 已确认计费启用，但没有读到赠金剩余金额或失效日；不能把用户最初的 $300 当作实时余额。

价格来源：[N2](https://cloud.google.com/products/compute/pricing/general-purpose)、[磁盘](https://cloud.google.com/compute/disks-image-pricing)、[IPv4](https://cloud.google.com/vpc/network-pricing)。

## 已验证的基础设施

- 初始约 61 GiB 可用内存；数据分区格式化后约 196 GiB 可用。
- KVM 测试使用 `-accel kvm`，实际启动 Linux guest，输出 `SUPERGOAL_NESTED_KVM_GUEST_BOOTED` 后正常关机，没有回退到软件模拟。
- Docker Engine 29.8.2、Compose 5.6.0、containerd 2.3.6；独立服务 `supergoal-docker.service`，使用数据盘上的 `overlay2` 存储。
- Docker socket：`unix:///var/lib/supergoal-lab/run/docker.sock`。标准 Docker/Containerd 服务在这个新建空白 VM 上停止并禁用，实验服务自行管理运行时。
- 容器实际读到 0.5 CPU、128 MiB 内存、128 进程的 cgroup v2 限额；公网 HTTPS 成功，云元数据 TCP 和宿主 SSH TCP 不可达。元数据拦截规则计数器记录了对应丢弃包。
- 基础设施验证容器已退出并移除。Windows 本机没有安装或运行实验 Docker。

首次初始化的 Tailscale 公钥文件因 `umask` 产生 0640 权限，APT 的非特权签名验证进程无法读取；改为公开文件应有的 0644 后完成安装，没有关闭签名或 TLS 验证。第一次 KVM 探针缺少完成标志的收据也保留为 attempt01；修正 QEMU 对 stdin 的占用后取得了明确的 guest 启动和关机证据。

## 已启动的公开任务实验

2026-10-05 23:20:35 北京时间启动 `public-gcp01`，6 个官方 hard 任务、6 个类别、5 个分组，共 30 个登记试次。每个 task/arm 一次；没有增加同题随机种子。第一个试次是 `configure-git-webserver / native`，实际容器限额已读取为 1 CPU / 2 GiB。

腾讯云的独立 `supergoal-gcp-model-tunnel.service` 将真实模型路由转发到 GCP loopback 18321；仅这个受限 SSH 账号可监听该端口。Hermes 0.21.3 源码来自已核验的生产快照，宿主 Python 3.13.5，Harbor 0.24.0。真实 SWE2 冒烟测试完成了两次请求及一次容器工具调用，宿主文件不可读、命令超时返回 124。模型运行无需 Windows 保持在线。

6 个任务的官方参考解在新环境均获得 1.0；这只证明评分环境通过预检，不属于任何模型组的成绩。任务树、镜像 ID/digest、运行时依赖及分组顺序均在模型试次前冻结。沿用 public-dev04 的运行时与适配器字节，不将迁移主机写成算法改进。

批次由 GCP `supergoal-public-gcp01.service` 串行执行。遇到非正常试次停下审计，不自动重跑；结束或异常后生成完整或部分报告。导出位于数据盘 `exports/experiments/results/public-gcp01-bundle.json` 和 `public-gcp01-analysis.json`。控制器错误保留缺失的官方分数，同时计入端到端失败，避免只统计成功跑完的试次。

GCP 的 Docker、Compose、内核和存储驱动不同于 grok-bot，两台主机结果分别报告。旧主机资源等待队列在确认尚未开始下一行、没有子进程后取消；未终止模型试次，未修改旧候选与原始结果。取消收据单独保留。旧队列未执行的行仍是未执行，不记作失败。

本批任务的官方时限为 15 或 30 分钟，检验的是有难度的有限任务完成及机制差异，尚不能证明跨小时或天的自主工作。GUI 和办公长程基准仍需单独准备、登记与运行。

自动停机是云端设置，不依赖本机在线。若需要延长实验，先核对已消耗费用、剩余队列和保存状态，再调整这一个实例的截止时间。

2026-10-06 扩展已启动：[24 项 TB 与 6 项 OneDay 研究](parallel-public-study-2026-10-06.md)。GCP 拒绝运行中修改 `terminationTime`，所以尝试延期没有生效，当前截止仍为当天 22:24:06 北京时间。并发队列保留完整的求解、评分与收尾时限；相对 systemd 超时已通过运行时 drop-in 设为 24 小时，避免它早于云端绝对截止误杀正在执行的任务，服务 PID 保持不变。

## 记录与脚本

- [实例与配额收据](../experiments/results/gcp-infrastructure-2026-10-05.json)
- [KVM 实测收据](../experiments/results/gcp-kvm-smoke-2026-10-05.json)
- [容器限额与网络实测收据](../experiments/results/gcp-container-preflight-2026-10-05.json)
- [真实 Hermes/SWE2 冒烟收据](../experiments/results/gcp-hermes-smoke-2026-10-05.json)
- [六项参考解预检](../experiments/results/public-gcp01-oracle-preflight.json)
- [新批次方法与预登记](../experiments/public_benchmarks/PUBLIC-GCP01.md)
- [冻结环境](../experiments/public_benchmarks/environment-public-gcp01.json)
- [已启动批次快照](../experiments/results/public-gcp01-progress.json)
- [旧等待队列取消收据](../experiments/results/public-dev04-gcp-migration-cancel.json)
- [主机初始化](../experiments/infrastructure/gcp_bootstrap.sh)
- [KVM 探针](../experiments/infrastructure/gcp_kvm_smoke.sh)
- [Docker 安装配置](../experiments/infrastructure/gcp_docker.sh)
- [容器预检](../experiments/infrastructure/gcp_preflight.py)

本机操作记录位于 `%LOCALAPPDATA%\Supergoal\gcp\2026-10-05`；OAuth 凭据由 gcloud 保存在正常用户凭据目录，SSH 私钥保存在用户 `.ssh`，均不进入项目仓库。
