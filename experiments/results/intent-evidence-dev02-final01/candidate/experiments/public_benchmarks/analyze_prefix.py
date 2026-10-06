"""Paired suffix results with prefix-offset activation and unchanged raw grades."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from experiments.public_benchmarks.analyze_context import analyze as context_analysis
from experiments.public_benchmarks.analyze_context_details import analyze as detail_analysis


def analyze(bundle, receipt, regressions, registration):
    if (registration['arms'] != ['fresh_ledger', 'fresh_current']
            or registration['prefix_checkpoint_count'] != 1):
        raise ValueError('This analysis is for the registered two-arm, one-prefix design')
    result = context_analysis(bundle, registration)
    details = detail_analysis(bundle, receipt, regressions, registration)
    lookup = {(r['task_id'], r['arm']): r for r in details['rows']}
    for row in result['rows']:
        extra = lookup[row['task_id'], row['arm']]
        for key in ('usage', 'modules', 'regressions'):
            if key in extra:
                row[key] = extra[key]
        if 'usage' in extra:
            row['incomplete_usage_records'] = extra['usage']['requests_with_incomplete_token_usage']
    result.update(prefix_requests_observed_once=registration['prefix_requests_observed_once'],
                  prefix_requests_charged_to_suffix=0,
                  scope='Three outcome-informed development problems, two suffix branches per frozen first-stage artifact. '
                        'One continuation per condition, not a holdout or statistical superiority claim. '
                        'No change to raw grades and no independent-task inflation from stages or shared prefixes.')
    return result


def markdown(result):
    labels = {'fresh_ledger': '回放旧需求', 'fresh_current': '仅当前需求'}

    def number(value, *, percent=False, scale=1):
        if value is None:
            return '未知'
        return f'{value:.2%}' if percent else f'{value / scale:,.2f}'

    lines = ['# 同一起点的需求回放对照', '',
             f"观测时间：`{result['collected_at']}`；批次状态：`{result['cohort_status']}`。", '',
             '三个已观察过的问题，各从同一第一阶段产物分出两条后续轨迹。两侧都清空对话历史，'
             '区别是执行器是否回放旧公开要求；审查器始终得到完整公开要求。共六条轨迹、十八个后续阶段，'
             '独立问题数仍为三个。下表严格均分不代表整条轨迹全部正确。', '',
             '| 问题 | 条件 | 后续严格均分 | 已评分阶段 | 后续全过 | 新物理请求 |',
             '|---|---|---:|---:|---|---:|']
    for row in result['rows']:
        passed = row['all_checkpoints_strictly_passed']
        lines.append(f"| {row['task_id']} | {labels[row['arm']]} | {number(row['complete_strict_mean'], percent=True)} | "
                     f"{row['scored_checkpoints']}/{row['expected_checkpoints']} | "
                     f"{'未知' if passed is None else '是' if passed else '否'} | {row['requests']} |")
    lines += ['', '## 配对差值', '', '方向固定为回放减仅当前需求，两侧完整返回后才计算。', '',
              '| 问题 | 严格均分差（百分点） | 新请求差 |', '|---|---:|---:|']
    for pair in result['contrasts'][0]['pairs']:
        delta = pair['strict_mean_difference']
        lines.append(f"| {pair['task_id']} | {'未知' if delta is None else f'{100 * delta:+.2f}'} | {pair['request_difference']} |")
    lines += ['', '起点按上一批登记顺序取每个问题的首条轨迹，不按成绩挑选。'
              '固定起点控制了初始产物差异，后续生成随机性和实际用量差异仍存在。'
              '这是开发诊断，不宣称总体显著性、稳定优越性或跨天自治。', '', '## 干预与验收', '',
              '| 条件 | 实际首轮输入符合策略 / 已观察 | 旧要求回放 | 旧要求省略 | 多次执行阶段 | 在线 pass | pass 中严格未全过 |',
              '|---|---:|---:|---:|---:|---:|---:|']
    for arm in result['arms']:
        activations = [a for r in result['rows'] if r['arm'] == arm for a in r['activations']]
        diagnostics = [d for r in result['rows'] if r['arm'] == arm for d in r['checkpoint_diagnostics']]
        counts = [sum(bool(a.get(k)) for a in activations) for k in
                  ['actual_payload_matches_declared_policy', 'requirement_replay_exercised', 'prior_requirement_omission_exercised']]
        verdicts = Counter(d['online_verdict'] for d in diagnostics)
        lines.append(f'| {labels[arm]} | {counts[0]}/{len(activations)} | {counts[1]} | {counts[2]} | '
                     f"{sum((d.get('executor_episodes') or 0) > 1 for d in diagnostics)} | {verdicts['pass']} | "
                     f"{sum(bool(d['online_pass_with_strict_shortfall']) for d in diagnostics)} |")
    lines += ['', f"输入/恢复分析异常：{len(result['audit_errors'])}。主机上的独立最终审计另核对共同镜像、文件清单、"
              '原始公开要求与调用日记。没有输入证据的阶段不计为已触发。', '',
              '## 新旧测试与回归', '',
              '按原测试模块编号分组；这不是完整的语义需求映射。下表逐阶段同时报告旧模块与当前模块，'
              '防止把旧测试通过掩盖新阶段未实现的情况。匿名跳过项不猜测归属。', '',
              '| 问题 | 条件 | 阶段 | 旧模块通过 / 已报告 | 当前模块通过 / 已报告 | 旧测试通过→失败 / 错误 |',
              '|---|---|---|---|---|---|']
    for row in result['rows']:
        changes = {t['to_checkpoint']: t for t in row.get('regressions', [])}
        for module in row.get('modules', []):
            parts = []
            for group in ('prior_modules', 'current_module'):
                counts = module['groups'][group]
                parts.append(f"{counts.get('PASSED', 0)} / {sum(counts.values())}")
            change = changes.get(module['checkpoint'], {})
            regress = ' / '.join(str(change[k]) if change.get(k) is not None else '未知'
                                 for k in ('pass_to_fail_count', 'pass_to_error_count'))
            lines.append(f"| {row['task_id']} | {labels[row['arm']]} | {module['checkpoint']} | "
                         + ' | '.join(parts) + f' | {regress} |')
    lines += ['', '第一段转换使用共同前缀的原始评分日志；该日志未提供给模型。'
              '同名测试退步是回归信号，还受到阶段环境变化影响。完整节点差异保存在回归 JSON 中。', '',
              '## 实际成本', '',
              f"复用前缀此前共 {result['prefix_requests_observed_once']} 次请求，单列一次，新后续预算中记为零。", '',
              '| 问题 | 条件 | 输入 / 输出 tokens | 缺完整用量的请求 | 求解与在线审查 / 官方评分（分钟） | 派发至结束（分钟） |',
              '|---|---|---|---:|---|---:|']
    for row in result['rows']:
        usage = row.get('usage', {})
        lines.append(f"| {row['task_id']} | {labels[row['arm']]} | {number(usage.get('input_tokens'))} / "
                     f"{number(usage.get('output_tokens'))} | {usage.get('requests_with_incomplete_token_usage', '未知')} | "
                     f"{number(usage.get('solver_and_online_review_seconds'), scale=60)} / "
                     f"{number(usage.get('official_grading_seconds'), scale=60)} | "
                     f"{number(usage.get('trial_wall_seconds'), scale=60)} |")
    lines += ['', '未知 token 总量不填零，观测小计保留在 JSON。派发至结束包含环境准备、评分与清理，'
              '不含等待资源派发；并发共享主机会影响时长。', '',
              '[预定方法](context-prefix-study-2026-10-06.md)、'
              '[分析及来源](../experiments/results/context-prefix01-analysis.json)。', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser()
    for name in ('bundle', 'receipt', 'regressions', 'registration', 'output', 'markdown_output'):
        parser.add_argument('--' + name.replace('_', '-'), type=Path, required=True)
    args = parser.parse_args()
    sources = [args.bundle, args.receipt, args.regressions, args.registration]
    result = analyze(*(json.loads(p.read_text(encoding='utf-8')) for p in sources))
    scripts = [Path(__file__), Path(__file__).with_name('analyze_context.py'), Path(__file__).with_name('analyze_context_details.py')]
    result['source_sha256'] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources + scripts}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    args.markdown_output.write_text(markdown(result), encoding='utf-8')


if __name__ == '__main__':
    main()
