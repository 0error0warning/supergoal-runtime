"""Small Responses transport for operator-only judge/probe calls."""
import json
import urllib.request


def request(provider, content, *, max_tokens=12000):
    body = {"model": "devin/swe-2", "instructions": "Follow the user's request carefully.",
            "input": [{"role": "user", "content": content}],
            "max_output_tokens": max_tokens, "stream": True, "store": False}
    req = urllib.request.Request(provider['base_url'].rstrip('/') + '/responses', data=json.dumps(body).encode(),
          headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + provider['api_key']})
    completed = None
    with urllib.request.urlopen(req, timeout=180) as response:
        for line in response:
            if not line.startswith(b'data: '):
                continue
            try:
                event = json.loads(line[6:])
            except ValueError:
                continue
            if event.get('type') == 'response.completed':
                completed = event.get('response')
    if not completed or completed.get('status') != 'completed' or completed.get('model') != 'devin/swe-2':
        raise RuntimeError('SWE2 did not return a complete response from the required model')
    messages = [m for m in completed.get('output', []) if m.get('type') == 'message']
    text = ''.join(c.get('text', '') for m in messages[-1:] for c in m.get('content', []))
    return {'text': text, 'usage': completed.get('usage'), 'model': completed['model'], 'status': completed['status']}
