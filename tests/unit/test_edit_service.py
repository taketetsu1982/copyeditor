"""Prepared v4 entry validates input before inference and exposes complete responses."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from copyeditor.config import ConfigError
from copyeditor.config_v4 import load_config
from copyeditor.edit_protocol import validate_final
from copyeditor.edit_service import EditService
from copyeditor.judgment_v2 import POLICY_ID
from copyeditor.providers.base import GenerationResult, SourceItem, Usage
from copyeditor.providers.typesafe import TypeSafe
from copyeditor.rules import load_rules
from tests.contracts.harness import FIXTURE_THRESHOLDS, fixture_registry


@pytest_asyncio.fixture
async def entry(tmp_path):
    for source in Path('rules').glob('*.md'):
        if source.name == 'README.md': continue
        raw = source.read_text()
        if source.stem != 'common':
            raw = '\n'.join(f'Default style: Default {source.stem} style.' if line.startswith('Default style: ') else line for line in raw.split('\n'))
        (tmp_path / source.name).write_text(raw)
    rules = load_rules(tmp_path, None)
    adapters = []
    def make(enabled=False):
        inputs, wires, estimates = [], [], []
        config = load_config(tmp_path / 'absent', {'GOOGLE_CLOUD_PROJECT': 'fixture',
            'COPYEDITOR_JUDGMENT_ENABLED': str(enabled).lower(), 'COPYEDITOR_JUDGMENT_THRESHOLDS_VERSION': 'synthetic',
            'TYPESAFE_API_KEY': 'synthetic'}, thresholds=FIXTURE_THRESHOLDS, pairs={(POLICY_ID, 'synthetic')})
        class Provider:
            async def estimate_input(self, value):
                estimates.append(value)
                return 0
            async def generate(self, value):
                inputs.append(value)
                return GenerationResult(json.dumps(dict(items=[dict(id=i.id, text=i.text.replace('Original', 'Candidate'),
                    flag=None, diagnosis='Clearer wording.' if value.stage == 'rewrite' else None) for i in value.items])),
                    'stop', Usage(0, 0, 0))
        def handler(request):
            wire = json.loads(request.content)
            wires.append(wire)
            verifying = 'originals' in wire['state']
            return httpx.Response(200, json=dict(model='jev-1.13.0', answers={key: dict(type='Noul',
                noul=0.9 if not verifying or key.endswith('meaning') else 0.3) for key in wire['questions']}))
        adapter = TypeSafe(config.secrets['TYPESAFE_API_KEY'], transport=httpx.MockTransport(handler)) if enabled else None
        if adapter: adapters.append(adapter)
        service = EditService(config, rules, Provider, adapter, registry=fixture_registry)
        return service, inputs, wires, estimates
    yield make
    for adapter in adapters: await adapter.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('degree', ['polish', 'rewrite'])
@pytest.mark.parametrize('language', ['ja', 'en', 'zh'])
async def test_edit_entry_uses_resolved_style_and_complete_mode_response(entry, enabled, degree, language):
    service, inputs, wires, estimates = entry(enabled)
    result = await service.polish(dict(text='Original.', language=language, degree=degree))
    assert result['status'] == 'ok' and result['text'] == 'Candidate.'
    assert result['schema_version'] == 4 and result['degree'] == degree
    assert inputs[0].background.tone == f'Default {language} style.' and estimates == inputs
    assert len(wires) == 2 * enabled and all('tone' not in json.dumps(wire) for wire in wires)
    validate_final(result, (SourceItem('text', 'Original.', ''),), expected_enabled=enabled, registry=fixture_registry)


@pytest.mark.asyncio
@pytest.mark.parametrize('tone', ['\t\u3000', '  Explicit tone.  '])
async def test_effective_tone_is_fixed_without_changing_explicit_whitespace(entry, tone):
    service, inputs, _, _ = entry()
    result = await service.polish(dict(text='Original.', language='en', tone=tone))
    assert result['status'] == 'ok'
    assert inputs[0].background.tone == ('Default en style.' if tone == '\t\u3000' else tone)


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('arguments,code,field', [(None, 'invalid_input', None), ({}, 'invalid_input', None),
    ({'text': 'Original.', 'language': None}, 'invalid_input', 'language'),
    ({'text': '123'}, 'invalid_input', 'language'),
    ({'text': 'Original.', 'degree': []}, 'invalid_input', 'degree'),
    ({'text': 'Original.', 'language': 'fr'}, 'unsupported_language', 'language'),
    ({'text': 'x' * 12001}, 'input_limit', 'text'),
    ({'text': 'x' * 12001, 'language': 'fr'}, 'unsupported_language', 'language'),
    ({'items': [{'id': 'a', 'text': 'One'}, {'id': 'a', 'text': 'Two'}]}, 'invalid_input', 'items[1].id')])
async def test_input_precedence_rejects_before_inference_or_provider(entry, monkeypatch, enabled, arguments, code, field):
    monkeypatch.setattr('copyeditor.language_detection._detector', lambda: pytest.fail('Unexpected inference'))
    service, inputs, wires, estimates = entry(enabled)
    result = await service.polish(arguments)
    assert result['status'] == 'error' and result['error']['code'] == code and result['error']['field'] == field
    assert not (inputs or wires or estimates or result['model_called'])
    validate_final(result, expected_enabled=enabled, registry=fixture_registry)


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled', [False, True])
async def test_omitted_language_is_inferred_once_from_body_and_lint_never_calls_providers(entry, monkeypatch, enabled):
    seen = []
    def confidence(body):
        seen.append(body)
        return [SimpleNamespace(value=p, language=SimpleNamespace(iso_code_639_1=SimpleNamespace(name=lang)))
                for p, lang in [(0.9, 'EN'), (0.1, 'JA')]]
    monkeypatch.setattr('copyeditor.language_detection._detector', lambda: SimpleNamespace(compute_language_confidence_values=confidence))
    service, inputs, wires, estimates = entry(enabled)
    result = await service.lint(dict(text='Original.'))
    assert result['status'] == 'ok' and result['language'] == 'en' and seen == ['Original.']
    assert not (inputs or wires or estimates) and result['model_calls'] == 0
    validate_final(result, tool='lint_text')


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('tool', ['polish', 'lint'])
async def test_unexpected_input_failure_is_a_closed_zero_call_error(entry, monkeypatch, enabled, tool):
    def broken(*args, **kwargs): raise RuntimeError('private detail')
    monkeypatch.setattr('copyeditor.edit_service.parse_edit_request', broken)
    service, inputs, wires, estimates = entry(enabled)
    result = await getattr(service, tool)(dict(text='Original.', language='en'))
    assert result['error']['code'] == 'internal_error' and 'private detail' not in json.dumps(result)
    assert not (inputs or wires or estimates)
    validate_final(result, tool=tool + '_text', expected_enabled=enabled and tool == 'polish', registry=fixture_registry)


@pytest.mark.asyncio
async def test_cancel_is_not_converted_to_an_application_error(entry, monkeypatch):
    async def cancel(*args, **kwargs): raise asyncio.CancelledError()
    monkeypatch.setattr('copyeditor.edit_service.polish', cancel)
    with pytest.raises(asyncio.CancelledError):
        await entry()[0].polish(dict(text='Original.', language='en'))


def test_public_loader_requires_builtin_style(tmp_path):
    assert load_rules(Path('rules'), None).languages['en'].default_style
    for source in Path('rules').glob('*.md'):
        raw = '\n'.join(line for line in source.read_text().split('\n') if not line.startswith('Default style: '))
        (tmp_path / source.name).write_text(raw)
    with pytest.raises(ConfigError): load_rules(tmp_path, None)


@pytest.mark.asyncio
async def test_item_inference_uses_body_once_and_explicit_language_bypasses_it(entry, monkeypatch):
    seen = []
    def confidence(body):
        seen.append(body)
        return [SimpleNamespace(value=p, language=SimpleNamespace(iso_code_639_1=SimpleNamespace(name=lang)))
                for p, lang in [(0.9, 'EN'), (0.1, 'JA')]]
    monkeypatch.setattr('copyeditor.language_detection._detector', lambda: SimpleNamespace(compute_language_confidence_values=confidence))
    service, inputs, _, _ = entry()
    result = await service.polish(dict(items=[dict(id='a', text='Original.', context='Ignored context'),
        dict(id='b', text='Original.')], audience='Ignored audience', tone='Explicit tone.'))
    assert result['status'] == 'ok' and seen == ['Original.\nOriginal.']
    assert [i.id for i in inputs[0].items] == ['a', 'b'] and inputs[0].background.tone == 'Explicit tone.'
    result = await service.polish(dict(text='123', language='en'))
    assert result['status'] == 'ok' and seen == ['Original.\nOriginal.']
