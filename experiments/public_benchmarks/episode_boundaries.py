"""Read-only diagnosis of actual SDK boundaries, not semantic task success."""
from __future__ import annotations

import argparse
from collections import Counter
import datetime
import hashlib
import json
from pathlib import Path
import re


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def boundary(result):
    """Only classify an iteration limit when the SDK's explicit flags agree.

    This diagnostic does not authorize replay, infer success, or change scores.
    Missing fields are unknown; model final-response text is never an input.
    """
    flags = {k: result.get(k) if type(result.get(k)) is bool else None
             for k in ['completed', 'failed', 'partial', 'interrupted']}
    calls = result.get('api_calls')
    calls = calls if type(calls) is int and calls >= 0 else None
    reason = result.get('turn_exit_reason')
    reason = reason if isinstance(reason, str) else None
    limit = re.fullmatch(r'max_iterations_reached\((\d+)/(\d+)\)', reason or '')
    error = bool(result.get('error'))
    if flags['interrupted'] is True:
        kind = 'interrupted'
    elif flags['failed'] is True:
        kind = 'sdk_failed'
    elif flags['partial'] is True or error:
        kind = 'partial_or_error'
    elif flags['completed'] is True:
        kind = 'sdk_completed'
    elif (flags['completed'] is False and all(flags[k] is False for k in ['failed', 'partial', 'interrupted'])
          and limit and calls is not None and int(limit[2]) > 0 and calls == int(limit[1]) >= int(limit[2])):
        kind = 'iteration_limit'
    elif flags['completed'] is False:
        kind = 'sdk_incomplete_unclassified'
    else:
        kind = 'no_sdk_completion_flags'
    return {'kind': kind, **flags, 'sdk_api_calls': calls,
            'turn_exit_reason': reason[:512] if reason else None, 'error_present': error,
            'cleanup_error_count': len(result.get('cleanup_errors') or [])}


def audit(root, manifest):
    rows = []
    seen = set()
    for item in manifest['controls']:
        uid = item['control_id']
        if not re.fullmatch(r'[a-f0-9]{32}', uid) or uid in seen:
            raise ValueError('Invalid or duplicate controller identity')
        seen.add(uid)
        folder = root / 'control' / uid
        source = folder / 'report.json'
        report = json.loads(source.read_bytes())
        if digest(report) != item['report_semantic_sha256']:
            raise ValueError('Report changed since the immutable observation: ' + uid)
        for index, episode in enumerate(report.get('rounds', [])):
            row = {**item, 'episode_index': index, 'role': episode['role'],
                   'physical_calls': episode.get('model_calls'), 'exit_code': episode.get('exit_code'),
                   'report_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
            relative = episode.get('result_relative_path')
            if not relative:
                row['boundary'] = {'kind': 'no_sdk_result_reference'}
            else:
                path = folder / relative
                if not path.resolve().is_relative_to(folder.resolve()) or path.is_symlink():
                    raise ValueError('Episode result escapes its controller')
                if not path.is_file():
                    row['boundary'] = {'kind': 'result_missing'}
                else:
                    data = path.read_bytes()
                    row.update(result_relative_path=relative, result_sha256=hashlib.sha256(data).hexdigest(),
                               result_bytes=len(data), boundary=boundary(json.loads(data)))
            rows.append(row)
    return {'captured_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'status': 'complete', 'input_manifest_sha256': digest(manifest), 'model_calls': 0,
            'raw_scores_changed': False, 'controllers': len(seen), 'episodes': len(rows),
            'boundary_counts': dict(Counter(r['boundary']['kind'] for r in rows)), 'rows': rows,
            'scope': 'SDK boundary diagnostics only. Flags do not establish task correctness or permission to resume uncertain effects.'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path('/var/lib/supergoal-lab'))
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('Preserve earlier audits')
    report = audit(args.root, json.loads(args.manifest.read_text()))
    report['operator_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    # Same defense in depth as the outcome collector, without exporting config.
    credential = json.loads((args.root / 'private/model.json').read_text())
    data = json.dumps(report, ensure_ascii=False, indent=2)
    if credential.get('api_key'):
        data = data.replace(credential['api_key'], '[REDACTED]')
    with args.output.open('x') as stream:
        stream.write(data)
    print(json.dumps({k: v for k, v in report.items() if k != 'rows'}))


if __name__ == '__main__':
    main()
