"""Exploratory mechanism replay, not a live-model success-rate experiment.

Both arms receive the same fixed executor terminal event and artifact fault.
The frozen v2 verifier is either enabled or removed. The independent oracle
checks the literal task outcome. This measures decisions under injected faults,
not the probability of those faults in natural tasks.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from supergoal_runtime.v2 import Kernel, verify


def main():
    strong = "from pathlib import Path; assert Path('result.txt').read_text() == '42'"
    cases = [
        ('valid', '42', [sys.executable, '-c', strong]),
        ('missing_artifact', None, [sys.executable, '-c', strong]),
        ('incorrect_value', '41', [sys.executable, '-c', strong]),
        ('positive_log_failed_exit', '41', [sys.executable, '-c', "print('all checks passed'); raise SystemExit(1)"]),
        ('checker_unavailable', '42', ['nonexistent-supergoal-checker-20261005']),
        ('weak_checker', '41', [sys.executable, '-c', "print('shape accepted')"]),
    ]
    rows = []
    with tempfile.TemporaryDirectory(prefix='sg-fault-replay-') as temp:
        for case, content, argv in cases:
            for enabled in (True, False):
                directory = Path(temp) / f'{case}-{enabled}'
                directory.mkdir()
                if content is not None:
                    (directory / 'result.txt').write_text(content)
                contract = {'outcome': 'result.txt must contain exactly 42', 'artifacts': ['result.txt'],
                            'checks': [{'id': case, 'argv': argv}]}
                kernel = Kernel(directory / 'control.sqlite')
                kernel.create('g', contract)
                lease = kernel.claim('g', 'replay')
                outcome = verify(directory, contract) if enabled else {'verdict': 'unverified'}
                decision = kernel.finish(lease, outcome)
                correct = (directory / 'result.txt').exists() and (directory / 'result.txt').read_text() == '42'
                stopped = decision['status'] in {'succeeded', 'unverified_done'}
                rows.append({'case': case, 'verification_enabled': enabled, 'status': decision['status'],
                             'oracle_pass': correct, 'unmet_terminal_stop': stopped and not correct,
                             'verified_success': decision['status'] == 'succeeded', 'verdict': outcome['verdict']})
    result = {'kind': 'synthetic_counterfactual_replay', 'live_model_calls': 0,
              'scope': 'A decision probe; not an estimate of live task performance or fault prevalence.',
              'source_sha256': {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in [Path(__file__), ROOT/'supergoal_runtime/v2/kernel.py', ROOT/'supergoal_runtime/v2/verification.py']},
              'rows': rows}
    path = ROOT / 'experiments/results/fault-replay.json'
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for row in rows:
        print(row['case'], row['verification_enabled'], row['status'], row['oracle_pass'])


if __name__ == '__main__':
    main()
