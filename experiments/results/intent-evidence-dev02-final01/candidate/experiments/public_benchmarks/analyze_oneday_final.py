"""Combine original outcomes with the one eligible parser-repair audit explicitly."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics


def analyze(original, supplementary, registration):
    if not original['status'].startswith('complete') or not supplementary['status'].startswith('complete'):
        raise ValueError('An input is still unfinished')
    expected = {(r['task_id'], r['arm']) for r in registration['planned_order']}
    rows = original['rows']
    actual = [(r['task_id'], r['arm']) for r in rows]
    if set(actual) != expected or len(actual) != len(expected):
        raise ValueError('Missing, duplicate or unexpected original cells')
    if len({r['index'] for r in rows}) != len(rows):
        raise ValueError('Duplicate original index')
    supplement = {r['index']: r for r in supplementary['rows']}
    if len(supplement) != len(supplementary['rows']) or set(supplement) != {9, 10, 11}:
        raise ValueError('Unexpected parser-repair audit cells')
    combined = []
    for row in rows:
        original_grade = row.get('final_judge')
        extra = supplement.get(row['index'])
        if extra:
            manifest = f"oneday-artifacts/oneday-oneday-transfer01-{row['index']:02d}/artifact-manifest.json"
            if (row['task_id'] != 'taskif_94' or row['status'] != 'requires_audit'
                    or original_grade is not None or row['grade_validity'] != 'not_graded'
                    or row['receipt_files'].get(manifest) != extra['artifact_manifest_sha256']
                    or extra['status'] != 'adapted_judge_graded'):
                raise ValueError('Supplementary grade cannot be matched to an eligible unchanged artifact')
        elif row['grade_validity'] != 'adapted_judge_graded':
            raise ValueError('Unresolved original grade')
        grade = extra or original_grade
        if grade.get('judge_model') != 'devin/swe-2' or grade.get('judge_saw_arm') is not False:
            raise ValueError('Unexpected judge condition')
        if not (0 <= grade['score'] <= grade['max_score'] and grade['max_score'] > 0):
            raise ValueError('Invalid score')
        combined.append({'task_id': row['task_id'], 'arm': row['arm'], 'index': row['index'],
                         'original_status': row['status'], 'original_grade': original_grade,
                         'supplementary_grade': extra, 'score_source': 'supplementary' if extra else 'original',
                         'score_fraction': grade['score'] / grade['max_score'],
                         'solver_and_online_review_requests': row['requests']})
    by_arm = {}
    for arm in registration['arms']:
        selected = [r for r in combined if r['arm'] == arm]
        initial = [r for r in selected if r['score_source'] == 'original']
        by_arm[arm] = {'original_graded_tasks': len(initial),
                       'original_graded_task_mean': statistics.mean(r['score_fraction'] for r in initial),
                       'with_supplementary_tasks': len(selected),
                       'task_mean_with_supplementary': statistics.mean(r['score_fraction'] for r in selected),
                       'solver_and_online_review_requests': sum(r['solver_and_online_review_requests'] for r in selected)}
    return {'experiment': 'oneday-transfer01', 'status': 'complete_with_disclosed_parser_repair',
            'statistical_unit': 'task ID, not individual criterion or arm',
            'distinct_task_ids': len({r['task_id'] for r in rows}),
            'score_sources': dict(Counter(r['score_source'] for r in combined)),
            'official_gemini_reproduction': False, 'raw_scores_unchanged': True, 'new_model_calls': 0,
            'request_scope': 'Solver plus online review; excludes final artifact-grader calls.',
            'score_scope': 'Descriptive mean of per-task rubric fractions, not all-correct task rate.',
            'by_arm': by_arm, 'rows': combined}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--original', type=Path, required=True)
    parser.add_argument('--supplementary', type=Path, required=True)
    parser.add_argument('--registration', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    paths = {k: getattr(args, k) for k in ['original', 'supplementary', 'registration']}
    result = analyze(*(json.loads(paths[k].read_text(encoding='utf-8')) for k in paths))
    result['source_files'] = {k: {'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                              for k, p in paths.items()}
    result['analysis_source_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps(result['by_arm']))


if __name__ == '__main__':
    main()
