"""Pinned inputs for a paired continuation from one previously saved prefix."""
import hashlib
import json
from pathlib import Path
import re


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def first_registered_origins(registration, receipt):
    expected = [(r['task_id'], r['arm']) for r in registration['planned_order']]
    rows = receipt['rows']
    if [(r['task_id'], r['arm']) for r in rows] != expected or len(set(expected)) != len(expected):
        raise ValueError('Source assignment differs from its registration')
    origins = {}
    for row in rows:
        origins.setdefault(row['task_id'], row)
    if any(r['status'] != 'returned' for r in origins.values()):
        raise ValueError('The prescribed origin is unavailable; do not select a substitute')
    return origins


def load_prefix(path, expected_sha256):
    path = Path(path)
    if digest(path) != expected_sha256:
        raise ValueError('Prefix specification changed')
    spec = json.loads(path.read_text(encoding='utf-8'))
    if spec.get('schema') != 1 or spec.get('prefix_checkpoint_count') != 1:
        raise ValueError('Expected exactly one fixed completed prefix checkpoint')
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', spec.get('image_id', '')):
        raise ValueError('Initial image must be pinned by content ID')
    if not re.fullmatch(r'[a-f0-9]{64}', spec.get('workspace_sha256', '')):
        raise ValueError('Expected initial workspace digest')
    for key in ('public_requirement', 'grader_history'):
        if digest(spec[key]['path']) != spec[key]['sha256']:
            raise ValueError('Pinned prefix input changed: ' + key)
    requirement = Path(spec['public_requirement']['path']).read_text(encoding='utf-8')
    if not requirement.strip():
        raise ValueError('Missing public prefix requirement')
    return spec, requirement


# Run before any model operation. Match the upstream artifact exclusions and
# retain symlink targets without following them outside the workspace.
WORKSPACE_PROBE = r'''import hashlib,json,os,pathlib,stat
root=pathlib.Path('/app')
skip={'.venv','__pycache__','.pytest_cache','.ruff_cache','.mypy_cache','.git','node_modules','target','dist','build'}
files={}
for folder,dirs,names in os.walk(root,followlinks=False):
 dirs[:]=sorted(d for d in dirs if d not in skip)
 for name in sorted((set(names)-skip)|{d for d in dirs if (pathlib.Path(folder)/d).is_symlink()}):
  p=pathlib.Path(folder)/name
  key=p.relative_to(root).as_posix()
  if p.is_symlink(): files[key]={'kind':'symlink','target':os.readlink(p)}
  elif stat.S_ISREG(p.stat().st_mode):
   with p.open('rb') as f: value=hashlib.file_digest(f,'sha256').hexdigest()
   files[key]={'kind':'file','sha256':value,'bytes':p.stat().st_size}
  else: files[key]={'kind':'special','mode':stat.S_IFMT(p.stat().st_mode)}
encoded=json.dumps(files,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
print(json.dumps({'workspace_sha256':hashlib.sha256(encoded).hexdigest(),'file_entries':len(files)}))
'''
