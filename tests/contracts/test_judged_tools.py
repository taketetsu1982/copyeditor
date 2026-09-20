"""Public tool fixtures establish control behavior, not live model quality."""
import json

import pytest
from fastmcp import Client
from mcp.shared.exceptions import MCPError
from jsonschema import Draft202012Validator

from copyeditor.judged_response import judged_output_schema
from copyeditor.responses import tool_output_schema
from tests.integration.test_judgment_transport import BODY, MARKER, ROOT, setup
from .harness import ProviderQueue, assert_subset, load_cases


@pytest.mark.asyncio
@pytest.mark.parametrize('case', load_cases(ROOT / 'contracts/tools.md', 'contract-case'), ids=lambda c: c['name'])
async def test_legacy_cases_through_public_tool(setup, case):
    make, calls, records = setup
    queue = ProviderQueue(case['provider'])
    make.state.update(enabled=False, queue=queue)
    server, _, _ = make()
    async with Client(server) as client:
        result = await client.call_tool(case['tool'], case['input'], raise_on_error=False)
    payload = result.structured_content
    Draft202012Validator(tool_output_schema(case['tool'])).validate(payload)
    assert_subset(payload, case['expect'])
    queue.assert_exhausted()
    assert len(calls) == len(case['provider']) and len(records) == 1
    assert not make.state['wires']


@pytest.mark.asyncio
@pytest.mark.parametrize('degree', ['polish', 'rewrite'])
@pytest.mark.parametrize('scenario', ['keep', 'pass', 'changed', 'mixed', 'no_issue', 'retry', 'unfixable', 'rejected', 'error', 'invalid', 'cancel', 'empty_html'])
async def test_judgment_control_cases_through_public_tool(setup, degree, scenario):
    make, calls, records = setup
    make.state['scenario'] = scenario
    server, _, _ = make()
    arguments = dict(items=[dict(id='a', text=BODY), dict(id='b', text=BODY)], degree=degree)
    if scenario == 'empty_html': arguments = dict(text='<script>private()</script>', format='html', degree=degree)
    async with Client(server) as client:
        if scenario == 'cancel':
            with pytest.raises(MCPError, match='Connection closed'):
                await client.call_tool('polish_text', arguments, raise_on_error=False)
            assert len(calls) == len(make.state['wires']) == len(records) == 1
            return
        result = await client.call_tool('polish_text', arguments, raise_on_error=False)
    payload, wires = result.structured_content, make.state['wires']
    Draft202012Validator(judged_output_schema()).validate(payload)
    assert len(records) == 1
    if scenario in ('error', 'invalid'):
        assert result.is_error and not {'items', 'text', 'diagnosis', 'detection', 'verification'} & payload.keys()
        assert payload['error']['code'] == {'error': 'provider_error', 'invalid': 'invalid_response'}[scenario]
        assert MARKER not in json.dumps(payload)
        assert len(wires) == 2
        assert payload['model_called'] and payload['providers'][0]['model_calls'] >= 1
        return
    assert not result.is_error
    items = payload.get('items', [payload])
    if 'items' in payload: assert [i['id'] for i in items] == ['a', 'b']
    else: assert payload['text'] == arguments['text']
    if scenario in ('keep', 'empty_html'):
        assert not calls and len(wires) == (scenario == 'keep')
        assert all(i['detection']['status'] == ('insufficient' if scenario == 'keep' else 'not_run') for i in items)
    elif scenario == 'mixed':
        assert items[0]['detection']['status'] == 'eligible' and items[1]['detection']['status'] == 'insufficient'
        assert items[1]['diagnosis'] is None and items[1]['flag'] is None
    elif scenario == 'no_issue' and degree == 'rewrite':
        assert len(calls) == 1 and len(wires) == 1
        assert all(i['diagnosis']['status'] == 'no_issue' and i['text'] == BODY for i in items)
    elif scenario in ('unfixable', 'rejected'):
        assert len(wires) == 1 and all(i['flag']['kind'] == scenario and i['text'] == BODY for i in items)
        assert all(i['verification']['status'] == 'not_run' for i in items)
    else:
        assert len(wires) == 2 and all(i['verification']['status'] == 'pass' and i['flag'] is None for i in items)
        assert all(i['text'] == (BODY.replace('Example', 'Sample') if scenario == 'changed' else BODY) for i in items)
        if scenario == 'retry' and degree == 'polish':
            assert len(calls) == 2 and all(i['regenerated'] for i in items)
            assert all(pair['candidate'] == BODY for pair in wires[-1]['state']['pairs'].values())


@pytest.mark.asyncio
@pytest.mark.parametrize('axis', ['meaning', 'scope', 'natural', 'achieved'])
@pytest.mark.parametrize('scenario', ['fail', 'middle'])
@pytest.mark.parametrize('degree', ['polish', 'rewrite'])
async def test_each_verification_rejection_preserves_original(setup, axis, scenario, degree):
    make, calls, records = setup
    make.state.update(scenario=scenario, axis=axis)
    async with Client(make()[0]) as client:
        result = await client.call_tool('polish_text', dict(text=BODY, degree=degree))
    payload = result.structured_content
    assert payload['text'] == BODY and payload['flag']['kind'] == 'verification_rejected'
    assert payload['verification']['status'] == ('fail' if scenario == 'fail' else 'indeterminate')
    assert payload['flag']['checks'] == [axis]
    assert len(calls) == (2 if degree == 'rewrite' else 1) and len(make.state['wires']) == 2
    assert records[0]['rejected_count'] == 1
