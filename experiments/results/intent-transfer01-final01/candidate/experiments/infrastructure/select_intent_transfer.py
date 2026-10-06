"""Select only from metadata; do not read task prompts, solutions, or scores."""
import datetime
import hashlib
import json
from pathlib import Path
import tomllib
import urllib.request


ROOT = Path('/var/lib/supergoal-lab')
SEED = 'intent-transfer01:2026-10-06'


def rank(name):
    return hashlib.sha256((SEED + ':' + name).encode()).hexdigest()


def main():
    target = ROOT / 'setup/intent-transfer01-selection.json'
    if target.exists():
        raise FileExistsError('Selection is immutable')
    used = set(json.loads(Path('/tmp/used-task-ids.json').read_text()))
    eligible, excluded = [], []
    for directory in sorted((ROOT / 'upstream/terminal-bench-2-1/tasks').iterdir()):
        if not directory.is_dir():
            continue
        meta = tomllib.loads((directory / 'task.toml').read_text())
        env = meta['environment']
        if (directory.name in used or env.get('gpus', 0) or env.get('cpus', 1) > 2
                or env.get('memory_mb', 2048) > 4096 or not env.get('docker_image')
                or meta['agent']['timeout_sec'] > 1800 or meta['verifier']['timeout_sec'] > 1200):
            excluded.append(directory.name)
            continue
        eligible.append({'task_id': directory.name, 'category': meta['metadata']['category'],
                         'difficulty': meta['metadata']['difficulty'], 'path': str(directory),
                         'image': env['docker_image'], 'cpus': env.get('cpus', 1),
                         'memory_mb': env.get('memory_mb', 2048),
                         'solver_seconds': meta['agent']['timeout_sec'],
                         'verifier_seconds': meta['verifier']['timeout_sec'], 'rank': rank(directory.name)})
    # One task per category, up to six. Metadata eligibility is independent of model outcomes.
    categories = sorted({row['category'] for row in eligible}, key=rank)[:6]
    selected = [min((r for r in eligible if r['category'] == category), key=lambda r: r['rank'])
                for category in categories]
    # Only text/PDF source files: no unsupported video or audio tool advantage.
    endpoint = 'https://huggingface.co/api/datasets/NJU-LINK/DR3-Eval'
    info = json.load(urllib.request.urlopen(endpoint, timeout=30))
    revision = info['sha']
    files = json.load(urllib.request.urlopen(endpoint + '/tree/' + revision
                                           + '/datasets_en?recursive=true&limit=1000', timeout=30))
    groups = {}
    for row in files:
        parts = row['path'].split('/')
        if row['type'] == 'file' and len(parts) == 3 and not parts[-1].startswith('long_context'):
            groups.setdefault(parts[1], []).append(row)
    research = [key for key, group in groups.items()
                if key != '015' and all(Path(row['path']).suffix.lower() in
                                       {'.pdf', '.html', '.txt', '.md', '.csv', '.json'} for row in group)]
    research = sorted(research, key=rank)[:2]
    selection = {'selected_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                 'seed': SEED, 'method': 'sha256-ranked categories and within-category metadata-eligible tasks',
                 'excluded_prior_tasks': sorted(used), 'other_exclusions': excluded,
                 'eligible_terminal_tasks': eligible, 'terminal_tasks': selected,
                 'research_dataset': {'repo': 'NJU-LINK/DR3-Eval', 'revision': revision,
                                      'language': 'en', 'context': '128k', 'dev_id': '015',
                                      'heldout_ids': research, 'source_files': {k: groups[k] for k in research + ['015']},
                                      'eligible_ids': sorted(set(research) | set(k for k in groups
                                          if k != '015' and all(Path(r['path']).suffix.lower() in
                                              {'.pdf', '.html', '.txt', '.md', '.csv', '.json'} for r in groups[k])))},
                 'prompts_read_during_selection': False, 'model_trials_started': 0}
    with target.open('x') as handle:
        json.dump(selection, handle, indent=2)
    print(json.dumps({'path': str(target), 'terminal': selected, 'research_ids': research,
                      'dataset_revision': revision}, indent=2))


if __name__ == '__main__':
    main()
