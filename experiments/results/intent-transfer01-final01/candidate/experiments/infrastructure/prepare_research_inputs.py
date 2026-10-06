"""Public DR3-Eval text/PDF adaptation; labels and graders stay host-side.

The delivery probe is not a research-quality score. Official LLM-judged metrics
are a separate offline evaluation and cannot feed back into online execution.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import urllib.parse
import urllib.request


ROOT = Path('/var/lib/supergoal-lab')


def download(url, target):
    if target.exists():
        raise FileExistsError(target)
    data = urllib.request.urlopen(url, timeout=90).read()
    target.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--split', choices=['development', 'heldout'], required=True)
    args = parser.parse_args()
    selection = json.loads((ROOT / 'setup/intent-transfer01-selection.json').read_text())['research_dataset']
    revision = selection['revision']
    ids = [selection['dev_id']] if args.split == 'development' else selection['heldout_ids']
    base = f'https://huggingface.co/datasets/{selection["repo"]}/resolve/{revision}/datasets_en/'
    queries = [json.loads(line) for line in urllib.request.urlopen(base + 'query.jsonl', timeout=30)
               .read().decode().splitlines() if line.strip()]
    destination = ROOT / 'tasks' / ('dr3-intent-' + args.split + '01')
    destination.mkdir()
    receipt = {'dataset': selection['repo'], 'revision': revision, 'split': args.split, 'tasks': [],
               'corpus_projection': 'doc_id,title,url,page_body only; original task labels and keywords removed',
               'adaptation': 'terminal/file access to fixed 128k corpus and text/PDF inputs; no dense retrieval',
               'reward_scope': 'delivery_present is only an artifact probe, NOT task success or research quality'}
    for task_id in ids:
        query = next(row for row in queries if row['task'] == task_id)
        target = destination / ('dr3-' + task_id)
        (target / 'environment/input/user_files').mkdir(parents=True)
        (target / 'environment/input/corpus').mkdir()
        (target / 'solution').mkdir()
        (target / 'tests').mkdir()
        pins = {}
        for item in selection['source_files'][task_id]:
            name = Path(item['path']).name
            if name not in query['user_files']:
                raise ValueError('Source manifest and public query differ')
            url = base + task_id + '/' + urllib.parse.quote(name)
            pins[name] = download(url, target / 'environment/input/user_files' / name)
        raw_corpus = urllib.request.urlopen(base + task_id + '/long_context_sampled_128k.json', timeout=90).read()
        corpus = json.loads(raw_corpus)
        projected, index = [], []
        for number, doc in enumerate(corpus):
            row = {'doc_id': f'D{number + 1:04d}', 'title': doc['title'], 'url': doc['url'], 'page_body': doc['page_body']}
            projected.append(row)
            text = row['title'] + '\n' + row['url'] + '\n\n' + row['page_body']
            (target / 'environment/input/corpus' / (row['doc_id'] + '.txt')).write_text(text)
            index.append({k: v for k, v in row.items() if k != 'page_body'})
        (target / 'environment/input/corpus/index.json').write_text(json.dumps(index, indent=2, ensure_ascii=False))
        # Exact public user request preserved, with a common delivery/tool wrapper.
        instruction = (query['query'] + '\n\nAttached files are in /app/input/user_files. '
            'Use /app/input/corpus as the fixed research collection; index.json maps document IDs to titles and URLs. '
            'You can search and read these files with terminal/file tools; pdftotext is installed for PDFs. '
            'Use this supplied collection for the study, without external web retrieval. '
            'Write your research report to /app/report.md, with sources supporting specific claims, '
            'and state material limitations or unresolved uncertainties. Do not modify the input files.')
        (target / 'instruction.md').write_text(instruction)
        (target / 'environment/Dockerfile').write_text('FROM sg-research-input:01\nCOPY input /app/input\nWORKDIR /app\n')
        (target / 'task.toml').write_text('''version = "1.0"
[metadata]
category = "research"
difficulty = "diagnostic"
[verifier]
timeout_sec = 60.0
[agent]
timeout_sec = 900.0
[environment]
build_timeout_sec = 600.0
cpus = 1
memory_mb = 2048
storage_mb = 10240
''')
        (target / 'solution/solve.sh').write_text('#!/bin/sh\nset -eu\nprintf "Environment-only reference: this file is not a research answer.\\n" > /app/report.md\n')
        (target / 'tests/test.sh').write_text('''#!/bin/sh
set -eu
mkdir -p /logs/verifier
python3 - <<'PY'
import json
from pathlib import Path
p=Path('/app/report.md')
present=p.is_file() and p.stat().st_size>30
Path('/logs/verifier/reward.json').write_text(json.dumps({'delivery_present': int(present)}))
print('DELIVERY_PROBE_ONLY',present)
PY
''')
        receipt['tasks'].append({'task_id': 'dr3-' + task_id, 'path': str(target),
            'query_sha256': hashlib.sha256(query['query'].encode()).hexdigest(), 'source_sha256': pins,
            'original_corpus_sha256': hashlib.sha256(raw_corpus).hexdigest(), 'corpus_documents': len(projected),
            'reference_reward_key': 'delivery_present', 'solver_seconds': 900, 'verifier_seconds': 60})
    (destination / 'provenance.json').write_text(json.dumps(receipt, indent=2))
    print(json.dumps({'provenance': str(destination / 'provenance.json'), 'task_ids': ids,
                      'public_prompts_printed': False}))


if __name__ == '__main__':
    main()
