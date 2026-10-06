import json
import http.client
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from experiments.model_proxy import ModelProxy


def test_budget_survives_proxy_process_boundary_and_disconnect_is_counted(tmp_path):
    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            data = json.dumps({'model': 'devin/swe-2', 'status': 'completed', 'usage': {'total_tokens': 1}}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    provider = {'base_url': f'http://127.0.0.1:{upstream.server_port}/v1', 'api_key': 'fixture'}
    journal = tmp_path / 'journal.json'

    def request(proxy):
        req = urllib.request.Request(proxy.config()['base_url'] + '/responses',
                                     data=b'{"model":"devin/swe-2","stream":false}')
        return urllib.request.urlopen(req, timeout=5).read()

    try:
        with ModelProxy(provider, limit=2, journal=journal) as proxy:
            assert json.loads(request(proxy))['status'] == 'completed'
        with ModelProxy(provider, limit=2, journal=journal, disconnect_at=2) as proxy:
            request(proxy)  # Socket is closed after headers, before the payload.
        saved = json.loads(journal.read_text())
        assert [r['index'] for r in saved['records']] == [1, 2]
        assert saved['records'][1]['injected_fault'] == 'response_stream_disconnect'
        with ModelProxy(provider, limit=2, journal=journal) as proxy:
            with pytest.raises(urllib.error.HTTPError) as exc:
                request(proxy)
            assert exc.value.code == 429
        with pytest.raises(ValueError, match='reset'):
            ModelProxy(provider, limit=3, journal=journal)
    finally:
        upstream.shutdown()
        upstream.server_close()


@pytest.mark.parametrize('upstream_status,body,expected_error', [
    (503, b'private-provider-diagnostic', 'HTTPError'),
    (200, b'data: {"type":"response.created"}\n\n', 'MissingTerminalResponse'),
    (200, b'data: {"type":"response.completed","response":{"status":"completed"}}\n\n', None),
])
def test_upstream_status_and_terminal_stream_are_distinct(tmp_path, upstream_status, body, expected_error):
    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(upstream_status)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    journal = tmp_path / 'journal.json'
    try:
        provider = {'base_url': f'http://127.0.0.1:{server.server_port}/v1', 'api_key': 'fixture'}
        with ModelProxy(provider, limit=1, journal=journal) as proxy:
            client = http.client.HTTPConnection('127.0.0.1', proxy.server.server_port, timeout=5)
            client.request('POST', '/v1/responses', body=b'{"model":"devin/swe-2","stream":true}')
            response = client.getresponse()
            data = response.read()
            assert response.status == upstream_status
            assert b'private-provider-diagnostic' not in data
            client.close()
        record, = json.loads(journal.read_text())['records']
        assert record['http_status'] == upstream_status
        assert record.get('error') == expected_error
        if upstream_status == 200:
            assert record['terminal_event_received'] == (expected_error is None)
    finally:
        server.shutdown()
        server.server_close()


def test_transport_failure_does_not_insert_second_http_response_into_sse(tmp_path, monkeypatch):
    class BrokenStream:
        status = 200
        headers = {'Content-Type': 'text/event-stream'}
        reads = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read1(self, size):
            self.reads += 1
            if self.reads == 1:
                return b'data: {"type":"response.created"}\n\n'
            raise TimeoutError('fixture stream stopped')

    monkeypatch.setattr(urllib.request, 'urlopen', lambda *a, **kw: BrokenStream())
    journal = tmp_path / 'journal.json'
    with ModelProxy({'base_url': 'http://unused/v1', 'api_key': 'fixture'}, limit=1, journal=journal) as proxy:
        client = http.client.HTTPConnection('127.0.0.1', proxy.server.server_port, timeout=5)
        client.request('POST', '/v1/responses', body=b'{"model":"devin/swe-2","stream":true}')
        response = client.getresponse()
        assert response.status == 200
        assert response.read() == b'data: {"type":"response.created"}\n\n'
        client.close()
    record, = json.loads(journal.read_text())['records']
    assert record['error'] == 'TimeoutError'
    assert record['error_origin'] == 'stream_transport'
    assert record['http_status'] == 200
