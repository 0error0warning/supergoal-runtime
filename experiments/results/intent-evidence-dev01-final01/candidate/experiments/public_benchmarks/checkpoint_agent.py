"""Opt-in next candidate; never mutate an already frozen running cohort."""
import json

from mechanism_agent import MechanismStudy
from mini_agent import MiniSweStudy
from harbor_agent import dump
from snapshot_lifecycle import capture_snapshot


class SnapshotMixin:
    def __init__(self, *args, **kwargs):
        self.snapshot_receipts = []
        super().__init__(*args, **kwargs)

    async def docker(self, *args, timeout=45):
        if args and args[0] == 'commit':
            if len(args) != 3:
                raise ValueError('Checkpoint adapter expects explicit container and tag')
            receipt = {}
            self.snapshot_receipts.append(receipt)
            try:
                return await capture_snapshot(super().docker, args[1], args[2], receipt, timeout=timeout)
            finally:
                dump(self.control / 'snapshot-lifecycle.json', self.snapshot_receipts)
        return await super().docker(*args, timeout=timeout)

    async def run(self, instruction, environment, context):
        try:
            await super().run(instruction, environment, context)
        finally:
            path = self.control / 'report.json'
            if path.exists():
                report = json.loads(path.read_text())
                report['snapshot_lifecycle'] = self.snapshot_receipts
                if any(r.get('state_restored') is False for r in self.snapshot_receipts):
                    report.update(status='adapter_error', error_type='SnapshotStateError')
                    if getattr(context, 'metadata', None):
                        context.metadata['study_status'] = 'adapter_error'
                dump(path, report)


class CheckpointStudy(SnapshotMixin, MechanismStudy):
    def version(self):
        return 'checkpoint-dev08'


class CheckpointMiniStudy(SnapshotMixin, MiniSweStudy):
    def version(self):
        return 'mini-swe-agent-2.4.6-checkpoint08'
