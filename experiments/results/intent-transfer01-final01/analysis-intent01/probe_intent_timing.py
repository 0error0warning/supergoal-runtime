"""Post-hoc, fixed-count timing diagnosis of all four immutable final artifacts.

The primary study score is never replaced. Run serially only after the solver
and research grader stop. This uses the official hidden tests offline, with no
model calls, and keeps every rerun including failures and missing artifacts.
"""
from __future__ import annotations

import hashlib
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import tomllib
import xml.etree.ElementTree as ET


ROOT = Path('/var/lib/supergoal-lab')
OUTPUT = ROOT / 'diagnostics/intent-timing01'
DOCKER = ['docker', '-H', 'unix://' + str(ROOT / 'run/docker.sock')]


def save(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, indent=2))
    temp.replace(path)


def main():
    OUTPUT.mkdir(parents=True, exist_ok=False)
    registration = ROOT / 'setup/registration-intent-transfer01.json'
    reg = json.loads(registration.read_text())
    arms = reg['arms']
    order = [arms[i % 4:] + arms[:i % 4] for i in range(5)]
    plan = {'kind': 'post_hoc_diagnostic', 'task': 'largest-eigenval', 'repeats_per_arm': 5,
        'arms': arms, 'round_order': order, 'primary_rewards_replaced': False, 'model_calls': 0,
        'reason': 'Primary evidence arm passed online but failed two official microsecond timing assertions',
        'registration_sha256': hashlib.sha256(registration.read_bytes()).hexdigest(),
        'operator_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'planned_epoch': time.time(), 'status': 'waiting_primary_study_and_research_diagnostics'}
    save(OUTPUT / 'plan.json', plan)
    while True:
        receipt = json.loads((ROOT / 'setup/intent-transfer01-receipt.json').read_text())
        grading = json.loads((ROOT / 'setup/intent-transfer01-grader-launch01.json').read_text())
        if receipt['status'] == 'complete' and grading['status'] in {'complete', 'failed'}:
            break
        if time.time() > 1791295500:  # 2026-10-06 14:05 UTC
            raise TimeoutError('Diagnostic admission closed')
        time.sleep(15)
    if time.time() > 1791295500:
        raise TimeoutError('Diagnostic admission closed')
    for unit in ['supergoal-intent-transfer01.service', 'supergoal-intent-diagnostics01.service']:
        while subprocess.run(['systemctl', 'is-active', '--quiet', unit]).returncode == 0:
            time.sleep(2)
    task = next(t for t in reg['tasks'] if t['task_id'] == 'largest-eigenval')
    task_path = Path(task['path'])
    for name, expected in task['files_sha256'].items():
        if hashlib.sha256((task_path / name).read_bytes()).hexdigest() != expected:
            raise ValueError('Official frozen task changed')
    environment = tomllib.loads((task_path / 'task.toml').read_text())['environment']
    dependencies = OUTPUT / 'pytest-deps'
    with (OUTPUT / 'dependency-install.log').open('x') as log:
        subprocess.run([str(ROOT / 'hermes-venv/bin/python'), '-m', 'pip', 'install',
            '--disable-pip-version-check', '--target', str(dependencies), 'pytest==8.4.1',
            'pytest-json-ctrf==0.3.5'], stdout=log, stderr=log, check=True, timeout=120)
    results = []
    cutoff = datetime.datetime.fromisoformat('2026-10-06T14:21:06+00:00').timestamp()
    for repetition, arm_order in enumerate(order, start=1):
        for arm in arm_order:
            folder = OUTPUT / f'{repetition}-{arm}'
            folder.mkdir()
            primary = next(r for r in receipt['rows'] if r['task_id'] == task['task_id'] and r['arm'] == arm)
            record = {'repetition': repetition, 'arm': arm, 'primary_reward': primary.get('raw_rewards'),
                      'status': 'unmeasured', 'started_epoch': time.time(),
                      'loadavg_before': os.getloadavg(), 'cpu_limit': environment['cpus'],
                      'memory_mb': environment['memory_mb']}
            try:
                if time.time() + 120 >= cutoff:
                    raise TimeoutError('Insufficient time for a full diagnostic before cloud shutdown')
                uid = primary['agent_result']['metadata']['control_id']
                image = json.loads((ROOT / 'control' / uid / 'report.json').read_text())['artifact_image_id']
                record['artifact_image_id'] = image
                with (folder / 'pytest.log').open('xb') as log:
                    run = subprocess.run(DOCKER + ['run', '--rm', '--network', 'none',
                        '--name', f'intent-timing-{repetition}-{arm}',
                        '--cpus', str(environment['cpus']), '--memory', str(environment['memory_mb']) + 'm',
                        '--memory-swap', str(environment['memory_mb']) + 'm',
                        '--mount', f'type=bind,src={dependencies},dst=/opt/pytest-deps,readonly',
                        '--mount', f'type=bind,src={task_path / "tests"},dst=/tests,readonly',
                        '--mount', f'type=bind,src={folder},dst=/diagnostic',
                        '--env', 'PYTHONPATH=/opt/pytest-deps', '--entrypoint', 'python', image,
                        '-m', 'pytest', '/tests/test_outputs.py', '-q', '-rA', '-p', 'no:cacheprovider',
                        '--junitxml=/diagnostic/junit.xml'], stdout=log, stderr=log, timeout=120)
                junit = folder / 'junit.xml'
                if junit.is_symlink() or not junit.is_file() or junit.stat().st_size > 8 * 1024**2:
                    raise ValueError('Diagnostic result must be a bounded regular XML file')
                cases = ET.parse(junit).findall('.//testcase')
                failures = [c.get('name') for c in cases if c.find('failure') is not None]
                errors = [c.get('name') for c in cases if c.find('error') is not None]
                record.update(status='measured', exit_code=run.returncode, tests=len(cases),
                              failures=failures, errors=errors, all_passed=run.returncode == 0 and len(cases) == 27)
            except Exception as exc:
                record['error'] = type(exc).__name__ + ': ' + str(exc)
            finally:
                subprocess.run(DOCKER + ['rm', '-f', f'intent-timing-{repetition}-{arm}'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            record['finished_epoch'] = time.time()
            results.append(record)
            save(OUTPUT / 'results.json', {'scope': plan, 'rows': results})
    save(OUTPUT / 'completion.json', {'status': 'complete', 'rows': len(results), 'model_calls': 0,
        'finished_epoch': time.time(), 'primary_rewards_replaced': False,
        'runtime_python': sys.version, 'operator_sha256': plan['operator_sha256']})


if __name__ == '__main__':
    main()
