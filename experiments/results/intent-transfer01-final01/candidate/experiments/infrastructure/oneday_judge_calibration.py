"""Calibration on two separate development IDs; no solver or tuning of holdout tasks."""
import asyncio
import json
import shutil

from harbor_agent import HermesSupergoalStudy, ROOT, dump
from oneday_operator import BENCH, judge


async def main():
    target = ROOT / 'setup/oneday-judge-calibration02.json'
    if target.exists():
        raise FileExistsError(target)
    qs = {q['question_id']: q for q in map(json.loads, (BENCH / 'datasets/questions_all.jsonl').read_text().splitlines())}
    state = {'status': 'running', 'ids': ['taskif_47'],
             'supersedes_for_gate_only': 'oneday-judge-calibration01.json',
             'reason': 'Original positive example taskif_2 had no reference answer. Fix aggregation by calling upstream calculate_question_score directly; preserve all original responses.',
             'kind': 'adapted_SWE2_judge_development_calibration', 'rows': []}
    dump(target, state)
    for qid in state['ids']:
        for kind in ['empty', 'reference']:
            q = qs[qid]
            row = {'question_id': qid, 'submission': kind}
            state['rows'].append(row)
            parent = ROOT / 'oneday-artifacts' / f'calibration02-{qid}-{kind}'
            submission = parent / 'submission'
            submission.mkdir(parents=True, exist_ok=False)
            files = []
            if kind == 'reference':
                for name in q['reference_answer_attachment_filenames']:
                    source = BENCH / 'Attachments/Reference_answer' / name
                    if not source.is_file():
                        raise FileNotFoundError('Official reference attachment missing: ' + name)
                    shutil.copy2(source, submission / name)
                    files.append(name)
            agent = HermesSupergoalStudy(ROOT / 'control')
            try:
                row.update(await judge(agent, qid, '' if kind == 'empty' else q['reference_answer_description'],
                                       submission, files, parent / 'judge'))
            except Exception as exc:
                row.update(status='failed', error=f'{type(exc).__name__}: {exc}'[:1000])
            dump(target, state)
    state['status'] = 'passed' if all(r.get('max_score', 0) > 0 and (
        r['score'] / r['max_score'] <= 0.25 if r['submission'] == 'empty'
        else r['score'] / r['max_score'] >= 0.5) for r in state['rows']) else 'requires_audit'
    dump(target, state)


if __name__ == '__main__':
    asyncio.run(main())
