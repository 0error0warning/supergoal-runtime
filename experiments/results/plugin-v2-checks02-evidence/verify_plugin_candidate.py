"""Zero-model regression of a staged plugin against the pinned real host ABI."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


def main():
    root = Path(__file__).resolve().parent
    receipt_path = root / 'receipt.json'
    if receipt_path.exists():
        raise FileExistsError('Preserve previous check attempts')
    manifest = json.loads((root / 'manifest.json').read_text())

    def verify_sources():
        for name, expected in manifest['files'].items():
            if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
                raise ValueError('Staged source changed: ' + name)

    verify_sources()
    import hermes_cli.plugins as host
    import pytest
    import pytest_asyncio
    import sqlite3
    from supergoal_runtime.v2.kernel import Kernel

    observed = Path(host.__file__).resolve()
    if not observed.is_relative_to(Path(manifest['hermes_source']).resolve()):
        raise ValueError('Integration test would use a different host')
    probe = Kernel(root / 'journal-probe.sqlite3')
    command = [sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
               '--junitxml=' + str(root / 'junit.xml'),
               'tests/unit/test_v2_kernel.py', 'tests/unit/test_v2_acceptance_workflow.py',
               'tests/unit/test_v2_sqlite_compatibility.py', 'tests/integration/test_v2_host_abi.py']
    with (root / 'pytest.log').open('xb') as log:
        done = subprocess.run(command, cwd=root, stdout=log, stderr=subprocess.STDOUT, timeout=240)
    verify_sources()
    cases = ET.parse(root / 'junit.xml').findall('.//testcase') if (root / 'junit.xml').exists() else []
    counts = {'tests': len(cases), 'failures': sum(c.find('failure') is not None for c in cases),
              'errors': sum(c.find('error') is not None for c in cases),
              'skipped': sum(c.find('skipped') is not None for c in cases)}
    abi = [c for c in cases if c.get('classname', '').endswith('test_v2_host_abi')]
    passed = done.returncode == 0 and len(abi) == 2 and counts['tests'] > 2 \
        and not any(counts[k] for k in ['failures', 'errors', 'skipped'])
    receipt = {'status': 'passed' if passed else 'failed', 'captured_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
               'host': 'supergoal-gcp', 'command': command, 'exit_code': done.returncode,
               'counts': counts, 'real_host_abi_cases': len(abi), 'hermes_import_path': str(observed),
               'hermes_source': manifest['hermes_source'], 'python': sys.version,
               'pytest': pytest.__version__, 'pytest_asyncio': pytest_asyncio.__version__,
               'sqlite': sqlite3.sqlite_version, 'kernel_journal_mode': probe.journal_mode,
               'source_manifest_sha256': hashlib.sha256((root / 'manifest.json').read_bytes()).hexdigest(),
               'operator_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               'log_sha256': hashlib.sha256((root / 'pytest.log').read_bytes()).hexdigest(),
               'junit_sha256': hashlib.sha256((root / 'junit.xml').read_bytes()).hexdigest() if cases else None,
               'private_network': os.environ.get('SUPERGOAL_TEST_PRIVATE_NETWORK') == '1',
               'model_calls': 0, 'production_deployed': False,
               'scope': 'Current kernel, acceptance/workflow and real registry ABI; no model-task or production Gateway run.'}
    with receipt_path.open('x') as stream:
        json.dump(receipt, stream, indent=2)
    print(json.dumps(receipt))
    if not passed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
