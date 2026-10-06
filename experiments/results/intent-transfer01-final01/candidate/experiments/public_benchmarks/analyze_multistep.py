"""Render registered trajectories without ranking incomplete arm subsets."""
import argparse
import json
from pathlib import Path


def percent(value):
    return '—' if value is None else f'{100 * value:.2f}%'


def markdown(bundle, source_link):
    complete = str(bundle['cohort_status']).startswith('complete')
    lines = ['# SCBench 多阶段研究' + ('结果' if complete else '进度'), '',
             f"数据截至 {bundle['collected_at']}。计划 {bundle['statistical_units']} 个不同问题、"
             f"{bundle['planned_trajectories']} 条轨迹。当前批次状态：`{bundle['cohort_status']}`。", '',
             '各检查点属于同一个问题，不能当作独立样本。未完成的组不与不同任务组成的其他组直接排名。', '',
             '| 问题 | 组 | 状态 | 已评分 / 计划阶段 | 严格测试平均通过率 | 请求 | 全部阶段严格通过 | 在线 pass 但严格未全过 | SDK 未完成阶段 |',
             '|---|---|---|---:|---:|---:|---|---:|---:|']
    for row in bundle['rows']:
        returned = 'result_path' in row
        phases = f"{row['scored_checkpoints']}/{row['expected_checkpoints']}" if returned else '—'
        all_passed = ('是' if row['all_registered_checkpoints_strictly_passed'] else '否') if returned else '待返回'
        disagreements = sum(d['online_pass_with_strict_shortfall'] for d in row.get('checkpoint_diagnostics', []))
        incomplete = sum(d.get('worker_incomplete', d.get('controller_status') == 'worker_incomplete')
                         for d in row.get('checkpoint_diagnostics', []))
        lines.append(f"| {row['task_id']} | {row['arm']} | {row['status']} | {phases} | "
                     f"{percent(row.get('mean_available_checkpoint_strict'))} | {row.get('charged_physical_requests', '—')} | "
                     f"{all_passed} | {disagreements if returned and row['arm'] == 'sg_v2' else '—'} | {incomplete if returned else '—'} |")
    lines += ['', '## 逐阶段诊断', '',
              '| 问题 / 组 / 阶段 | 执行 / 审查轮数 | 在线判断 | 严格通过率 | 旧阶段测试通过 / 总数 |',
              '|---|---:|---|---:|---:|']
    for row in bundle['rows']:
        for diag in row.get('checkpoint_diagnostics', []):
            details = diag.get('official_reward_details') or {}
            regression = f"{details.get('regression_passed', '—')}/{details.get('regression_collected', '—')}"
            lines.append(f"| {row['task_id']} / {row['arm']} / {diag['checkpoint']} | "
                         f"{diag['executor_episodes']} / {diag['review_episodes']} | {diag['online_verdict'] or '无'} | "
                         f"{percent(diag['strict_pass_rate'])} | {regression} |")
    lines += ['', '## 解释范围', '',
              '- 原始官方分数保留；按已评分阶段计算的平均分不代表完整问题成功。执行或评分异常在原始记录中单列。',
              '- SDK 未完成可以不抛出 Harbor 异常；需结合控制器状态和服务故障审计，不把这类零分直接解释为模型求解能力。',
              '- core 检查通常只是基础子集，不能替代 strict 全部测试。新阶段的平均分下降，也不自动等于旧功能退化；同名旧测试的转换另行核对。',
              '- 请求包含执行、审查、传输重试及不确定预留。角色之间共享上限；相同上限并不意味着相同实际 token 用量。',
              '- 上游会逐步提供新需求；该流程不等于一次委托后的跨天自主规划。',
              '- 上游保留代码质量诊断历史；适配器不把评分附加到提示，但这不是所有评分信息在环境层面完全隔离的配置。',
              '- 冻结候选不根据本批次结果修改，不能把三种执行器的各阶段或不同任务种类重复计算为更多独立样本。', '',
              f'[完整观测与用量]({source_link})；[协议、预登记与解释边界](scb-transfer-study-2026-10-06.md)。', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    source = '../' + args.bundle.resolve().relative_to(root).as_posix()
    args.output.write_text(markdown(json.loads(args.bundle.read_text(encoding='utf-8')), source), encoding='utf-8')
    print(str(args.output))


if __name__ == '__main__':
    main()
