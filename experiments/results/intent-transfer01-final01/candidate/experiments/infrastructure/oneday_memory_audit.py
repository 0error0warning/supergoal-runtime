"""One supplementary grading attempt for pre-model parser failures only.

Keep original failed receipts. Use identical blinded artifacts, rubric and model;
raise the independently reserved grader RAM from 4 to 8 GiB. Never regrade a
completed model decision or select the highest score across attempts.
"""
import asyncio
import datetime
import hashlib
import json
import time

from harbor_agent import ROOT, HermesSupergoalStudy, dump
from oneday_operator import judge
from resource_pool import ResourcePool


async def main():
    admission_deadline = datetime.datetime.fromisoformat('2026-10-06T14:14:06+00:00').timestamp()
    receipt_path = ROOT / 'setup/oneday-memory-audit01.json'
    if receipt_path.exists():
        raise FileExistsError(receipt_path)
    receipt = {'status': 'waiting_resources', 'memory_gb': 8, 'original_results_unchanged': True,
               'rule': 'taskif_94 rows with parser failure and no original judge request', 'rows': []}
    dump(receipt_path, receipt)
    pool = ResourcePool(ROOT / 'run/parallel-resource-pool.json')
    for index in [9, 10, 11]:
        # The ongoing formal cohort remains free to finish with its original cap.
        while True:
            batch = json.loads((ROOT / 'setup/oneday-transfer01-receipt.json').read_text())
            row = next(r for r in batch['rows'] if r['index'] == index)
            if row['status'] not in {'pending', 'running'}:
                break
            if time.time() > admission_deadline - 1800:
                receipt['status'] = 'admission_closed'
                dump(receipt_path, receipt)
                return
            await asyncio.sleep(10)
        parent = ROOT / 'oneday-artifacts' / f'oneday-oneday-transfer01-{index:02d}'
        original = parent / 'judge'
        if row['status'] != 'requires_audit' or (original / 'request.json').exists() or (original / 'response.json').exists():
            receipt['rows'].append({'index': index, 'status': 'not_eligible', 'original_status': row['status']})
            dump(receipt_path, receipt)
            continue
        name = f'oneday-memory-audit01-{index}'
        while not pool.acquire(name, {'cpus': 1, 'memory_mb': 8192}):
            if time.time() > admission_deadline - 1800:
                receipt['status'] = 'admission_closed'
                dump(receipt_path, receipt)
                return
            await asyncio.sleep(5)
        out = {'index': index, 'status': 'running', 'original_error': row.get('error'),
               'config_sha256': hashlib.sha256((original / 'config.json').read_bytes()).hexdigest(),
               'artifact_manifest_sha256': hashlib.sha256((parent / 'artifact-manifest.json').read_bytes()).hexdigest()}
        receipt['rows'].append(out)
        receipt['status'] = 'running'
        dump(receipt_path, receipt)
        config = json.loads((original / 'config.json').read_text())
        agent = HermesSupergoalStudy(ROOT / 'control')
        try:
            out.update(await judge(agent, config['question_id'], config['answer'], parent / 'submission',
                                   config['files'], parent / 'judge-audit-memory08', memory_gb=8))
        except Exception as exc:
            out.update(status='requires_audit', error=type(exc).__name__ + ': ' + str(exc)[:1200])
        finally:
            pool.release(name)
            dump(receipt_path, receipt)
    receipt['status'] = 'complete_with_itemized_outcomes'
    dump(receipt_path, receipt)


if __name__ == '__main__':
    asyncio.run(main())
