"""Executed only after an episode in a separate sandbox without model access."""
import copy
import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

cases = json.loads(Path('/task/cases.json').read_text())


def standard_library_only(path):
    tree = ast.parse(Path(path).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(item.name.split('.')[0] for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append((node.module or '').split('.')[0])
    return all(name in sys.stdlib_module_names or name == '__future__' for name in modules)


if isinstance(cases, dict) and cases.get('family') == 'data':
    try:
        stdlib = standard_library_only('reproduce.py')
        process = subprocess.run([sys.executable, 'reproduce.py'], capture_output=True, timeout=15)
        output = json.loads(Path('result.json').read_text()) if Path('result.json').is_file() else None
        Path('/records/grade.json').write_text(json.dumps({'exit_code': process.returncode, 'output': output, 'stdlib_only': stdlib}, ensure_ascii=False))
    except Exception as exc:
        Path('/records/grade.json').write_text(json.dumps({'error': type(exc).__name__}))
    raise SystemExit(0)
results = []
try:
    stdlib = standard_library_only('/workspace/normalizer.py')
    spec = importlib.util.spec_from_file_location('submission', '/workspace/normalizer.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for case in cases:
        before = copy.deepcopy(case)
        try:
            value = module.normalize(**case)
            results.append({'output': value, 'input_unchanged': case == before, 'stdlib_only': stdlib})
        except Exception as exc:
            results.append({'error': type(exc).__name__})
except Exception as exc:
    results = [{'error': type(exc).__name__}]
Path('/records/grade.json').write_text(json.dumps(results, ensure_ascii=False))
