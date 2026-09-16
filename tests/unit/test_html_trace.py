import pytest
import copyeditor.html_trace as module
from copyeditor.html_trace_types import HTMLTraceError, LexicalEvent


@pytest.mark.parametrize('raw,error', [
    ('', None), ('a\r\nb&notit;', None), ('<tr><td>x</td></tr>', None),
    ('<svg><![CDATA[]]></svg>', None), ('</>', None), ('<!--x', None), ('<', None),
    ('<p x="', 'unfinished_tag'), ('\0', 'nul'), ('<script>x', None),
    ('<textarea>a</x>&amp;</textarea>', None),
])
@pytest.mark.parametrize('chunk', [2, 10240])
def test_ctr02_trace_coverage_and_eof(raw, error, chunk, monkeypatch):
    monkeypatch.setattr(module.TracedInputStream, '_defaultChunkSize', chunk)
    result = module.trace(raw)
    assert result.input_error == error
    assert result.events[-1].kind == 'eof'
    assert (result.events[-1].start, result.events[-1].end) == (len(raw), len(raw))
    assert all(0 <= span.event_index < len(result.events) for span in result.spans)
    if error is None:
        assert ''.join(raw[s.start:s.end] for s in result.spans) == raw
        starts = [0] + [s.end for s in result.spans[:-1]] if raw else []
        assert [s.start for s in result.spans] == starts
    assert module.trace(raw) == result


def test_ctr02_trace_context_identity_and_lexical_events():
    result = module.trace('<pre><code>x</code>y</pre>')
    text = [e for e in result.events if e.kind == 'characters']
    assert [n.name for n in text[0].before.open_elements][-2:] == ['pre', 'code']
    assert text[0].before == text[0].after
    assert text[0].before.open_elements[-2].uid == text[1].before.open_elements[-1].uid
    assert text[0].before.open_elements[-1].uid != text[1].before.open_elements[-1].uid
    result = module.trace('<svg><![CDATA[]]></svg>')
    lexical = next(e for e in result.events if isinstance(e, LexicalEvent))
    assert (lexical.start, lexical.end) == (5, 17)
    result = module.trace('<script>x')
    assert result.spans[-1].kind == 'raw'
    assert result.events[-1].before.open_elements[-1].name == 'script'
    assert result.events[-1].after.open_elements[-1].name != 'script'
    result = module.trace('&notit;')
    assert [(result.raw[s.start:s.end], s.kind) for s in result.spans] == [('&not', 'reference'), ('i', 'text'), ('t;', 'text')]


@pytest.mark.parametrize('fault,expected', [('drop', 'incomplete_coverage'), ('duplicate', 'incomplete_coverage'), ('unknown', 'unclassified'), ('raise', 'internal_error')])
def test_ctr02_trace_adapter_failures(fault, expected, monkeypatch):
    original = module.LexicalAdapter
    class Broken(original):
        def __next__(self):
            packet = super().__next__()
            if packet.parts:
                if fault == 'raise':
                    raise RuntimeError('private sentinel')
                a, b, kind = packet.parts[0]
                parts = () if fault == 'drop' else packet.parts * 2 if fault == 'duplicate' else ((a, b, 'unknown'),)
                return packet._replace(parts=parts)
            return packet
    monkeypatch.setattr(module, 'LexicalAdapter', Broken)
    if fault == 'raise':
        with pytest.raises(HTMLTraceError) as caught:
            module.trace('x')
        assert str(caught.value) == caught.value.code == expected
        assert caught.value.__cause__ is None and caught.value.__suppress_context__
    else:
        assert module.trace('x').input_error == expected
        assert module.trace('\0').input_error == 'nul'


def test_ctr02_trace_chunk_independence_and_reconstructed_nodes(monkeypatch):
    raw = '<pre>a\r\n<code>b</pre>c</code>'
    expected = module.trace(raw)
    monkeypatch.setattr(module.TracedInputStream, '_defaultChunkSize', 2)
    assert module.trace(raw) == expected
    events = [event for event in expected.events if event.kind == 'characters']
    first_code = next(n for n in events[1].after.open_elements if n.name == 'code')
    new_code = next(n for n in events[2].after.open_elements if n.name == 'code')
    assert first_code.uid != new_code.uid
    assert not any(n.name == 'pre' for n in events[2].before.open_elements)
