"""Finite receipt/report collection for the registered OneDay transfer cohort."""
import datetime
import hashlib
import json
from pathlib import Path
import re
import time


ROOT = Path('/var/lib/supergoal-lab')


def build(receipt):
    rows = []
    for row in receipt['rows']:
        out = dict(row)
        folder = ROOT / 'oneday-artifacts' / f"oneday-oneday-transfer01-{row['index']:02d}"
        log = folder / 'judge/full.log'
        text = log.read_text(errors='replace') if log.exists() else ''
        warnings = [line[-1000:] for line in text.splitlines()
                    if re.search(r'Failed to parse|Attachment parsing failed|Failed to render|attachment does not exist|No matching result', line, re.I)]
        out['artifact_parser_audit_flags'] = warnings
        out['grade_validity'] = ('needs_parser_audit' if warnings else 'adapted_judge_graded'
                                 if row.get('final_judge', {}).get('status') == 'adapted_judge_graded' else 'not_graded')
        out['receipt_files'] = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in [folder/'artifact-manifest.json', folder/'judge/scores.json', folder/'judge/aggregate.json']
                                if p.is_file()}
        rows.append(out)
    return {'schema': 1, 'collected_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'experiment': 'oneday-transfer01', 'status': receipt['status'], 'raw_scores_unchanged': True,
            'scores_are_official_gemini_reproduction': False, 'rows': rows}


def main():
    source = ROOT / 'setup/oneday-transfer01-receipt.json'
    out = ROOT / 'exports/experiments/results/oneday-transfer01-analysis.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    stop_at = datetime.datetime.fromisoformat('2026-10-06T14:14:06+00:00').timestamp()
    while True:
        receipt = json.loads(source.read_text())
        result = build(receipt)
        temp = out.with_suffix('.tmp')
        temp.write_text(json.dumps(result, indent=2, ensure_ascii=False))
        temp.replace(out)
        if receipt['status'] != 'running' or time.time() >= stop_at:
            break
        heartbeat = receipt.get('heartbeat_at', receipt['started_at'])
        if time.time() - heartbeat > 180:
            break
        time.sleep(30)


if __name__ == '__main__':
    main()
