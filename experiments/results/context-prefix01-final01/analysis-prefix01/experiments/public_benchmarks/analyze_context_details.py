"""Supplementary cost and test-module accounting for a fixed context cohort.

This does not change preregistered scores or execute any model/checker. Test
module ownership describes upstream file names, not complete semantic coverage.
"""
from __future__ import annotations

import argparse
from collections import Counter
import datetime
import hashlib
import json
from pathlib import Path
import re


def elapsed(record):
    if not record or not record.get('started_at') or not record.get('finished_at'):
        return None
    start, end = (datetime.datetime.fromisoformat(record[k].replace('Z', '+00:00'))
                  for k in ('started_at', 'finished_at'))
    if start.tzinfo is None or end.tzinfo is None or end < start:
        raise ValueError('Invalid observed time interval')
    return (end - start).total_seconds()


def complete_sum(values):
    return sum(values) if values and all(v is not None for v in values) else None


def module_groups(checkpoint):
    current = int(checkpoint['checkpoint'].removeprefix('checkpoint_'))
    groups = {'prior_modules': Counter(), 'current_module': Counter(),
              'unmapped_modules': Counter(), 'future_modules': Counter()}
    for name, status in checkpoint.get('outcomes', {}).items():
        match = re.search(r'(?:^|/)test_checkpoint_(\d+)\.py::', name)
        if not match:
            group = 'unmapped_modules'
        else:
            number = int(match.group(1))
            group = 'prior_modules' if number < current else 'current_module' if number == current else 'future_modules'
        groups[group][status] += 1
    return {'checkpoint': checkpoint['checkpoint'], 'groups': {k: dict(v) for k, v in groups.items()},
            'all_collected_outcomes_accounted': checkpoint.get('all_collected_outcomes_accounted', False),
            'anonymous_skipped_summaries': checkpoint.get('anonymous_skipped_summaries', []),
            'scope': 'Upstream test-module number; not a semantic requirement classifier. Anonymous skips have no inferred ownership.'}


def usage(row, controls, ledger):
    unmeasured, charged = 0, 0
    role_seconds = Counter()
    for uid in ledger.get('children', []):
        child = controls[uid]
        count = child['charged_requests']
        charged += count
        report = child.get('report') or {}
        records = report.get('requests', [])
        if len(records) > count:
            raise ValueError('More usage records than reserved requests')
        unmeasured += count - len(records) + sum(any((r.get('usage') or {}).get(k) is None
                              for k in ('input_tokens', 'output_tokens')) for r in records)
        for episode in report.get('rounds', []):
            role_seconds[episode['role']] += episode['seconds']
    if charged != row['charged_physical_requests']:
        raise ValueError('Problem and child request counts differ')
    return {'requests': charged, 'requests_with_incomplete_token_usage': unmeasured,
            'input_tokens': row['observed_input_tokens'] if not unmeasured else None,
            'output_tokens': row['observed_output_tokens'] if not unmeasured else None,
            'observed_input_tokens': row['observed_input_tokens'],
            'observed_output_tokens': row['observed_output_tokens'],
            'role_episode_seconds': dict(role_seconds)}


def analyze(bundle, receipt, regressions, registration):
    expected = [(r['task_id'], r['arm']) for r in registration['planned_order']]
    sources = []
    for document in (bundle, receipt, regressions):
        entries = {(r['task_id'], r['arm']): r for r in document['rows']}
        if set(entries) != set(expected) or len(entries) != len(document['rows']):
            raise ValueError('Missing, duplicate or unregistered cells')
        sources.append(entries)
    rows = []
    for key in expected:
        row, execution, regression = (source[key] for source in sources)
        item = {'task_id': key[0], 'arm': key[1], 'status': row['status']}
        rows.append(item)
        if not row.get('result_sha256'):
            continue
        if row['result_sha256'] != regression.get('result_sha256'):
            raise ValueError('Outcome and test logs belong to different results')
        ledger = bundle['problem_ledgers'][row['problem_id']]
        measured = usage(row, bundle['child_controls'], ledger)
        wall = execution.get('finished_epoch', 0) - execution.get('started_epoch', 0) \
            if execution.get('finished_epoch') is not None and execution.get('started_epoch') is not None else None
        if wall is not None and wall < 0:
            raise ValueError('Negative trial elapsed time')
        measured.update(trial_wall_seconds=wall,
                        solver_and_online_review_seconds=complete_sum([elapsed(s.get('agent_execution')) for s in row['steps']]),
                        official_grading_seconds=complete_sum([elapsed(s.get('verifier')) for s in row['steps']]))
        item.update(usage=measured, modules=[module_groups(c) for c in regression['checkpoints']],
                    regressions=[{'from_checkpoint': t['from_checkpoint'], 'to_checkpoint': t['to_checkpoint'],
                                  'status': t['status'], 'identity_scope': t.get('identity_scope'),
                                  'pass_to_fail_count': len(t['pass_to_fail']) if 'pass_to_fail' in t else None,
                                  'pass_to_error_count': len(t['pass_to_error']) if 'pass_to_error' in t else None,
                                  'fail_to_pass_count': len(t['fail_to_pass']) if 'fail_to_pass' in t else None}
                                 for t in regression['transitions']])
    return {'experiment': registration['experiment'], 'collected_at': bundle['collected_at'], 'rows': rows,
            'new_model_calls': 0, 'scores_unchanged': True,
            'scope': 'Supplementary descriptive accounting. Wall time includes setup, grading and cleanup after dispatch, '
                     'not admission queueing. Shared-host concurrency affects time. Missing token usage is not zero. '
                     'Test module grouping and same-ID regressions do not prove complete requirement coverage.'}


