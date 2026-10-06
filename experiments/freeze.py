"""Create a content-addressed manifest before reserved task execution."""
import argparse
import hashlib
import json
from pathlib import Path

from tasks import ARMS, FAMILIES, build


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    files = sorted((root / 'experiments').glob('*.py')) + sorted((root / 'supergoal_runtime').rglob('*.py'))
    files += [root / 'experiments/protocol-v1.md', root / 'tests/unit/test_v2_kernel.py', root / 'tests/unit/test_research_tasks.py']
    manifest = {'format': 1, 'model': 'devin/swe-2', 'arms': ARMS,
                'limits': {'executor_calls': 48, 'all_physical_calls': 60, 'outer_turns': 6, 'wall_seconds': 1200, 'executor_output_tokens': 3000},
                'legacy_baseline_archive_sha256': 'e3e89f8c0763b8732f7d490b1476fa4b89489dca829f6809a96aa54b8a84a605',
                'source_sha256': {p.relative_to(root).as_posix(): digest(p.read_bytes()) for p in files},
                'reserved_tasks': {f'{family}-{seed}': digest(json.dumps(build(family, seed), sort_keys=True, ensure_ascii=False).encode())
                                   for seed in (7201, 7202, 7203) for family in FAMILIES}}
    encoded = json.dumps(manifest, ensure_ascii=False, indent=2)
    target = Path(args.output)
    with target.open('x', encoding='utf-8') as f:
        f.write(encoded + '\n')
    print(digest(encoded.encode()))


if __name__ == '__main__':
    main()
