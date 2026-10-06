"""Run the actual ABI assertions without installing pytest into the frozen SDK.

Only pytest's collection/decorators are omitted. Host registry, dispatcher and
continuation APIs are real; the test's fake enqueue callback performs no I/O.
"""
import ast
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path('/var/lib/supergoal-lab')


async def main():
    source = ROOT / 'setup/v2-host-abi-test.py'
    target = ROOT / 'setup/v2-real-host-abi-dev05.json'
    if target.exists():
        raise FileExistsError(target)
    parent = ROOT / 'setup/v2-real-host-abi-dev05'
    parent.mkdir(exist_ok=False)
    os.environ['HERMES_HOME'] = str(parent / 'initial-home')
    from supergoal_runtime.plugin import register
    from hermes_cli import plugins as host

    functions = [node for node in ast.parse(source.read_text()).body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for node in functions:
        node.decorator_list = []
    namespace = dict(json=json, sys=sys, Path=Path, patch=patch, register=register, host=host)
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), 'exec'), namespace)
    tests = []
    for name in sorted(n for n in namespace if n.startswith('test_')):
        temporary = Path(tempfile.mkdtemp(dir=parent, prefix='case-'))
        os.environ['HERMES_HOME'] = str(temporary / 'hermes-home')
        await namespace[name](temporary)
        tests.append({'test': name, 'status': 'passed'})
    target.write_text(json.dumps({'status': 'passed', 'tests': tests,
          'host_version': 'Hermes 0.21.3 production source snapshot', 'model_calls': 0,
          'mode': 'standalone execution of test functions; not a pytest invocation',
          'test_source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
          'isolated_hermes_home': True}, indent=2))
    print(target.read_text())


if __name__ == '__main__':
    asyncio.run(main())
