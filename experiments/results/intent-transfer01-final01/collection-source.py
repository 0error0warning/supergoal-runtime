"""Export frozen intent-study evidence; never rerun, repair, or grade a trial.

Credentials, full trajectories, source corpora, private Hermes source and Docker
layers stay on the experiment disk. Their availability/hashes are distinguished
from files actually included in this archive.
"""
from __future__ import annotations

import argparse
from collections import Counter
import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


ROOT = Path('/var/lib/supergoal-lab')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return json.loads(path.read_text())


def collect(registration, output, *, allow_partial=False):
    reg = read(registration)
    receipt_path = ROOT / 'setup' / (reg['experiment'] + '-receipt.json')
    receipt = read(receipt_path)
    if receipt['status'] != 'complete' and not allow_partial:
        raise ValueError('Wait for the registered cohort; no silent partial export')
    output.mkdir(parents=True, exist_ok=False)
    credential = read(ROOT / 'private/model.json').get('api_key')
    redactions = []

    def copy(source, name=None):
        source = Path(source)
        if not source.resolve().is_relative_to(ROOT) or source.is_symlink() or not source.is_file():
            raise ValueError('Export source must be a regular file inside the lab')
        destination = output / (name or source.relative_to(ROOT))
        destination.parent.mkdir(parents=True, exist_ok=True)
        raw = source.read_bytes()
        if credential and credential.encode() in raw:
            raw = raw.replace(credential.encode(), b'[REDACTED]')
            redactions.append(str(destination.relative_to(output)))
        destination.write_bytes(raw)

    def write(name, data):
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(data, ensure_ascii=False, indent=2).encode()
        if credential and credential.encode() in raw:
            raw = raw.replace(credential.encode(), b'[REDACTED]')
            redactions.append(name)
        target.write_bytes(raw)

    copy(registration)
    copy(receipt_path)
    candidate = Path(reg['candidate'])
    for name, expected in reg['source_sha256'].items():
        source = candidate / name
        if sha(source.read_bytes()) != expected:
            raise ValueError('Frozen candidate changed: ' + name)
        copy(source, 'candidate/' + name)
    reference_path = Path(reg.get('reference_receipt', ROOT / 'setup' / (reg['experiment'] + '-oracles-receipt.json')))
    for name in [str(reference_path),
                 str(ROOT / 'setup/registration-intent-environment01.json'),
                 str(ROOT / 'setup/intent-transfer01-environments.json'),
                 str(ROOT / 'setup/intent-transfer01-selection.json'),
                 str(ROOT / 'setup/intent-transfer01-launch.json'),
                 str(ROOT / 'setup/intent-transfer01-grader-launch01.json'),
                 str(ROOT / 'setup/intent-transfer01-grading.log'),
                 str(ROOT / 'setup/intent-transfer01-final-audit.json'),
                 str(ROOT / 'setup/intent-transfer01-final-audit02.json'),
                 str(ROOT / 'setup/intent-transfer01-image-equivalence.json'),
                 str(ROOT / 'setup/research-image01/Dockerfile'),
                 str(ROOT / 'setup/research-image01/receipt.json'),
                 str(ROOT / 'tasks/dr3-intent-development01/provenance.json'),
                 str(ROOT / 'tasks/dr3-intent-heldout01/provenance.json')]:
        if name and Path(name).is_file():
            copy(name)
    if reference_path.is_file():
        for row in read(reference_path)['rows']:
            if row.get('result_path'):
                source = Path(row['result_path'])
                copy(source)
                for name in ['test-stdout.txt', 'test-stderr.txt', 'reward.txt', 'reward.json']:
                    if (source.parent / 'verifier' / name).is_file():
                        copy(source.parent / 'verifier' / name)
    images = set(subprocess.check_output(['docker', '-H', 'unix://' + str(ROOT / 'run/docker.sock'),
                 'image', 'ls', '--no-trunc', '--quiet'], text=True, timeout=30).splitlines())
    exported = []
    for row in receipt['rows']:
        item = {'row': row, 'trajectories': []}
        if row.get('result_path'):
            result_path = Path(row['result_path'])
            copy(result_path)
            result = read(result_path)
            for name in ['test-stdout.txt', 'test-stderr.txt', 'reward.txt', 'reward.json']:
                log = result_path.parent / 'verifier' / name
                if log.is_file():
                    copy(log)
            metadata = (result.get('agent_result') or {}).get('metadata') or {}
            control_id = metadata.get('control_id', '')
            if re.fullmatch('[a-f0-9]{32}', control_id):
                control = ROOT / 'control' / control_id
                report_path = control / 'report.json'
                for name in ['report.json', 'request-journal.json', 'environment.json',
                             'environment-bootstrap.json', 'environment-bootstrap-process.json',
                             'goal-brief.json', 'coverage-audits.json']:
                    if (control / name).is_file():
                        copy(control / name)
                for trace in sorted(control.glob('*-*/result.json')):
                    raw = trace.read_bytes()
                    data = json.loads(raw)
                    counts = Counter(m.get('name') or 'tool' for m in data.get('messages', [])
                                     if m.get('role') == 'tool')
                    item['trajectories'].append({'server_path': str(trace), 'sha256': sha(raw),
                        'bytes': len(raw), 'tool_result_counts': dict(counts), 'included': False,
                        'completed': data.get('completed')})
                if report_path.is_file():
                    image = read(report_path).get('artifact_image_id')
                    item['artifact'] = {'image_id': image, 'available_at_export': image in images,
                                        'layers_exported': False}
        exported.append(item)
    write('exported-rows.json', exported)
    grading = ROOT / 'research-grading' / reg['experiment']
    if grading.exists():
        for path in sorted(grading.rglob('*')):
            if path.name in {'rubric.json', 'rubric-lock.json', 'rubric-requests.json',
                             'diagnostics.json', 'diagnostic.json', 'input-integrity.json',
                             'judge-response.json', 'judge-requests.json', 'report.md'}:
                copy(path)
    checks = ROOT / 'checks' / reg['experiment']
    if checks.exists():
        for relative in ['validation.json', 'junit.xml', 'pytest.log',
                         'attempt02/validation.json', 'attempt02/junit.xml', 'attempt02/pytest.log']:
            if (checks / relative).is_file():
                copy(checks / relative)
    timing = ROOT / 'diagnostics/intent-timing01'
    if reg['experiment'] == 'intent-transfer01' and timing.exists():
        for path in sorted(timing.rglob('*')):
            if 'pytest-deps' not in path.parts and path.name in {
                'plan.json', 'results.json', 'completion.json', 'dependency-install.log', 'pytest.log', 'junit.xml'}:
                copy(path)
    for name in ['launch_intent_diagnostics.py', 'probe_intent_timing.py', 'audit_intent_study.py', 'audit_intent_study02.py']:
        source = ROOT / 'analysis-intent01' / name
        if source.is_file():
            copy(source)
    copy(Path(__file__).resolve(), 'collection-source.py')
    write('manifest.json', {'schema': 1, 'experiment': reg['experiment'],
        'collected_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'cohort_status': receipt['status'], 'new_model_calls': 0, 'raw_rewards_unchanged': True,
        'redacted_files': redactions,
        'excluded': ['credentials and worker control/config files', 'full model trajectories',
                     'source corpora and user attachments', 'private Hermes source', 'Docker layers'],
        'files': {p.relative_to(output).as_posix(): {'sha256': sha(p.read_bytes()), 'bytes': p.stat().st_size}
                  for p in sorted(output.rglob('*')) if p.is_file()}})
    return {'output': str(output), 'rows': len(exported), 'redacted_files': redactions}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--registration', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--allow-partial', action='store_true')
    parser.add_argument('--archive', type=Path)
    args = parser.parse_args()
    result = collect(args.registration, args.output, allow_partial=args.allow_partial)
    if args.archive:
        if args.archive.exists():
            raise FileExistsError(args.archive)
        archive = shutil.make_archive(str(args.archive).removesuffix('.tar.gz'), 'gztar',
                                      root_dir=args.output.parent, base_dir=args.output.name)
        result.update(archive=archive, sha256=sha(Path(archive).read_bytes()))
    print(json.dumps(result))


if __name__ == '__main__':
    main()
