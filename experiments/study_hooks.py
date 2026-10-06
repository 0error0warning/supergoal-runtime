"""Read-only observation through the actual Hermes plugin hook interface."""
import json
import threading
from pathlib import Path

CONTEXT = None
LOCK = threading.Lock()


def record(kind, fields, **kwargs):
    value = {"kind": kind, **{k: kwargs.get(k) for k in fields}}
    with LOCK, Path('/records/hooks.jsonl').open('a') as f:
        f.write(json.dumps(value, default=str, ensure_ascii=False) + '\n')


def register(ctx):
    global CONTEXT
    CONTEXT = ctx
    ctx.register_hook('post_api_request', lambda **kw: record('model',
        ['api_request_id', 'turn_id', 'model', 'response_model', 'usage', 'api_duration'], **kw))
    ctx.register_hook('post_tool_call', lambda **kw: record('tool',
        ['tool_call_id', 'turn_id', 'tool_name', 'status', 'error_type', 'args', 'result'], **kw))

