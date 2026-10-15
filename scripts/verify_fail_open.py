import copy
import importlib
import json
import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = 'jev_effort_router_under_test'

def _install_package() -> None:
    if PACKAGE in sys.modules:
        return
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(ROOT)]
    package.__package__ = PACKAGE
    sys.modules[PACKAGE] = package
    for child in sorted(ROOT.glob('*.py')):
        stem = child.stem
        if stem == '__init__':
            continue
        module = importlib.import_module(f'{PACKAGE}.{stem}')
        sys.modules.setdefault(stem, module)
        setattr(package, stem, module)

_install_package()

from config import load_settings
from router import Router

def verify_fail_open_byte_identity():
    print('=== VERIFYING BYTE-IDENTICAL KWARGS ON FAIL-OPEN ===')

    base_conf = {
        'enabled': True,
        'decider': 'openai_compatible',
        'decider_base_url': 'https://opencode.ai/zen/go/v1',
        'decider_model': 'deepseek-v4.1-flash',
        'decider_api_key_env': 'OPENCODE_GO_API_KEY',
        'routable_providers': ['opencode-go'],
        'timeout_s': 2.0,
        'grids': {
            'opencode-go': [
                {'model': 'deepseek-v4.1-flash', 'profile': 'Everyday writing and general coding'},
                {'model': 'kimi-k3', 'profile': 'Complex multi-file refactoring and deep reasoning'},
            ]
        },
        'effort_maps': {
            'opencode-go': {
                'deepseek-v4.1-flash': {'accepted': ['low', 'medium', 'high']}
            }
        }
    }

    original_request = {
        'model': 'deepseek-v4.1-flash',
        'messages': [
            {'role': 'system', 'content': 'You are a helpful assistant.'},
            {'role': 'user', 'content': 'Calculate 2+2 and return only the number.'}
        ],
        'temperature': 0.7,
        'max_tokens': 100,
        'stream': False,
        'custom_header_metadata': {'caller': 'test_suite'}
    }

    # Case 1: Missing API key in environment
    print('\n--- Case 1: Missing API key ---')
    os.environ.pop('NONEXISTENT_KEY_FOR_TEST', None)
    conf_missing_key = dict(base_conf, decider_api_key_env='NONEXISTENT_KEY_FOR_TEST')
    settings_missing = load_settings(lambda k, d=None: conf_missing_key.get(k, d))
    router_missing = Router(lambda: settings_missing)

    req_copy1 = copy.deepcopy(original_request)
    res1 = router_missing.on_llm_request(
        request=req_copy1,
        original_request=dict(original_request),
        provider='opencode-go',
        model='deepseek-v4.1-flash',
        turn_id='test-turn-missing-key',
        session_id='test-session-1',
        platform='cli',
        api_mode='chat_completions'
    )
    print(f'Router returned: {res1}')
    assert res1 is None, f'Expected None (fail-open), got {res1}'
    assert req_copy1 == original_request, 'Request was modified during fail-open!'
    print('VERIFIED: res is None -> original kwargs unchanged (byte-identical)')

    # Case 2: Invalid base_url (network error / unreachable host)
    print('\n--- Case 2: Invalid decider base_url ---')
    conf_bad_url = dict(base_conf, decider_base_url='https://invalid-decider-endpoint.local/v1')
    settings_bad_url = load_settings(lambda k, d=None: conf_bad_url.get(k, d))
    router_bad_url = Router(lambda: settings_bad_url)

    req_copy2 = copy.deepcopy(original_request)
    res2 = router_bad_url.on_llm_request(
        request=req_copy2,
        original_request=dict(original_request),
        provider='opencode-go',
        model='deepseek-v4.1-flash',
        turn_id='test-turn-bad-url',
        session_id='test-session-2',
        platform='cli',
        api_mode='chat_completions'
    )
    print(f'Router returned: {res2}')
    assert res2 is None, f'Expected None (fail-open), got {res2}'
    assert req_copy2 == original_request, 'Request was modified during fail-open!'
    print('VERIFIED: res is None -> original kwargs unchanged (byte-identical)')

    # Case 3: Middleware contract simulation
    print('\n--- Case 3: Middleware contract simulation ---')
    outgoing_kwargs = res2['request'] if (res2 is not None and 'request' in res2) else req_copy2
    assert outgoing_kwargs == original_request
    assert json.dumps(outgoing_kwargs, sort_keys=True) == json.dumps(original_request, sort_keys=True)
    print('VERIFIED: outgoing payload is byte-identical to original_request')
    print('\nALL FAIL-OPEN CHECKS PASSED!')

if __name__ == '__main__':
    verify_fail_open_byte_identity()
