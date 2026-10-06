"""Loopback-only SWE2 allowlist, physical-call budget and usage receipt proxy.

The real credential stays in the supervisor. The executor receives a disposable
local credential. No prompt/response content is exported in proxy metrics.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class ModelProxy:
    def __init__(self, provider, *, limit=60, journal=None, disconnect_at=None):
        self.provider, self.limit = provider, limit
        self.records, self.lock = [], threading.Lock()
        self.rejections = []
        self.journal = Path(journal) if journal else None
        self.disconnect_at = disconnect_at
        if self.journal and self.journal.exists():
            saved = json.loads(self.journal.read_text())
            if saved['limit'] != limit:
                raise ValueError('Cannot reset the request budget when reopening its journal')
            self.records, self.rejections = saved['records'], saved['rejections']
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                body = json.dumps({'object': 'list', 'data': [{'id': 'devin/swe-2', 'object': 'model'}]}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                try:
                    body = json.loads(raw)
                except ValueError:
                    self.send_error(400, 'invalid JSON')
                    return
                if self.path != '/v1/responses' or body.get('model') != 'devin/swe-2':
                    with outer.lock:
                        outer.rejections.append({'time': time.time(), 'path': self.path, 'model': str(body.get('model'))[:100]})
                    self.send_error(403, 'SWE2 Responses only; fallback is disabled')
                    return
                with outer.lock:
                    if len(outer.records) >= outer.limit:
                        outer.rejections.append({'time': time.time(), 'reason': 'physical_budget'})
                        self.send_error(429, 'study physical request budget exhausted')
                        return
                    row = {'index': len(outer.records)+1, 'started': time.time(),
                           'request_model': body['model'], 'stream': bool(body.get('stream'))}
                    outer.records.append(row)
                    # Reserve durably before contacting upstream. Interrupted
                    # reservations remain charged; reopening cannot grant calls.
                    outer._persist()
                request = urllib.request.Request(outer.provider['base_url'].rstrip('/') + '/responses', data=raw,
                    headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + outer.provider['api_key']})
                headers_sent = False

                def send_proxy_error(status, code):
                    # Forward status, never private upstream bodies/headers. A
                    # second HTTP response inside an already-open SSE body is
                    # invalid; closing that stream conveys truncation instead.
                    if headers_sent:
                        return
                    data = json.dumps({'error': {'type': code, 'message': code}}).encode()
                    self.send_response(status)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)

                try:
                    with urllib.request.urlopen(request, timeout=90) as upstream:
                        row['http_status'] = upstream.status
                        self.send_response(upstream.status)
                        self.send_header('Content-Type', upstream.headers.get('Content-Type', 'application/json'))
                        self.send_header('Connection', 'close')
                        self.end_headers()
                        headers_sent = True
                        buffer = b''
                        disconnected = False
                        if row['index'] == outer.disconnect_at:
                            row['injected_fault'] = 'response_stream_disconnect'
                            self.connection.shutdown(socket.SHUT_RDWR)
                            self.connection.close()
                            disconnected = True
                        while True:
                            chunk = upstream.read1(65536)
                            if not chunk:
                                break
                            buffer += chunk
                            if not disconnected:
                                try:
                                    self.wfile.write(chunk)
                                    self.wfile.flush()
                                except OSError:
                                    disconnected = True
                                    row['client_disconnected'] = True
                        if body.get('stream'):
                            row['terminal_event_received'] = False
                            for line in buffer.splitlines():
                                if line.startswith(b'data: '):
                                    try:
                                        event = json.loads(line[6:])
                                        if event.get('type') in ('response.completed', 'response.incomplete', 'response.failed'):
                                            row['terminal_event_received'] = True
                                            row['terminal_event_type'] = event['type']
                                            response = event.get('response', {})
                                            row.update(usage=response.get('usage'), response_model=response.get('model'), response_id=response.get('id'), status=response.get('status'))
                                    except ValueError:
                                        pass
                            if not row['terminal_event_received']:
                                row['error'] = 'MissingTerminalResponse'
                        else:
                            response = json.loads(buffer)
                            row.update(usage=response.get('usage'), response_model=response.get('model'), response_id=response.get('id'), status=response.get('status'))
                except urllib.error.HTTPError as exc:
                    row.update(error=type(exc).__name__, http_status=exc.code, error_origin='upstream_http')
                    try:
                        send_proxy_error(exc.code, 'upstream_http_error')
                    except OSError:
                        pass
                    finally:
                        exc.close()
                except Exception as exc:
                    row['error'] = type(exc).__name__
                    row['error_origin'] = 'stream_transport' if headers_sent else 'upstream_connection'
                    try:
                        send_proxy_error(502, type(exc).__name__)
                    except OSError:
                        pass
                finally:
                    row['seconds'] = time.time() - row['started']
                    with outer.lock:
                        outer._persist()
                    self.close_connection = True

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def _persist(self):
        if self.journal:
            temp = self.journal.with_suffix('.tmp')
            with temp.open('w') as handle:
                json.dump({'limit': self.limit, 'records': self.records, 'rejections': self.rejections}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            temp.replace(self.journal)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()

    def config(self):
        return {'base_url': f'http://127.0.0.1:{self.server.server_port}/v1', 'api_key': 'lab-only',
                'model': 'devin/swe-2', 'api_mode': 'codex_responses'}
