"""Pin the metadata-selected held-out task environments before solver admission."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tomllib


ROOT = Path('/var/lib/supergoal-lab')
docker = ['docker', '-H', 'unix://' + str(ROOT / 'run/docker.sock')]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(task):
    task = dict(task)
    target = ROOT / 'tasks/intent-transfer01' / task['task_id']
    source = Path(task['path'])
    shutil.copytree(source, target)
    logpath = ROOT / 'setup' / ('intent-transfer-image-' + task['task_id'] + '.log')
    with logpath.open('xb') as log:
        subprocess.run(docker + ['pull', task['image']], stdout=log, stderr=log, check=True)
    image_id = subprocess.check_output(docker + ['image', 'inspect', task['image'], '--format', '{{.Id}}'], text=True).strip()
    config = target / 'task.toml'
    old = config.read_text()
    new = old.replace('docker_image = "' + task['image'] + '"', 'docker_image = "' + image_id + '"')
    before, after = tomllib.loads(old), tomllib.loads(new)
    before['environment']['docker_image'] = image_id
    if before != after:
        raise ValueError('Unexpected metadata adaptation')
    config.write_text(new)
    task.update(path=str(target), image_id=image_id,
                original_files_sha256={p.relative_to(source).as_posix(): sha(p) for p in source.rglob('*') if p.is_file()},
                files_sha256={p.relative_to(target).as_posix(): sha(p) for p in target.rglob('*') if p.is_file()})
    return task


def main():
    selection = json.loads((ROOT / 'setup/intent-transfer01-selection.json').read_text())
    target = ROOT / 'tasks/intent-transfer01'
    target.mkdir()
    with ThreadPoolExecutor(max_workers=2) as executor:
        tasks = list(executor.map(prepare, selection['terminal_tasks']))
    path = ROOT / 'setup/intent-transfer01-environments.json'
    with path.open('x') as handle:
        json.dump({'selection_sha256': sha(ROOT / 'setup/intent-transfer01-selection.json'),
                   'tasks': tasks, 'model_trials_started': 0}, handle, indent=2)
    print(json.dumps({'environment_lock': str(path), 'tasks': len(tasks)}))


if __name__ == '__main__':
    main()
