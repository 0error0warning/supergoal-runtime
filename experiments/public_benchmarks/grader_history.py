"""Keep the SCBench grader's history outside model-visible phases.

The original bytes are copied to the host before removal, then restored before
the official verifier runs. This is a benchmark adapter, never a task criterion.
"""
import asyncio
import base64
import hashlib
import json
from pathlib import Path
import shlex


REMOTE = '/tmp/scb-check-history.jsonl'


async def execute(environment, script):
    result = await environment.exec('python3 -c ' + shlex.quote(script), user='root', timeout_sec=20)
    if result.return_code:
        raise RuntimeError('Grader history boundary failed: ' + str(result.stderr)[-600:])
    return json.loads(result.stdout)


async def conceal(environment, receipt_path):
    receipt_path = Path(receipt_path)
    if receipt_path.exists():
        raise FileExistsError(receipt_path)
    data = await execute(environment, f'''import base64,hashlib,json,pathlib,stat
p=pathlib.Path({REMOTE!r})
if p.is_symlink(): raise ValueError('Reserved grader history is a symlink')
if p.exists():
 s=p.stat()
 if not stat.S_ISREG(s.st_mode) or s.st_size>1048576: raise ValueError('Unexpected grader history file')
 b=p.read_bytes()
 print(json.dumps(dict(exists=True,data=base64.b64encode(b).decode(),sha256=hashlib.sha256(b).hexdigest(),mode=stat.S_IMODE(s.st_mode),uid=s.st_uid,gid=s.st_gid)))
else: print(json.dumps(dict(exists=False)))
''')
    if data['exists'] and hashlib.sha256(base64.b64decode(data['data'])).hexdigest() != data['sha256']:
        raise ValueError('History transfer hash mismatch')
    # Never remove the container copy before its host copy is durable.
    import os
    with receipt_path.open('x') as stream:
        json.dump(data, stream)
        stream.flush()
        os.fsync(stream.fileno())
    expected = data.get('sha256')
    hidden = await execute(environment, f'''import hashlib,json,pathlib
p=pathlib.Path({REMOTE!r})
if p.is_symlink(): raise ValueError('Reserved grader history changed')
expected={expected!r}
if expected is not None:
 if hashlib.sha256(p.read_bytes()).hexdigest()!=expected: raise ValueError('Grader history changed before hiding')
 p.unlink()
elif p.exists(): raise ValueError('Unexpected grader history appeared')
print(json.dumps(dict(hidden=not p.exists())))
''')
    if not hidden['hidden']:
        raise RuntimeError('Grader history still visible')
    return {'status': 'concealed', 'original_exists': data['exists'],
            'original_sha256': expected, 'backup_sha256': hashlib.sha256(receipt_path.read_bytes()).hexdigest()}


async def restore(environment, receipt_path):
    data = json.loads(Path(receipt_path).read_text())
    payload = json.dumps(data)
    return await execute(environment, f'''import base64,hashlib,json,os,pathlib
p=pathlib.Path({REMOTE!r})
d=json.loads({payload!r})
if p.is_symlink(): raise ValueError('Reserved grader history became a symlink')
if d['exists']:
 b=base64.b64decode(d['data'])
 if hashlib.sha256(b).hexdigest()!=d['sha256']: raise ValueError('Host history backup changed')
 if p.exists():
  if hashlib.sha256(p.read_bytes()).hexdigest()!=d['sha256']: raise ValueError('Unexpected grader history contents')
 else:
  with p.open('xb') as f: f.write(b)
 os.chmod(p,d['mode']);os.chown(p,d['uid'],d['gid'])
 if hashlib.sha256(p.read_bytes()).hexdigest()!=d['sha256']: raise ValueError('Restore hash mismatch')
elif p.exists(): raise ValueError('Model phase created reserved grader history')
print(json.dumps(dict(status='restored',original_exists=d['exists'],sha256=d.get('sha256'))))
''')


async def restore_before_cancellation(environment, receipt_path):
    task = asyncio.create_task(restore(environment, receipt_path))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.shield(task)
        raise
