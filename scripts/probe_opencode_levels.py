import json
import os
import urllib.error
import urllib.request
from pathlib import Path

def probe():
    home_env = Path('/home/rafael/hermes/home/.env')
    key = None
    for line in home_env.read_text().splitlines():
        if line.startswith('OPENCODE_GO_API_KEY='):
            key = line.split('=', 1)[1].strip()
            break
    if not key:
        print('No OPENCODE_GO_API_KEY found')
        return

    base_url = 'https://opencode.ai/zen/go/v1'
    url = f'{base_url}/chat/completions'
    levels = [None, 'low', 'medium', 'high', 'xhigh', 'max', 'invalid_level']

    print('=== OPENCODE REASONING_EFFORT LEVEL PROBE ===')
    for lvl in levels:
        body = {
            'model': 'deepseek-v4.1-flash',
            'messages': [{'role': 'user', 'content': 'Say hi in 1 word.'}],
            'max_tokens': 5,
        }
        if lvl is not None:
            body['reasoning_effort'] = lvl

        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode('utf-8'),
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {key}',
                'User-Agent': 'Hermes-Agent/0.21.4 (jev-effort-router)',
                'x-opencode-session': 'probe-effort-support-levels',
            },
            method='POST',
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                print(f'level={lvl!r:15} -> HTTP {resp.status} (ACCEPTED)')
        except urllib.error.HTTPError as exc:
            body_err = exc.read().decode('utf-8', errors='replace')[:120]
            print(f'level={lvl!r:15} -> HTTP {exc.code} (REJECTED): {body_err}')

if __name__ == '__main__':
    probe()
