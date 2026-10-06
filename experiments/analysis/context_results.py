"""Keep both context studies, including the invalid-checker counterexample."""
import hashlib
import json
from pathlib import Path

from registered_results import measured_tokens

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT/'experiments/results'


def main():
    studies = {}
    lines = ['# Fresh-session context-policy studies', '',
             'Study 01 is retained in full. Its seed 8602 has an incorrect public checker',
             'and cannot support an unconfounded efficacy comparison. Study 02 uses new',
             'fixtures after a recorded correction and known-correct-output preflight.', '']
    for tag, script in (('context-reset01', 'context_reset.py'), ('context-reset02', 'context_reset02.py')):
        registration_path = RESULTS/(tag+'-registration.json')
        result_path = RESULTS/(tag+'-results.json')
        registration = json.loads(registration_path.read_text(encoding='utf-8'))
        rows = json.loads(result_path.read_text(encoding='utf-8'))
        expected = [f'{tag}-{seed}-{policy}' for seed, policy in registration['plan']]
        errors = []
        if [r['run_id'] for r in rows] != expected:
            errors.append('Missing, duplicated, unregistered or reordered attempts')
        actual_source = hashlib.sha256((Path(__file__).parent/script).read_bytes()).hexdigest()
        if registration['script_sha256'] != actual_source:
            errors.append('Driver no longer matches registration')
        lines += [f'## {tag}', '',
                  '| Fixture | Policy | All phases correct and stopped | Final artifact correct | Runtime status | Episodes | Requests |',
                  '|---|---|---:|---:|---|---:|---:|']
        for row in rows:
            if row['started'] < registration['registered']:
                errors.append('Run predates registration: '+row['run_id'])
            if row['task_sha256'] != registration['fixture_sha256'][str(row['seed'])]:
                errors.append('Fixture mismatch: '+row['run_id'])
            if row['executor_calls'] > 48 or len(row['requests']) > 60 or len(row['episodes']) > 6:
                errors.append('Budget exceeded: '+row['run_id'])
            if not all(e['history_reset'] and e['fresh_hermes_home'] for e in row['episodes']):
                errors.append('Missing reset exposure: '+row['run_id'])
            if any(r['request_model'] != 'devin/swe-2' for r in row['requests']):
                errors.append('Model mismatch: '+row['run_id'])
            invalid = tag == 'context-reset01' and row['seed'] == 8602
            label = str(row['seed'])+(' (invalid checker)' if invalid else '')
            lines.append(f'| {label} | {row["policy"]} | {row["passed"]} | {row["final_artifact_pass"]} | '
                         f'{row["status"]} | {len(row["episodes"])} | {len(row["requests"])} |')
        policies = {}
        for policy in ('minimal_continue', 'original_goal', 'durable_state'):
            selected = [r for r in rows if r['policy'] == policy]
            policies[policy] = {'runs': len(selected), 'registered_successes': sum(r['passed'] for r in selected),
                                'final_artifact_passes': sum(r['final_artifact_pass'] for r in selected),
                                'physical_requests': sum(len(r['requests']) for r in selected),
                                'continuation_episodes': sum(max(0, len(r['episodes'])-1) for r in selected),
                                'continuation_prompt_bytes': sum(e['prompt_bytes'] for r in selected for e in r['episodes'][1:]),
                                'tokens': measured_tokens(selected)}
        studies[tag] = {'complete': not errors, 'audit_errors': errors, 'runs': len(rows),
                        'policies': policies, 'invalid_efficacy_fixture_seeds': [8602] if tag == 'context-reset01' else [],
                        'input_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                         for p in (registration_path, result_path)}}
        note = ('Descriptive totals include the invalid fixture; do not infer policy efficacy.'
                if tag == 'context-reset01' else 'Descriptive totals over two new related fixtures:')
        lines += ['', note, '',
                  '| Policy | Physical requests | Known total tokens | Missing usage records |',
                  '|---|---:|---:|---:|']
        for policy, value in policies.items():
            lines.append(f'| {policy} | {value["physical_requests"]} | '
                         f'{value["tokens"].get("total_tokens", 0):,} | {value["tokens"].get("usage_missing", 0)} |')
        lines += ['', 'No superiority/equivalence claim: few related fixtures, a single model alias,',
                  'one sample per condition, shared host/provider, and no long-duration reasoning.', '']
    result = {'kind': 'supplementary_context_policy_analysis', 'studies': studies,
              'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (RESULTS/'context-analysis.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    (RESULTS/'context-analysis.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps({tag: {'complete': s['complete'], 'audit_errors': s['audit_errors']} for tag,s in studies.items()}))
    if any(not s['complete'] for s in studies.values()):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
