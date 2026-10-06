"""Own the pause lifecycle instead of delegating it to a cancellable commit.

Cancelling a Docker CLI does not cancel the daemon's operation. A failed commit
therefore yields no usable checkpoint, even if a tag eventually appears. Pause
ownership is explicit, so a timed-out capture cannot silently strand the actor.
"""
from __future__ import annotations

import asyncio
import json
import re
import time


class SnapshotStateError(RuntimeError):
    """The container state could not be restored; handoff requires reconciliation."""


async def capture_snapshot(docker, container, tag, receipt, *, timeout=45):
    async def inspect():
        return json.loads(await docker('inspect', container, timeout=10))[0]

    info = await inspect()
    before = info['State']
    # Docker commit inherits Compose ownership labels. Compose down --rmi local
    # then selects and deletes tagged checkpoint images from the same project.
    # Empty values detach them from that project; a tag alone is insufficient.
    labels = sorted(key for key in (info.get('Config', {}).get('Labels') or {})
                    if key.startswith('com.docker.compose.')
                    and all(c.isalnum() or c in '._-' for c in key))
    changes = [arg for key in labels for arg in ('--change', f'LABEL {key}=""')]
    owns_pause = before['Running'] and not before['Paused']
    receipt.update(container_id=container, image_tag=tag, state_before=before,
                   owns_pause=owns_pause, detached_compose_labels=labels,
                   started_monotonic=time.monotonic(), status='capturing')
    try:
        if owns_pause:
            await docker('pause', container, timeout=10)
        # Never let the daemon own an implicit pause that outlives this client.
        output = await docker('commit', '--no-pause', *changes, container, tag, timeout=timeout)
        image_id = output.strip().splitlines()[-1] if output.strip() else ''
        if not re.fullmatch(r'sha256:[a-f0-9]{64}', image_id):
            raise ValueError('Docker commit returned no unambiguous image digest')
        receipt.update(status='captured', image_id=image_id)
        return image_id
    except BaseException as exc:
        receipt.update(status='capture_failed', error_type=type(exc).__name__, error=str(exc)[:1000])
        raise
    finally:
        async def restore():
            after = (await inspect())['State']
            if owns_pause and after['Paused']:
                await docker('unpause', container, timeout=10)
                after = (await inspect())['State']
            receipt['state_after'] = after
            # A caller that already owned a pause must retain it. Stopped
            # containers must remain stopped; capture does not start anything.
            if after['Paused'] != before['Paused'] or after['Running'] != before['Running']:
                raise SnapshotStateError('Capture changed container running/paused state')
            receipt['state_restored'] = True

        cleanup = asyncio.create_task(restore())
        try:
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise
        except Exception as exc:
            receipt.update(state_restored=False, restoration_error=type(exc).__name__ + ': ' + str(exc))
            raise SnapshotStateError('Container handoff is unsafe after snapshot capture') from exc
        finally:
            receipt['finished_monotonic'] = time.monotonic()
