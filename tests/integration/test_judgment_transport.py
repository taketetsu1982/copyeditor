"""Fake-provider route acceptance; not live or native-language quality evidence.
AC-08-1: off-mode compatibility; AC-08-7, AC-08-9: errors and private logs.
AC-08-12: sending stops after failure.
AC-08-15, AC-08-16: ja/en/zh and text/items/markdown/HTML route matrix."""
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from jsonschema import Draft202012Validator

from copyeditor.config import load_config
from copyeditor.edit_protocol import output_schema
from copyeditor.judgment_v2 import POLICY_ID
from tests.contracts.harness import FIXTURE_THRESHOLDS, fixture_registry
from copyeditor.providers.base import GenerationResult, Usage
from copyeditor.providers.typesafe import TypeSafe
from copyeditor.rules import load_rules
from copyeditor.server import build_server
from copyeditor.service import Service
from tests.contracts.harness import generation_result
from tests.integration.test_transport import FIELDS, MARKER, tcp_server

ROOT = Path(__file__).resolve().parents[2]
BODY = 'Example ' + MARKER


@pytest_asyncio.fixture
async def setup(tmp_path, capsys, caplog):
    disabled, calls, records, adapters = logging.root.manager.disable, [], [], []
    state = dict(enabled=True, scenario='pass', axis='scope', wires=[])
    def make(mode='none', candidate=None):
        config = load_config(tmp_path / 'absent', {'GOOGLE_CLOUD_PROJECT': 'fixture',
            'COPYEDITOR_MODEL': 'test-model',
            'COPYEDITOR_JUDGMENT_ENABLED': str(state['enabled']).lower(), 'TYPESAFE_API_KEY': MARKER, 'COPYEDITOR_JUDGMENT_THRESHOLDS_VERSION': 'synthetic'},
            thresholds=FIXTURE_THRESHOLDS, pairs={(POLICY_ID, 'synthetic')})
        snapshot = load_rules(ROOT / 'rules', None)
        class Editor:
            async def estimate_input(self, value): return 0
            async def generate(self, value):
                calls.append(value.stage)
                if 'queue' in state: return generation_result(state['queue'].take())
                scenario = state['scenario']
                if scenario == 'cancel': raise asyncio.CancelledError()
                body = dict(items=[dict(id=i.id, text=i.text.replace('Example', 'Sample'), flag=None,
                    diagnosis=MARKER if value.stage == 'rewrite' else None) for i in value.items])
                return GenerationResult(json.dumps(body), 'stop', Usage(1, 1, 2))
        def handler(request):
            data = json.loads(request.content)
            state['wires'].append(data)
            verifying = 'originals' in data['state']
            if verifying and state['scenario'] == 'error':
                return httpx.Response(500, content=MARKER.encode())
            answers = {key: dict(type='Noul', noul=0.3 if verifying and key.endswith('.gate') else 0.9)
                       for key in data['questions']}
            return httpx.Response(200, json=dict(model='jev-1.13.0', answers=answers, usage=dict(input_tokens=1, output_tokens=1)))
        adapter = TypeSafe(config.secrets['TYPESAFE_API_KEY'], transport=httpx.MockTransport(handler)) if state['enabled'] else None
        if adapter: adapters.append(adapter)
        server = build_server(config, snapshot, Service(config, snapshot, Editor, adapter, registry=fixture_registry), None, records.append)
        return server, config, snapshot
    make.state = state
    yield make, calls, records
    for adapter in adapters: await adapter.aclose()
    logging.disable(disabled)
    assert MARKER not in repr(capsys.readouterr()) + repr(caplog.records) + json.dumps(records)


@asynccontextmanager
async def connection(setup, tcp_server, transport):
    if transport == 'tcp':
        async with tcp_server(False) as (client, *_): yield client
    else:
        server, _, _ = setup[0]()
        app = server.http_app(json_response=True, stateless_http=True)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://localhost',
                    headers={'accept': 'application/json, text/event-stream'}) as client: yield client


async def call(client, arguments, tool='polish_text'):
    response = await client.post('/mcp', json=dict(jsonrpc='2.0', id=1, method='tools/call',
                                                params=dict(name=tool, arguments=arguments)))
    assert response.status_code == 200
    result = response.json()['result']
    payload = result['structuredContent']
    assert json.loads(result['content'][0]['text']) == payload
    assert result['isError'] == (payload['status'] == 'error')
    return payload


@pytest.mark.asyncio
@pytest.mark.parametrize('transport', ['asgi', 'tcp'])
@pytest.mark.parametrize('language', ['ja', 'en', 'zh'])
@pytest.mark.parametrize('degree', ['polish', 'rewrite'])
@pytest.mark.parametrize('route', ['text', 'items', 'markdown', 'html'])
@pytest.mark.parametrize('enabled', [False, True])
async def test_public_route_matrix(setup, tcp_server, transport, language, degree, route, enabled):
    make, calls, records = setup
    make.state['enabled'] = enabled
    text = '<p>' + BODY + '</p>' if route == 'html' else BODY
    args = dict(language=language, degree=degree, format=route if route in ('markdown', 'html') else 'text')
    args.update(items=[dict(id='first', text=text), dict(id='second', text=text, context=MARKER)]) if route == 'items' else args.update(text=text)
    async with connection(setup, tcp_server, transport) as client:
        payload = await call(client, args)
        lint = await call(client, dict(text=BODY, language=language), 'lint_text')
    Draft202012Validator(output_schema('polish_text')).validate(payload)
    assert payload['status'] == 'ok' and payload['schema_version'] == 4
    assert lint['schema_version'] == 4 and payload['language'] == language
    entries = payload['items'] if route == 'items' else [payload]
    assert all(item['text'] == text.replace('Example', 'Sample') and item['flag'] is None for item in entries)
    if route == 'items': assert [i['id'] for i in entries] == ['first', 'second']
    assert len(calls) == 1
    assert len(make.state['wires']) == (2 if enabled else 0)
    assert len(records) == 2 and all(set(record) == FIELDS for record in records)


@pytest.mark.asyncio
@pytest.mark.parametrize('transport', ['asgi', 'tcp'])
async def test_raw_failures_discard_content_and_stop_sending(setup, tcp_server, transport):
    make, calls, records = setup
    make.state['scenario'] = 'error'
    async with connection(setup, tcp_server, transport) as client:
        for arguments in (None, [], True, {'text': BODY, 'degree': 'invalid'}):
            payload = await call(client, arguments)
            assert payload['schema_version'] == 4 and payload['degree'] is None
            assert payload['error']['code'] == 'invalid_input' and not payload['model_called']
            assert not calls and not make.state['wires']
        payload = await call(client, dict(items=[dict(id='a', text=BODY), dict(id='b', text=BODY)], degree='rewrite'))
        assert payload['error']['code'] == 'provider_error' and payload['degree'] == 'rewrite'
        assert payload['model_called'] and not {'text', 'items'} & payload.keys()
        assert len(calls) == 1 and len(make.state['wires']) == 2
        assert MARKER not in json.dumps(payload)
        response = await client.post('/mcp', content=b'{')
        assert response.status_code == 400 and response.json()['error']['code'] == -32700
    assert len(records) == 5 and all(set(record) == FIELDS for record in records)
