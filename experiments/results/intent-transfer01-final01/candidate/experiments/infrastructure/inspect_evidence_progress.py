"""Print bounded, credential-free status from this study's own control records."""
import argparse
import datetime
import json
from pathlib import Path
import sys


ROOT = Path('/var/lib/supergoal-lab')
parser = argparse.ArgumentParser()
parser.add_argument('--since', default='2026-10-06T11:34:00+00:00')
parser.add_argument('--details', action='store_true')
args = parser.parse_args()
since = datetime.datetime.fromisoformat(args.since).timestamp()
sys.path.insert(0, str(ROOT / 'candidates/intent-evidence-dev01'))
from supergoal_runtime.v2.work_policy import parse_brief  # noqa: E402

for directory in sorted((ROOT / 'control').iterdir(), key=lambda p: p.stat().st_mtime):
    if not directory.is_dir() or directory.stat().st_mtime < since:
        continue
    rounds = sorted(p.name for p in directory.iterdir() if p.is_dir() and p.name[:2].isdigit())
    if not rounds:
        continue
    journal = directory / 'request-journal.json'
    report = {'control_id': directory.name, 'roles': rounds}
    if journal.exists():
        records = json.loads(journal.read_text()).get('records', [])
        report['requests'] = len(records)
        report['http_statuses'] = {str(code): sum(r.get('http_status') == code for r in records)
                                   for code in {r.get('http_status') for r in records}}
    brief = directory / 'goal-brief.json'
    if brief.exists():
        brief = json.loads(brief.read_text())
        report.update(route=brief['route'], authority=brief['authority'], requirements=len(brief['requirements']))
        result = directory / '01-interpret/result.json'
        if result.exists() and args.details:
            data = json.loads(result.read_text())
            report['interpreter_completed'] = data.get('completed')
            report['interpreter_response'] = data.get('final_response', '')[:8000]
            try:
                parse_brief(data.get('final_response', ''), brief['original_request'])
            except Exception as exc:
                report['brief_error'] = str(exc)
    audits = directory / 'coverage-audits.json'
    if audits.exists():
        report['audits'] = [{'verdict': a['verdict'], 'reason': a['reason']} for a in json.loads(audits.read_text())]
    final = directory / 'report.json'
    if final.exists():
        data = json.loads(final.read_text())
        verdict = data.get('last_acceptance') or {}
        report.update(arm=data['arm'], status=data['status'],
                      acceptance={'verdict': verdict.get('verdict'), 'reason': str(verdict.get('reason', ''))[:500]})
    print(json.dumps(report, ensure_ascii=False))
