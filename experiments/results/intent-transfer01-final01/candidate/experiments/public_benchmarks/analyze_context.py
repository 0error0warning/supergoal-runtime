"""Problem-level 2x2 analysis with actual SDK-input activation checks."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from experiments.public_benchmarks.context_policy import CONDITIONS


def analyze(bundle, registration):
    arms = registration.get('arms', list(CONDITIONS))
    prefix_count = registration.get('prefix_checkpoint_count', 0)
    if not arms or not set(arms) <= set(CONDITIONS) or prefix_count not in (0, 1):
        raise ValueError('Unknown conditions or prefix length')
    expected = {(r['task_id'], r['arm']) for r in registration['planned_order']}
    source = {(r['task_id'], r['arm']): r for r in bundle['rows']}
    if set(source) != expected or len(source) != len(bundle['rows']):
        raise ValueError('Missing, duplicate or unregistered cells')
    tasks = {task['task_id']: task for task in registration['tasks']}
    rows, audits, contracts = [], [], {}
    for cell in registration['planned_order']:
        key = cell['task_id'], cell['arm']
        row = source[key]
        expected_checkpoints = tasks[key[0]].get('checkpoints', row.get('expected_checkpoints'))
        if row.get('expected_checkpoints') is not None and row['expected_checkpoints'] != expected_checkpoints:
            raise ValueError('Checkpoint count differs from registration')
        complete = row.get('scored_checkpoints') == expected_checkpoints and bool(expected_checkpoints)
        mean = row.get('mean_available_checkpoint_strict') if complete else None
        if mean is not None and not 0 <= mean <= 1:
            raise ValueError('Invalid strict score')
        ledger = bundle['problem_ledgers'].get(row.get('problem_id'), {})
        carry, replay = CONDITIONS[cell['arm']]
        activations = []
        previous_history = 0
        previous_history_sha256 = None
        for index, step in enumerate(ledger.get('steps', []), 1):
            child = bundle['child_controls'].get(step.get('control_id'), {})
            actual = child.get('first_executor_payload') or {'present': False}
            declared = step.get('context_input') or {}
            report = child.get('report') or {}
            contexts = report.get('executor_contexts') or []
            first = contexts[0] if contexts else {}
            matches = bool(actual['present'] and first and declared
                           and actual['history_sha256'] == declared['incoming_history_sha256'] == first['history_sha256']
                           and actual['history_messages'] == declared['incoming_history_messages'] == first['history_messages']
                           and actual['goal_sha256'] == declared['public_prompt_sha256']
                           and actual['prompt_sha256'] == first['prompt_sha256'])
            prior_count = index - 1 + prefix_count
            expected_replay = prior_count if replay else 0
            assignment_matches = (declared.get('condition') == cell['arm']
                                  and declared.get('prior_public_requirements') == prior_count
                                  and declared.get('prior_requirements_replayed') == expected_replay
                                  and (carry or not actual.get('history_messages')))
            carried_output_matches = (not carry or index == 1 or (
                actual.get('history_messages') == previous_history
                and actual.get('history_sha256') == previous_history_sha256))
            if actual['present'] and not carried_output_matches:
                audits.append({'task_id': key[0], 'arm': key[1], 'checkpoint': index,
                               'kind': 'carried_history_differs_from_previous_output'})
            if actual['present'] and not (matches and assignment_matches):
                audits.append({'task_id': key[0], 'arm': key[1], 'checkpoint': index, 'kind': 'actual_context_mismatch'})
            if first and not actual['present']:
                audits.append({'task_id': key[0], 'arm': key[1], 'checkpoint': index, 'kind': 'actual_context_payload_missing'})
            transitions = prior_count > 0 and matches and assignment_matches and carried_output_matches
            activations.append({'checkpoint': index + prefix_count, 'actual_payload_present': actual['present'],
                                'actual_payload_matches_declared_policy': matches and assignment_matches,
                                'carried_history_matches_previous_output': carried_output_matches if carry and index > 1 else None,
                                'incoming_history_messages': actual.get('history_messages'),
                                'history_carry_exercised': transitions and carry and bool(actual['history_messages']),
                                'fresh_reset_exercised_after_available_history': transitions and not carry and previous_history > 0,
                                'requirement_replay_exercised': transitions and replay,
                                'prior_requirement_omission_exercised': transitions and not replay})
            previous_history = step.get('outgoing_history_messages', 0)
            previous_history_sha256 = step.get('outgoing_history_sha256')
            contract = report.get('audit_contract_sha256')
            if contract:
                contracts.setdefault((key[0], index), set()).add(contract)
        boundaries = ledger.get('grader_history_boundaries', [])
        for boundary in boundaries:
            restored = boundary.get('restoration') or {}
            if restored.get('status') != 'restored' or restored.get('sha256') != boundary.get('original_sha256'):
                audits.append({'task_id': key[0], 'arm': key[1], 'kind': 'grader_history_restore_mismatch'})
        rows.append({**cell, 'status': row['status'], 'complete_strict_mean': mean,
                     'available_checkpoint_mean': row.get('mean_available_checkpoint_strict'),
                     'scored_checkpoints': row.get('scored_checkpoints', 0),
                     'expected_checkpoints': expected_checkpoints,
                     'all_checkpoints_strictly_passed': row.get('all_registered_checkpoints_strictly_passed'),
                     'requests': row.get('charged_physical_requests'),
                     'observed_input_tokens': row.get('observed_input_tokens'),
                     'observed_output_tokens': row.get('observed_output_tokens'),
                     'incomplete_usage_records': row.get('incomplete_usage_records'),
                     'execution_or_grading_exceptions': sum(bool(s.get('exception_info')) for s in row.get('steps', [])),
                     'checkpoint_diagnostics': [{k: d.get(k) for k in (
                         'checkpoint', 'online_verdict', 'strict_pass_rate', 'online_pass_with_strict_shortfall',
                         'executor_episodes', 'review_episodes', 'controller_status',
                         'upstream_http_errors', 'transport_error_calls')}
                         for d in row.get('checkpoint_diagnostics', [])],
                     'activations': activations})
    for (task, step), versions in contracts.items():
        if len(versions) > 1:
            audits.append({'task_id': task, 'checkpoint': step, 'kind': 'auditor_contract_differs_between_conditions'})
    lookup = {(r['task_id'], r['arm']): r for r in rows}
    contrasts = []
    for control, treatment in registration['contrasts']:
        pairs = []
        for task in registration['tasks']:
            a, b = lookup[task['task_id'], control], lookup[task['task_id'], treatment]
            pairs.append({'task_id': task['task_id'],
                          'strict_mean_difference': b['complete_strict_mean'] - a['complete_strict_mean']
                              if a['complete_strict_mean'] is not None and b['complete_strict_mean'] is not None else None,
                          'request_difference': b['requests'] - a['requests']
                              if a['requests'] is not None and b['requests'] is not None else None})
        contrasts.append({'control': control, 'treatment': treatment, 'direction': 'treatment minus control', 'pairs': pairs})
    interactions = []
    for task in registration['tasks'] if set(arms) == set(CONDITIONS) else []:
        values = {arm: lookup[task['task_id'], arm]['complete_strict_mean'] for arm in CONDITIONS}
        value = (values['carry_ledger'] - values['carry_current']) - (values['fresh_ledger'] - values['fresh_current']) \
            if all(v is not None for v in values.values()) else None
        interactions.append({'task_id': task['task_id'], 'difference_in_differences': value})
    return {'experiment': registration['experiment'], 'collected_at': bundle['collected_at'],
            'cohort_status': bundle['cohort_status'], 'statistical_units': len(registration['tasks']),
            'scope': 'Previously observed development problems; dependent checkpoints; descriptive contrasts only.',
            'arms': arms, 'prefix_checkpoint_count': prefix_count,
            'interaction_direction': '(carry_ledger - carry_current) - (fresh_ledger - fresh_current)' if interactions else None,
            'missing_outcomes_imputed': False, 'new_model_calls': 0,
            'rows': rows, 'contrasts': contrasts, 'interactions': interactions, 'audit_errors': audits}


def markdown(result):
    labels = {'carry_ledger': '保留历史＋回放需求', 'carry_current': '保留历史＋仅本阶段需求',
              'fresh_ledger': '清空历史＋回放需求', 'fresh_current': '清空历史＋仅本阶段需求'}
    lines = ['# 跨阶段上下文消融：实际干预与问题级结果', '',
             f"观测时间：`{result['collected_at']}`；批次状态：`{result['cohort_status']}`。",
             '三个已观察过的开发问题，四种条件共十二条轨迹。严格测试平均通过率不是整个问题全对率；'
             '不同检查点与分组不增加独立任务数。未返回结果不填零，不按部分返回子集排名。', '',
             '| 问题 | 条件 | 完整问题严格测试均分 | 已评分阶段 | 全阶段严格通过 | 物理请求 | 缺少完整 token 回执的请求 |',
             '|---|---|---:|---:|---|---:|---:|']
    for row in result['rows']:
        mean = row['complete_strict_mean']
        score = '待返回' if mean is None else f'{mean:.2%}'
        strict = '待返回' if row['all_checkpoints_strictly_passed'] is None else '是' if row['all_checkpoints_strictly_passed'] else '否'
        lines.append(f"| {row['task_id']} | {labels[row['arm']]} | {score} | {row['scored_checkpoints']}/{row['expected_checkpoints'] or '—'} | "
                     f"{strict} | {row['requests'] if row['requests'] is not None else '—'} | "
                     f"{row['incomplete_usage_records'] if row['incomplete_usage_records'] is not None else '—'} |")
    lines += ['', '## 实际干预核对', '',
              '| 条件 | 首轮 SDK 输入符合策略 / 已观测 | 历史携带 | 有历史后清空 | 旧需求回放 | 旧需求省略 |',
              '|---|---:|---:|---:|---:|---:|']
    for arm in CONDITIONS:
        records = [a for r in result['rows'] if r['arm'] == arm for a in r['activations']]
        counts = [sum(bool(a.get(k)) for a in records) for k in (
            'actual_payload_matches_declared_policy', 'history_carry_exercised',
            'fresh_reset_exercised_after_available_history', 'requirement_replay_exercised', 'prior_requirement_omission_exercised')]
        lines.append(f'| {labels[arm]} | {counts[0]}/{len(records)} | ' + ' | '.join(map(str, counts[1:])) + ' |')
    lines += ['', f"已发现的上下文/评分历史审计异常：{len(result['audit_errors'])}。缺少输入证据时不会计为已触发。",
              '各组审查器都接收完整公开要求。这里的回放是先前公开题面的原文，并非经过验证的事实记忆。', '',
              '## 在线验收与独立评分', '',
              '下列计数只描述已经返回的相关阶段，不是独立任务数或总体误判率。超过一次执行说明阶段内续跑实际发生；'
              '它与跨阶段历史交接是两个不同的机制。', '',
              '| 条件 | 已返回阶段 | 超过一次执行 | 在线 pass | 其中严格测试未全过 |',
              '|---|---:|---:|---:|---:|']
    for arm in CONDITIONS:
        records = [d for r in result['rows'] if r['arm'] == arm for d in r['checkpoint_diagnostics']]
        lines.append(f'| {labels[arm]} | {len(records)} | '
                     f"{sum((d.get('executor_episodes') or 0) > 1 for d in records)} | "
                     f"{sum(d['online_verdict'] == 'pass' for d in records)} | "
                     f"{sum(bool(d['online_pass_with_strict_shortfall']) for d in records)} |")
    lines += ['',
              '## 问题内配对', '',
              '下表方向为“后组减前组”，单位为严格测试均分的百分点。两侧问题未完整评分时留空。'
              '这是预登记开发对照的描述性结果，单次输出随机性及不同实际计算量仍限制归因。', '',
              '| 前组 → 后组 | 问题 | 严格均分差 | 物理请求差 |', '|---|---|---:|---:|']
    for contrast in result['contrasts']:
        for pair in contrast['pairs']:
            value = pair['strict_mean_difference']
            score = '—' if value is None else f'{100 * value:+.2f}'
            calls = pair['request_difference']
            lines.append(f"| {labels[contrast['control']]} → {labels[contrast['treatment']]} | {pair['task_id']} | "
                         f"{score} | {calls if calls is not None else '—'} |")
    lines += ['', '交互项为 `(保留＋回放 − 保留＋当前) − (清空＋回放 − 清空＋当前)`，四个条件全部返回后才计算：', '']
    for row in result['interactions']:
        value = row['difference_in_differences']
        lines.append(f"- {row['task_id']}：" + ('待完整配对' if value is None else f'{100 * value:+.2f} 个百分点'))
    lines += ['', '本研究统一使用修复后的终端桥接器；不与此前冻结候选合并统计。'
              '它不检验自主发现阶段、上下文溢出、整个控制器重启或跨天连续自治。完整 token 总量在回执缺失时未知。', '',
              '[预登记与方法](context-handoff-study-2026-10-06.md)、'
              '[分析和来源散列](../experiments/results/context-handoff01-analysis.json)、'
              '[冻结与真实接入检查](../experiments/results/context-handoff01-launch/manifest.json)。', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--registration', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--markdown-output', type=Path)
    args = parser.parse_args()
    result = analyze(json.loads(args.bundle.read_text(encoding='utf-8')),
                     json.loads(args.registration.read_text(encoding='utf-8')))
    result['source_sha256'] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in [args.bundle, args.registration, Path(__file__)]}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if args.markdown_output:
        args.markdown_output.write_text(markdown(result), encoding='utf-8')


if __name__ == '__main__':
    main()
