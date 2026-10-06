"""Describe the registered public development sample without inflating its size."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import random


def paired_statistics(counts):
    wins = counts.get('native_0_sg_1', 0)
    losses = counts.get('native_1_sg_0', 0)
    ties = counts.get('native_0_sg_0', 0) + counts.get('native_1_sg_1', 0)
    n, discordant = wins + losses + ties, wins + losses
    if not n:
        return {'paired_tasks': 0}
    p = min(1.0, 2 * sum(math.comb(discordant, k) for k in range(min(wins, losses)+1)) / 2**discordant) if discordant else 1.0
    differences = [1] * wins + [-1] * losses + [0] * ties
    rng = random.Random(20261006)
    samples = sorted(sum(rng.choices(differences, k=n))/n for _ in range(10000))
    return {'paired_tasks': n, 'sg_only_success': wins, 'native_only_success': losses,
            'success_rate_difference': (wins-losses)/n, 'mcnemar_exact_two_sided_p': p,
            'task_bootstrap_percentile_95': [samples[249], samples[9749]],
            'bootstrap_resamples': 10000, 'bootstrap_seed': 20261006,
            'bootstrap_degenerate': samples[249] == samples[9749],
            'scope': 'Conditional on available paired, deliberately selected task IDs. Not a population-generalization guarantee; resampling calls no model.'}


def analyze(bundle, registration):
    label = registration["experiment"]
    arms = registration["arms"]
    tasks = registration["tasks"]
    registered_tasks = {task["task_id"] for task in tasks}
    selected = {}
    errors = []
    for row in bundle["trials"]:
        result = row["result"]
        metadata = (result.get("agent_result") or {}).get("metadata") or {}
        if metadata.get("adapter") != label and not row["job_name"].startswith(f"tb-{label}-"):
            continue
        arm = metadata.get("study_arm") or (((result.get("config") or {}).get("agent") or {}).get("kwargs") or {}).get("arm")
        if arm not in arms:
            raise ValueError("Registered trial has no identifiable comparison arm")
        task_id = result["task_name"].removeprefix("terminal-bench/")
        if task_id not in registered_tasks:
            raise ValueError(f"Unregistered task identity: {result['task_name']}")
        key = (task_id, arm)
        if key in selected:
            raise ValueError(f"Duplicate registered task/arm; do not pick the best: {key}")
        control_id = metadata.get("control_id")
        report = (bundle["model_runs"].get(control_id) or {}).get("report") or {}
        adjudication = bundle.get("adjudications", {}).get(control_id)
        grade = ((result.get("verifier_result") or {}).get("rewards") or {}).get("reward")
        acceptance = (report.get("last_acceptance") or {}).get("verdict")
        valid = row["classification"] == "graded"
        # Controller/worker errors belong to the system being evaluated. Keep
        # official reward untouched, but include these failures in end-to-end
        # completion instead of conditioning every comparison on successful runs.
        exception_type = (result.get("exception_info") or {}).get("exception_type")
        execution_limit_reached = exception_type == "AgentTimeoutError"
        records = report.get('requests', [])
        charged = report.get('used_requests')
        unmeasured = sum(any((r.get('usage') or {}).get(k) is None
                            for k in ['input_tokens', 'output_tokens']) for r in records)
        if isinstance(charged, int):
            unmeasured += max(0, charged - len(records))
        usage_complete = isinstance(charged, int) and unmeasured == 0
        agent_usage = result.get('agent_result') or {}
        # A registered solver deadline is a capability/budget outcome, not by
        # itself evidence of a broken controller. Both still count end-to-end.
        harness_failure = (report.get("status") == "adapter_error" and not execution_limit_reached) or (
            adjudication is not None and adjudication.get("kind") == "harness_snapshot_handoff_failure")
        end_to_end = grade if valid else 0 if harness_failure or execution_limit_reached else None
        summary = {"task_id": key[0], "upstream_task_name": result["task_name"],
                   "arm": key[1], "classification": row["classification"],
                   "official_raw_reward": grade, "valid_for_comparison": valid,
                   "end_to_end_reward": end_to_end,
                   "harness_failure": harness_failure, "adjudication": adjudication,
                   "execution_limit_reached": execution_limit_reached, "exception_type": exception_type,
                   "requests": report.get("used_requests"), "status": report.get("status"),
                   "upstream_http_errors": dict(Counter(str(r['http_status']) for r in report.get('requests', [])
                       if isinstance(r.get('http_status'), int) and r['http_status'] >= 400)),
                   "transport_error_calls": sum(bool(r.get('error')) for r in report.get('requests', [])),
                   "input_tokens": agent_usage.get('n_input_tokens') if usage_complete else None,
                   "output_tokens": agent_usage.get('n_output_tokens') if usage_complete else None,
                   "cache_tokens": agent_usage.get('n_cache_tokens') if usage_complete else None,
                   "observed_input_tokens": agent_usage.get('n_input_tokens'),
                   "observed_output_tokens": agent_usage.get('n_output_tokens'),
                   "token_usage_complete": usage_complete,
                   "requests_with_unmeasured_token_usage": unmeasured if isinstance(charged, int) else None,
                   "executor_episodes": sum(r["role"] == "executor" for r in report.get("rounds", [])),
                   "review_episodes": sum(r["role"] == "review" for r in report.get("rounds", [])),
                   "role_episode_counts": dict(Counter(r['role'] for r in report.get('rounds', []))),
                   "upstream_role_episode_counts": dict(Counter(r['longhorizon_role'] for r in report.get('rounds', [])
                                                                if r.get('longhorizon_role'))),
                   "history_reset_exercised": arm in {"sg_fresh", "sg_fresh_no_context"}
                       and sum(r["role"] == "executor" for r in report.get("rounds", [])) > 1,
                   "bounded_reaudit_exercised": arm == "sg_retry_unknown" and any(
                       a["role"] == b["role"] == "review"
                       for a, b in zip(report.get("rounds", []), report.get("rounds", [])[1:])),
                   "review_verdicts": [r.get('verdict') for r in report.get('review_attempts', [])],
                   "episode_seconds": sum(r["seconds"] for r in report.get("rounds", [])),
                   "last_online_verdict": acceptance, "control_id": control_id,
                   "false_online_acceptance": valid and acceptance == "pass" and grade == 0,
                   # An image identifier can outlive its Docker image. Older
                   # exports contain no availability observation; retain that
                   # uncertainty instead of turning a reference into a backup.
                   "artifact_snapshot_recorded": bool(report.get("artifact_image_id")),
                   "artifact_snapshot_available": (bundle.get("checkpoint_observations", {}).get(
                       report.get("artifact_image_id"), {}).get("image_available")),
                   "artifact_retention_receipt": (bundle.get("checkpoint_observations", {}).get(
                       report.get("artifact_image_id"), {}).get("retention_status", "unobserved"))}
        selected[key] = summary
        if not valid:
            errors.append(summary)
    paired = Counter()
    paired_end_to_end = Counter()
    for task in tasks:
        native, full = selected.get((task["task_id"], "native")), selected.get((task["task_id"], "sg_v2"))
        if native and full and native["valid_for_comparison"] and full["valid_for_comparison"]:
            paired[f"native_{native['official_raw_reward']:g}_sg_{full['official_raw_reward']:g}"] += 1
        if (native and full and native["end_to_end_reward"] is not None
                and full["end_to_end_reward"] is not None):
            paired_end_to_end[f"native_{native['end_to_end_reward']:g}_sg_{full['end_to_end_reward']:g}"] += 1
    return {"experiment": label, "collected_at": bundle["collected_at"],
            "host": bundle.get("host", "grok-bot"),
            "planned_tasks": len(tasks), "planned_arms": len(arms), "planned_trials": len(registration["planned_order"]),
            "observed_trials": len(selected), "valid_trials": sum(r["valid_for_comparison"] for r in selected.values()),
            "raw_trial_count_including_oracles_and_prior_candidates": len(bundle["trials"]),
            "paired_native_full": dict(paired), "apparatus_or_unresolved": errors,
            "paired_native_full_end_to_end": dict(paired_end_to_end),
            "paired_statistics_official": paired_statistics(paired),
            "paired_statistics_end_to_end": paired_statistics(paired_end_to_end),
            "descriptive_pairs_to_sg": paired_arm_comparisons(list(selected.values()), arms),
            "end_to_end_by_arm": {arm: {
                "observed_evaluable_trials": sum(r["arm"] == arm and r["end_to_end_reward"] is not None for r in selected.values()),
                "successes": sum(r["arm"] == arm and r["end_to_end_reward"] == 1 for r in selected.values()),
                "controller_failures": sum(r["arm"] == arm and r["harness_failure"] for r in selected.values()),
                "execution_deadlines": sum(r["arm"] == arm and r["execution_limit_reached"] for r in selected.values()),
            } for arm in arms},
            "mechanism_contrasts": mechanism_contrasts(list(selected.values()), registration.get('contrasts', [])),
            "mechanism_activation_by_arm": {arm: {
                "observed_trials": sum(r["arm"] == arm for r in selected.values()),
                "second_executor_episode": sum(r["arm"] == arm and r["executor_episodes"] > 1 for r in selected.values()),
                "history_reset_exercised": sum(r["arm"] == arm and r["history_reset_exercised"] for r in selected.values()),
                "bounded_reaudit_exercised": sum(r["arm"] == arm and r["bounded_reaudit_exercised"] for r in selected.values()),
                "false_online_acceptances": sum(r["arm"] == arm and r["false_online_acceptance"] for r in selected.values()),
            } for arm in arms},
            "rows": list(selected.values())}


def paired_arm_comparisons(rows, arms):
    """Compare SG with every registered arm on the same observed task IDs.

    These supplementary comparisons are descriptive, not newly preregistered
    hypotheses. Missing outcomes and missing usage remain explicit.
    """
    if 'sg_v2' not in arms:
        return []
    lookup = {(r['task_id'], r['arm']): r for r in rows}
    output = []
    for other in arms:
        if other == 'sg_v2':
            continue
        pairs = []
        for task in sorted({r['task_id'] for r in rows}):
            a, b = lookup.get((task, other)), lookup.get((task, 'sg_v2'))
            if not a or not b or a['end_to_end_reward'] is None or b['end_to_end_reward'] is None:
                continue
            pairs.append((a, b))
        usage = {}
        for metric in ['requests', 'input_tokens', 'output_tokens']:
            measured = [(a[metric], b[metric]) for a, b in pairs
                        if a.get(metric) is not None and b.get(metric) is not None]
            usage[metric] = {
                'paired_tasks': len(measured),
                'other_total': sum(a for a, _ in measured) if measured else None,
                'sg_total': sum(b for _, b in measured) if measured else None,
            }
        output.append({
            'other_arm': other, 'sg_arm': 'sg_v2',
            'paired_tasks': len(pairs), 'task_ids': [a['task_id'] for a, _ in pairs],
            'sg_only_success': sum(b['end_to_end_reward'] == 1 and a['end_to_end_reward'] != 1 for a, b in pairs),
            'other_only_success': sum(a['end_to_end_reward'] == 1 and b['end_to_end_reward'] != 1 for a, b in pairs),
            'mean_reward_difference_sg_minus_other': sum(
                b['end_to_end_reward'] - a['end_to_end_reward'] for a, b in pairs) / len(pairs) if pairs else None,
            'paired_usage': usage,
            'interpretation': 'Supplementary descriptive comparison on available paired tasks; no unpaired scores or usage, no multiplicity-adjusted inference.',
        })
    return output


def mechanism_contrasts(rows, contrasts):
    """Task-paired descriptive effects; never treat calls as sample units."""
    lookup = {(r['task_id'], r['arm']): r for r in rows}
    output = []
    for contrast in contrasts:
        control, treatment = contrast['control'], contrast['treatment']
        pairs = []
        for task in sorted({r['task_id'] for r in rows}):
            a, b = lookup.get((task, control)), lookup.get((task, treatment))
            if not a or not b or a['end_to_end_reward'] is None or b['end_to_end_reward'] is None:
                continue
            pairs.append({'task_id': task, 'reward_difference': b['end_to_end_reward']-a['end_to_end_reward'],
                          'request_difference': b['requests']-a['requests'] if a['requests'] is not None and b['requests'] is not None else None,
                          'second_episode_observed': max(a['executor_episodes'], b['executor_episodes']) > 1,
                          'control_second_episode_observed': a['executor_episodes'] > 1,
                          'treatment_second_episode_observed': b['executor_episodes'] > 1,
                          'treatment_history_reset_exercised': b.get('history_reset_exercised', False),
                          'treatment_bounded_reaudit_exercised': b.get('bounded_reaudit_exercised', False),
                          'unknown_audit_observed': 'unknown' in a.get('review_verdicts', []) or 'unknown' in b.get('review_verdicts', [])})
        output.append({**contrast, 'paired_tasks': len(pairs), 'pairs': pairs,
                       'mean_reward_difference': sum(p['reward_difference'] for p in pairs)/len(pairs) if pairs else None,
                       'interpretation': 'Exploratory, deliberately selected task units; activation subsets are descriptive and post-treatment.'})
    return output


def markdown(summary, registration):
    lookup = {(r["task_id"], r["arm"]): r for r in summary["rows"]}
    lines = [f"# Terminal-Bench 2.1 公开任务试验：{summary['experiment']}", "",
             f"收据截至 {summary['collected_at']}。已返回 {summary['observed_trials']}/{summary['planned_trials']} 个登记试次，"
             f"其中 {summary['valid_trials']} 个官方流程无异常。这是已登记子集，不是完整 Terminal-Bench 排行榜成绩。", "",
             f"执行器：{registration.get('executor_description', '真实 Hermes 0.21.3 + devin/swe-2')}。{len(registration['tasks'])} 个不同公开 task ID、"
             f"{len(registration['arms'])} 组，每组合一次；没有增加随机种子。全部审查、续跑和重试共享 "
             f"{registration.get('max_upstream_requests_per_trial')} 次上游请求及官方任务时间上限。"
             f"并发配置：`{json.dumps(registration.get('concurrency'), ensure_ascii=False)}`；保留各任务官方 CPU / 内存限额。"
             "容器内 Debian APT 改 HTTPS，Harbor 0.24.0，宿主 Python 3.13.5，环境差异已披露。", "",
             "## 官方结果", "", "未运行或尚无结果记为 —；评分装置问题记为待审计。执行超时与已确认的控制器失败均计入端到端失败，但超时本身不是控制器缺陷的证据。", "",
             "| 任务 | " + " | ".join(registration['arms']) + " |",
             "|---|" + "---:|" * len(registration['arms'])]
    for task in registration["tasks"]:
        cells = []
        for arm in registration["arms"]:
            row = lookup.get((task["task_id"], arm))
            if row is None:
                cells.append("—")
            elif row["valid_for_comparison"]:
                cells.append(str(int(row["official_raw_reward"])))
            elif row["execution_limit_reached"]:
                raw = row["official_raw_reward"]
                cells.append((str(int(raw)) if raw is not None else "无评分") + "（执行超时）")
            else:
                cells.append("待审计")
        lines.append("| " + " | ".join([task["task_id"], *cells]) + " |")
    lines += ["", "## 机制与实测用量", "",
              "| 任务 / 分组 | 执行轮次 | 审查轮次 | 实际请求 | 输入 / 输出 tokens | 在线终判 |",
              "|---|---:|---:|---:|---:|---|"]
    for task in registration["tasks"]:
        for arm in registration["arms"]:
            row = lookup.get((task["task_id"], arm))
            if row:
                lines.append(f"| {row['task_id']} / {arm} | {row['executor_episodes']} | {row['review_episodes']} | "
                             f"{row['requests']} | {row['input_tokens']} / {row['output_tokens']} | {row['last_online_verdict'] or '无'} |")
    false_accepts = sum(row["false_online_acceptance"] for row in summary["rows"])
    continued = sum(row["executor_episodes"] > 1 for row in summary["rows"])
    lines += ["", f"已观察到 {continued} 个试次执行超过一轮；{false_accepts} 个试次的在线 pass 与官方失败冲突。"
              "这两个计数描述已返回的数据，不能单独作为增益或可靠性证明。", "",
              "原生与完整 SG 的完整有效配对：`" + json.dumps(summary["paired_native_full"], ensure_ascii=False) + "`。"
              "以不同 task ID 为单位，不把模型调用数、审查次数或相同任务的不同组当成独立任务样本。", "",
              "端到端配对（包含控制器失败）：`" + json.dumps(summary["paired_native_full_end_to_end"], ensure_ascii=False) + "`。",
              "按 task ID 配对的统计：`" + json.dumps(summary['paired_statistics_end_to_end'], ensure_ascii=False) + "`。"
              "小样本或相同差值可能产生退化的 bootstrap 区间；它不是通用能力的置信保证。",
              "各组端到端统计：`" + json.dumps(summary["end_to_end_by_arm"], ensure_ascii=False) + "`。"
              "分母仅含已有可判定结果；待审计的评分装置问题与未启动试次单列。", "",
              "## 解释边界", "",
              "- 官方参考解只验证环境和评分器，不属于任何模型组的成功。旧候选、装置失败和原始零分全部保留。",
              "- 题目由项目选择，公开题目存在训练污染可能；结果不能单独证明通用性或跨天自治。开发与冻结后任务的划分见登记。",
              "- 在线审查器尚未完成独立校准。只读文件系统快照不保留服务进程，可能无法判断运行状态。",
              "- 工具适配器已发现的接入缺陷、修复验证及冻结候选范围见[研究报告](research-findings-2026-10-06.md)。"
              "原成绩保留；不能把包含接入差异的比较解释为纯 harness 因果效应。",
              "- 此入口使用实际 v2 内核，但不等于生产网关插件端到端研究；生产默认引擎未切换。",
              "- 各组请求和时间上限相同，实际 token 用量不同；总输入 token 未设硬上限。",
              "- 物理请求有记账但缺少 token 回执时，该任务的完整 token 总量记为未知；原始观测小计另存。"
              "token 用量的配对比较只包含双方回执完整的任务，不把服务失败后的观测零值当成真实零消耗。",
              f"- 模型轨迹和官方日志位于 {summary['host']}；快照 ID 不代表镜像仍存在。"
              "快照可用性与独立存档收据单列，缺少实测记录时为未知；公开导出提供散列和必要评分日志。", "",
              f"原始数据：[结果与评分日志](../experiments/results/{summary.get('bundle_file', 'public-benchmarks-2026-10-05.json')})。"
              f"方法：[候选协议]({registration.get('method_document', '../experiments/public_benchmarks/PUBLIC-' + registration['experiment'].removeprefix('public-').upper() + '.md')})、"
              f"[预登记](../experiments/public_benchmarks/registration-{registration['experiment']}.json)。", ""]
    affected = [row for row in summary['rows'] if row['upstream_http_errors'] or row['status'] == 'worker_incomplete']
    orchestration = [row for row in summary['rows'] if row['upstream_role_episode_counts']]
    if summary.get('descriptive_pairs_to_sg'):
        lines += ['## 补充任务配对比较', '',
                  '各行只使用两组都有端到端结果的相同 task ID；不同对照行的任务集合可能不同。待运行和评分装置未解决的单元没有填零。'
                  '用量只汇总这些结果配对中两侧都有该指标的任务，缺失用量不填零。这是事后补充的描述性比较，不是新增预登记检验或总体排名。', '',
                  '| 对照组 | 配对任务 | 仅 SG 全过 | 仅对照全过 | 平均分差（SG − 对照） | 配对请求合计（SG / 对照；任务数） |',
                  '|---|---:|---:|---:|---:|---|']
        for contrast in summary['descriptive_pairs_to_sg']:
            usage = contrast['paired_usage']['requests']
            difference = contrast['mean_reward_difference_sg_minus_other']
            score = '—' if difference is None else f'{difference:+.4f}'
            requests = '—' if not usage['paired_tasks'] else f"{usage['sg_total']} / {usage['other_total']}；{usage['paired_tasks']}"
            lines.append(f"| {contrast['other_arm']} | {contrast['paired_tasks']} | {contrast['sg_only_success']} | "
                         f"{contrast['other_only_success']} | {score} | {requests} |")
        lines.append('')
    if orchestration:
        lines += ['## 上游角色调用', '',
                  '管理、格式修复与最终答复也计入总请求及时间，不能仅按执行/审查轮数估计开销。以下是角色 episode 数，不是物理请求数。', '',
                  '| 任务 / 组 | 上游角色 episode 计数 |', '|---|---|']
        for row in orchestration:
            lines.append(f"| {row['task_id']} / {row['arm']} | {json.dumps(row['upstream_role_episode_counts'])} |")
        lines.append('')
    if affected:
        lines += ['## 服务与执行完整性附注', '',
                  '以下原始分数保持不变。worker_incomplete 表示 SDK 未完成，不能仅凭官方零分把原因归为求解能力；HTTP 错误可能已被重试恢复，需要结合终态和轨迹解释。', '',
                  '| 任务 / 组 | 控制器状态 | 已记录上游 HTTP 错误 |', '|---|---|---|']
        for row in affected:
            lines.append(f"| {row['task_id']} / {row['arm']} | {row['status']} | "
                         f"{json.dumps(row['upstream_http_errors'])} |")
        lines.append('')
    if summary.get('mechanism_contrasts'):
        lines += ['## 已登记机制对照（开发样本，仅描述性）', '',
                  '历史清空只有在对应清空组实际进入第二轮执行时才生效。对照组发生续跑，不代表清空组的干预也已触发；首轮结果差异不能归因于历史清空。unknown 重审也必须实际出现第二次审查。', '',
                  '| 组 | 已返回 | 第二轮执行 | 历史清空实际触发 | 有限重审实际触发 | 错误在线 pass |',
                  '|---|---:|---:|---:|---:|---:|']
        for arm, observed in summary['mechanism_activation_by_arm'].items():
            lines.append(f"| {arm} | {observed['observed_trials']} | {observed['second_executor_episode']} | "
                         f"{observed['history_reset_exercised']} | {observed['bounded_reaudit_exercised']} | "
                         f"{observed['false_online_acceptances']} |")
        lines += ['',
                  '```json', json.dumps(summary['mechanism_contrasts'], indent=2, ensure_ascii=False), '```', '']
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args()
    registration = json.loads(args.registration.read_text(encoding="utf-8"))
    summary = analyze(json.loads(args.bundle.read_text(encoding="utf-8")), registration)
    summary["bundle_file"] = args.bundle.name
    args.json_output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    args.markdown_output.write_text(markdown(summary, registration), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k not in {"rows", "apparatus_or_unresolved"}}))


if __name__ == "__main__":
    main()
