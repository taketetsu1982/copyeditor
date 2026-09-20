import json
import logging
import math
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastmcp import Client

from copyeditor.config import load_config
from copyeditor.judgment import ACTION_CRITERIA
from copyeditor.providers.base import GenerationResult, Usage
from copyeditor.providers.typesafe import TypeSafe
from copyeditor.rules import load_rules
from copyeditor.server import build_server
from copyeditor.service import Service


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled,degree', [(False, 'polish'), (False, 'rewrite'), (True, 'polish'), (True, 'rewrite')])
@pytest.mark.parametrize('source', ['yaml', 'environment', 'example', 'tiny', 'precise'])
async def test_loaded_decimal_config_returns_json_success(tmp_path, enabled, degree, source):
    minimum = '1e-1000' if source == 'tiny' else '0.5000000000000000000000000001' if source == 'precise' else '0.5'
    path = tmp_path / 'config.yaml'
    env = {'GOOGLE_CLOUD_PROJECT': 'fixture', 'COPYEDITOR_JUDGMENT_ENABLED': str(enabled).lower(),
           'TYPESAFE_API_KEY': 'synthetic'}
    if source == 'example':
        path.write_text(Path('config.example.yaml').read_text().replace('enabled: false', f'enabled: {str(enabled).lower()}'))
    elif source == 'environment':
        env.update(COPYEDITOR_LENGTH_RATIO_MIN=minimum, COPYEDITOR_LENGTH_RATIO_MAX='2.0')
    else:
        path.write_text(f'length_ratio: {{min: {minimum}, max: 2.0}}\n')
    prices = '{"currency": "USD", "input_per_million": 0.125, "output_per_million": 0.25}'
    env['COPYEDITOR_PRICING'] = '{"gemini-3.1-flash-lite": ' + prices + '}'
    env['COPYEDITOR_JUDGMENT_PRICING'] = '{"jev-1.13.0": ' + prices.replace('0.125', '0.001').replace('0.25', '0.002') + '}'
    config = load_config(path, env)
    assert isinstance(config['length_ratio.min'], Decimal)
    calls = []
    class Editor:
        async def estimate_input(self, value): return 0
        async def generate(self, value):
            calls.append(value.stage)
            body = (dict(diagnoses=[dict(id=i.id, status='issue', expression=i.text, reason='Expression issue.') for i in value.items])
                    if value.stage == 'diagnose' else dict(items=[dict(id=i.id, text='ab' if source == 'precise' else i.text, flag=None) for i in value.items]))
            return GenerationResult(json.dumps(body), 'stop', Usage(1, 1, 2))
    def handler(request):
        data = json.loads(request.content)
        answers = {key: (dict(type='choice', choice='simplify_vocabulary', confidence=0,
                   probabilities={a: int(a == 'simplify_vocabulary') for a in ACTION_CRITERIA})
                   if q['type'] == 'choice' else dict(type='noul', noul=0.9)) for key, q in data['questions'].items()}
        return httpx.Response(200, json=dict(model='jev-1.13.0', answers=answers, usage=dict(input_tokens=1, output_tokens=1)))
    disabled = logging.root.manager.disable
    adapter = TypeSafe(config.secrets['TYPESAFE_API_KEY'], transport=httpx.MockTransport(handler)) if enabled else None
    try:
        snapshot = load_rules(Path('rules'), None)
        server = build_server(config, snapshot, Service(config, snapshot, Editor, adapter), None, lambda record: None)
        async with Client(server) as client:
            result = await client.call_tool('polish_text', dict(text='abcd', degree=degree), raise_on_error=False)
        payload = result.structured_content
        assert not result.is_error and payload['status'] == 'ok', json.dumps(payload)
        assert payload['schema_version'] == (3 if enabled else 2 if degree == 'rewrite' else 1)
        assert json.loads(result.content[0].text) == payload
        json.dumps(payload, allow_nan=False)
        ratio = payload['preservation']['length_ratio']
        assert all(type(value) in (int, float) and math.isfinite(value) for value in ratio.values())
        assert 0 < ratio['min'] <= 1 and ratio['max'] == 2
        assert payload['text'] == 'abcd'
        assert (payload['flag'] is not None) == (source == 'precise')
        assert len(calls) == (2 if degree == 'rewrite' else 1) + (source == 'precise')
        assert config['length_ratio.min'] == Decimal(minimum)
        if source != 'example':
            assert isinstance(config['pricing'][config['model']]['input_per_million'], Decimal)
            assert type(payload['cost']['amount']) is str
    finally:
        if adapter: await adapter.aclose()
        logging.disable(disabled)
