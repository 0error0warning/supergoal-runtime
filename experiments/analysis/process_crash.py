"""Supplementary process-crash experiment against the frozen kernel.

Each pair receives the same operation and crash point. The control removes only
the SQL transaction around the operation; it is deliberately a mechanism
ablation, not a competitive agent baseline. os._exit kills the child before
Python cleanup can commit or roll back. No model calls or production data.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(os.environ.get('SUPERGOAL_CANDIDATE_SOURCE', ROOT))
sys.path.insert(0, str(SOURCE))
from supergoal_runtime.v2 import Kernel, Lease

EXIT = 86
CONTRACT = {'outcome': 'Complete a finite task'}
OUTCOME = {'verdict': 'fail', 'reason': 'Another work item is required'}
CASES = (
    ('create_after_goal', 'create', 'INSERT INTO work'),
    ('finish_after_goal', 'finish', 'UPDATE work'),
    ('finish_after_consume', 'finish', 'INSERT INTO work'),
    ('finish_after_next_work', 'finish', 'INSERT INTO receipts'),
    ('finish_after_receipt', 'finish', 'INSERT INTO events'),
    ('finish_committed_before_ack', 'finish', None),
    ('wake_after_goal', 'wake', 'INSERT INTO work'),
    ('wake_after_next_work', 'wake', 'INSERT INTO events'),
    ('wake_committed_before_ack', 'wake', None),
    ('cancel_after_goal', 'cancel', 'UPDATE work'),
    ('claim_committed_before_dispatch', 'claim', None),
)


def snapshot(path):
    with closing(sqlite3.connect(path)) as db:
        return {table: db.execute(f'SELECT * FROM {table} ORDER BY 1,2').fetchall()
                for table in ('goals', 'work', 'receipts', 'events')}


def setup(path, operation):
    k = Kernel(path, clock=lambda: 100)
    lease = None
    if operation != 'create':
        k.create('g', CONTRACT)
        if operation != 'claim':
            lease = k.claim('g', 'initial-owner')
        if operation == 'wake':
            k.finish(lease, OUTCOME, wait_key='input')
    return lease


def act(k, operation, lease):
    if operation == 'create':
        return k.create('g', CONTRACT)
    if operation == 'finish':
        return k.finish(lease, OUTCOME)
    if operation == 'wake':
        return k.wake('g', event_id='arrival-1', key='input')
    if operation == 'cancel':
        return k.cancel('g')
    if operation == 'claim':
        return k.claim('g', 'dispatch-owner', ttl=10)
    raise ValueError(operation)


class ProbedKernel(Kernel):
    fault = None
    remove_transaction = False

    def trace(self, sql):
        if self.fault and sql.startswith(self.fault):
            os._exit(EXIT)

    @contextmanager
    def _tx(self):
        if not self.remove_transaction:
            # Use the actual candidate implementation, adding only a crash hook.
            with super()._tx() as db:
                db.set_trace_callback(self.trace)
                yield db
        else:
            # Same operations and durability pragmas, with statement autocommit.
            db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=FULL')
            db.set_trace_callback(self.trace)
            try:
                yield db
            finally:
                db.close()


def child(config_path):
    config = json.loads(Path(config_path).read_text(encoding='utf-8'))
    k = ProbedKernel(config['db'], clock=lambda: 100)
    k.remove_transaction = config['remove_transaction']
    k.fault = config['fault']
    lease = Lease(**config['lease']) if config['lease'] else None
    act(k, config['operation'], lease)
    # Lost acknowledgement: the operation committed, but no return reaches host.
    os._exit(EXIT)


def run(output):
    rows = []
    started = time.time()
    with tempfile.TemporaryDirectory(prefix='sg-process-crash-') as temporary:
        directory = Path(temporary)
        for case, operation, fault in CASES:
            for remove_transaction in (False, True):
                path = directory / f'{case}-{remove_transaction}.sqlite'
                lease = setup(path, operation)
                before = snapshot(path)
                reference = directory / (path.stem + '-reference.sqlite')
                with closing(sqlite3.connect(path)) as source, closing(sqlite3.connect(reference)) as target:
                    source.backup(target)
                act(Kernel(reference, clock=lambda: 100), operation, lease)
                after = snapshot(reference)
                config = {'db': str(path), 'lease': asdict(lease) if lease else None,
                          'operation': operation, 'fault': fault,
                          'remove_transaction': remove_transaction}
                config_path = directory / (path.stem + '.json')
                config_path.write_text(json.dumps(config), encoding='utf-8')
                proc = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                                       '--child', str(config_path)], timeout=20,
                                      capture_output=True, text=True)
                if proc.returncode != EXIT:
                    raise RuntimeError(f'Crash injection failed: {case} {proc.returncode} {proc.stderr}')
                recovered = snapshot(path)
                row = {'case': case, 'transaction_enabled': not remove_transaction,
                       'crash_exit_code': proc.returncode,
                       'consistent_boundary': recovered in (before, after),
                       'observed_boundary': 'before' if recovered == before else
                                            'after' if recovered == after else 'partial',
                       'goal_rows': len(recovered['goals']),
                       'work_rows': len(recovered['work']),
                       'receipt_rows': len(recovered['receipts']),
                       'event_rows': len(recovered['events'])}
                if not remove_transaction:
                    expected = before if fault else after
                    row['expected_boundary_pass'] = recovered == expected
                    if case == 'finish_committed_before_ack':
                        k = Kernel(path, clock=lambda: 100)
                        first = k.finish(lease, OUTCOME)
                        row['duplicate_receipt_pass'] = (first['turns'] == 1 and
                                                        snapshot(path) == recovered)
                    if case == 'wake_committed_before_ack':
                        k = Kernel(path, clock=lambda: 100)
                        row['duplicate_wake_pass'] = (not k.wake('g', event_id='arrival-1', key='input')
                                                      and snapshot(path) == recovered)
                    if case == 'claim_committed_before_dispatch':
                        k = Kernel(path, clock=lambda: 200)
                        row['safe_quarantine_pass'] = (k.claim('g', 'replacement') is None and
                                                       k.state('g')['status'] == 'needs_reconciliation')
                rows.append(row)
    report = {'kind': 'paired_real_process_crash_probe', 'live_model_calls': 0,
              'scope': 'Finite injected crash boundaries; not hardware power-loss, tool-side-effect recovery, or live-task success rates.',
              'started': started, 'seconds': time.time()-started,
              'platform': platform.platform(), 'python': platform.python_version(),
              'sqlite_version': sqlite3.sqlite_version,
              'source_sha256': {str(p.relative_to(SOURCE)) if p.is_relative_to(SOURCE) else p.name:
                                hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in (SOURCE/'supergoal_runtime/v2/kernel.py', Path(__file__).resolve())},
              'rows': rows}
    Path(output).write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'rows': len(rows), 'full_consistent': sum(r['consistent_boundary'] for r in rows if r['transaction_enabled']),
                      'ablated_consistent': sum(r['consistent_boundary'] for r in rows if not r['transaction_enabled']),
                      'output': str(output)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--child')
    parser.add_argument('--output', default=str(ROOT/'experiments/results/process-crash-local.json'))
    args = parser.parse_args()
    if args.child:
        child(args.child)
    else:
        run(args.output)
