"""Run isolated full-suite checks against the pinned real Hermes source."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path('/var/lib/supergoal-lab')
HOST = ROOT / 'private/runtime/hermes-v0.21.3-251bedc-mt2'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source, output = args.source, args.output
    output.mkdir(exist_ok=False)
    target = output / 'validation.json'
    if target.exists():
        raise FileExistsError(target)
    registration = json.loads((ROOT / 'setup/registration-intent-transfer01.json').read_text())
    pins = {name: expected for name, expected in registration['source_sha256'].items()
            if name.startswith(('supergoal_runtime/', 'tests/', 'experiments/public_benchmarks/'))}
    for name, expected in pins.items():
        if hashlib.sha256((source / name).read_bytes()).hexdigest() != expected:
            raise ValueError('Test source differs from frozen candidate: ' + name)
    dependencies = ROOT / 'checks/intent-transfer01/pytest-deps'
    env = dict(os.environ, HERMES_HOME=str(output / 'initial-test-home'),
               PYTHONPATH=os.pathsep.join(map(str, [dependencies, source, HOST])))
    command = [sys.executable, '-m', 'pytest', 'tests', '-q', '-o', 'addopts=',
               '--junitxml=' + str(output / 'junit.xml')]
    with (output / 'pytest.log').open('x') as log:
        run = subprocess.run(command, cwd=source, env=env, stdout=log, stderr=log,
                             stdin=subprocess.DEVNULL, timeout=600)
    target.write_text(json.dumps({'exit_code': run.returncode, 'command': command,
        'host_source': str(HOST), 'model_calls': 0, 'isolated_test_home': True,
        'pytest': importlib.metadata.version('pytest'),
        'pytest_asyncio': importlib.metadata.version('pytest-asyncio'),
        'frozen_files_checked': len(pins), 'source_sha256': pins,
        'complete_test_source_sha256': {p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(source.rglob('*')) if p.is_file() and '__pycache__' not in p.parts and '.pytest_cache' not in p.parts},
        'launcher_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'test_bundle_sha256': hashlib.sha256(Path('/tmp/intent-test-source02.tar.gz').read_bytes()).hexdigest(),
        'prior_setup_failures': ['source01 archive creation referenced absent plugin.py; no tests ran',
            'source02 omitted experiments/infrastructure; two collection errors; no tests ran',
            'source03 adds the frozen infrastructure directory to the isolated test copy']}, indent=2))
    print(json.dumps({'exit_code': run.returncode, 'checked_files': len(pins), 'output': str(target)}))
    raise SystemExit(run.returncode)


if __name__ == '__main__':
    main()