def markdown(result):
    def show(value, scale=1):
        return '—' if value is None else f'{value / scale:,.2f}'

    lines = ['# 上下文消融：用量、耗时与旧功能回归', '',
             f"固定观测：`{result['collected_at']}`。以下不改变原分数，不增加模型调用。", '',
             '| 问题 | 条件 | 请求 | 输入 / 输出 tokens | 求解及在线审查 / 官方评分（分钟） | 派发至结束（分钟） |',
             '|---|---|---:|---:|---:|---:|']
    for row in result['rows']:
        u = row.get('usage', {})
        lines.append(f"| {row['task_id']} | {row['arm']} | {u.get('requests', '—')} | "
                     f"{show(u.get('input_tokens'))} / {show(u.get('output_tokens'))} | "
                     f"{show(u.get('solver_and_online_review_seconds'), 60)} / {show(u.get('official_grading_seconds'), 60)} | "
                     f"{show(u.get('trial_wall_seconds'), 60)} |")
    lines += ['', 'token 回执不完整时总量留空，JSON 中另外保留观测小计。派发至结束包括环境、评分、清理等开销，'
              '不含等待资源派发的时间；多个任务共享主机，时间差不是纯模型速度差。', '',
              '| 问题 | 条件 | 阶段转换 | 同名旧测试通过→失败 | 通过→错误 | 失败→通过 |',
              '|---|---|---|---:|---:|---:|']
    for row in result['rows']:
        for transition in row.get('regressions', []):
            lines.append(f"| {row['task_id']} | {row['arm']} | {transition['from_checkpoint']} → {transition['to_checkpoint']} | "
                         + ' | '.join(str(transition[k]) if transition[k] is not None else '未知'
                                      for k in ('pass_to_fail_count', 'pass_to_error_count', 'fail_to_pass_count')) + ' |')
    lines += ['', '下表只看各问题最后阶段，按上游测试模块文件名区分本阶段与先前阶段。'
              '模块归属不是完整语义需求映射；匿名跳过项不猜测归属。同名测试回归也受阶段环境变化影响，不能单独归因于产物改动。', '',
              '| 问题 | 条件 | 最后阶段 | 先前模块通过 / 已报告 | 本阶段模块通过 / 已报告 |',
              '|---|---|---|---|---|']
    for row in result['rows']:
        if not row.get('modules'):
            continue
        last = row['modules'][-1]
        parts = []
        for group in ('prior_modules', 'current_module'):
            counts = last['groups'][group]
            parts.append(f"{counts.get('PASSED', 0)} / {sum(counts.values())}")
        lines.append(f"| {row['task_id']} | {row['arm']} | {last['checkpoint']} | " + ' | '.join(parts) + ' |')
    lines += ['', '[主要结果及干预核对](context-handoff01-progress.md)、'
              '[方法](context-handoff-study-2026-10-06.md)、'
              '[补充分析和来源散列](../experiments/results/context-handoff01-details.json)。', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser()
    for name in ('bundle', 'receipt', 'regressions', 'registration', 'output', 'markdown_output'):
        parser.add_argument('--' + name.replace('_', '-'), type=Path, required=True)
    args = parser.parse_args()
    inputs = [args.bundle, args.receipt, args.regressions, args.registration]
    result = analyze(*(json.loads(p.read_text(encoding='utf-8')) for p in inputs))
    result['source_sha256'] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in [*inputs, Path(__file__)]}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    args.markdown_output.write_text(markdown(result), encoding='utf-8')


if __name__ == '__main__':
    main()
