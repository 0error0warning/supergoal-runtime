"""Descriptive paired results. Small generated samples do not establish generality."""
import argparse
import json
from pathlib import Path


def summarize(reports):
    lines = ['| Arm | Artifact pass | Declared complete | False completion | Runs with errors | Physical calls | Seconds |',
             '|---|---:|---:|---:|---:|---:|---:|']
    for arm in sorted({r['arm'] for r in reports}):
        rows = [r for r in reports if r['arm'] == arm]
        done = sum(r['passed'] for r in rows)
        false_done = sum(r['false_completion'] for r in rows)
        declared = sum(r['declared_complete'] for r in rows)
        infra = sum(r['status'] in {'infrastructure_error', 'supervisor_error', 'wall_timeout'} or any(e.get('error') for e in r['episodes']) for r in rows)
        calls = sum(len(r['requests']) for r in rows)
        seconds = sum(r['seconds'] for r in rows)
        lines.append(f'| {arm} | {done}/{len(rows)} | {declared} | {false_done} | {infra} | {calls} | {seconds:.1f} |')
    lines += ['', 'Counts describe these fixtures only. Different seeds within a family are related tasks,',
              'and repeated samples must not be counted as independent evidence of generality.', '',
              'The synthesis score covers structured facts and brief presence, not the prose quality',
              'of the brief. The data score includes rerunning the script on the original inputs,',
              'not generalization to unseen datasets. These limitations accompany completion claims.']
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('reports')
    parser.add_argument('--output')
    args = parser.parse_args()
    text = summarize(json.loads(Path(args.reports).read_text(encoding='utf-8')))
    if args.output:
        Path(args.output).write_text(text, encoding='utf-8')
    else:
        print(text)
