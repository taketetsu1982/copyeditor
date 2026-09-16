from pathlib import Path
import pytest
import copyeditor.html as html
from copyeditor.html_source import TracedInputStream
from copyeditor.html_trace_types import HTMLTraceError
from tests.contracts.harness import load_cases

CASES = load_cases(Path(__file__).resolve().parents[2] / 'rules/common.md', 'html-case')


@pytest.mark.consumer('CTR-02')
@pytest.mark.parametrize('case', CASES, ids=lambda case: case['name'])
def test_ctr02_html_contract(case):
    assert len(CASES) == 28
    original, candidate = case['original'], case['candidate']
    actual = {'original_accepted': html.analyze(original).accepted,
              'candidate_accepted': None if candidate is None else html.analyze(candidate).accepted,
              'same_structure': None if candidate is None else html.same_structure(original, candidate)}
    assert actual == case['expect']


@pytest.mark.parametrize('original,candidate,same', [
    ('<pre>\r\nx</pre>', '<pre>\nx</pre>', False),
    ('<pre><b>x</b></pre>', '<pre><b>y</b></pre>', False),
    ('<pre>a<div>b</pre>c', '<pre>a<div>b</pre>d', True),
    ('<code>a', '<code>b', False), ('<style>a', '<style>b', False),
    ('<textarea>a &amp;', '<textarea>b &amp;', True),
    ('<tr><td>x</td></tr>', '<tr><td>y</td></tr>', True),
    ('<p> \r\n </p>', '<p> \n </p>', False),
    ('<p> a\r\nb </p>', '<p> revised </p>', True),
    ('<p>\x1c</p>', '<p>x</p>', True),
    ('<svg><![CDATA[]]></svg>', '<svg></svg>', False),
    ('</><p>a</p>', '</><p>b</p>', True),
    ('<script>x</script><p>a', '<script>x</script><p>b', True),
])
def test_ctr02_html_protected_and_editable_boundaries(original, candidate, same):
    assert html.same_structure(original, candidate) is same


@pytest.mark.parametrize('space', ['\t', '\r\n', '\u0085', '\u00a0', '\u1680', '\u2007', '\u2028', '\u202f', '\u205f', '\u3000'])
def test_ctr02_html_explicit_whitespace(space):
    assert html.analyze('<p>' + space + '</p>').signature == ('<p>' + space + '</p>',)


@pytest.mark.parametrize('raw', ['a\r\nb &notit; c', '<pre><code>x</pre>y</code>', '<textarea>a</x>&amp;</textarea>', '<p> a < b &bogus; z</p>'])
def test_ctr02_html_signature_ignores_buffering(raw, monkeypatch):
    expected = html.analyze(raw)
    monkeypatch.setattr(TracedInputStream, '_defaultChunkSize', 2)
    assert html.analyze(raw) == expected


@pytest.mark.parametrize('fault', ['missing', 'unknown', 'rejected', 'exception'])
def test_ctr02_html_trace_failures(fault, monkeypatch):
    result = html.trace('x')
    def broken(raw):
        if fault == 'exception':
            raise HTMLTraceError()
        if fault == 'rejected':
            return result._replace(input_error='incomplete_coverage')
        spans = () if fault == 'missing' else (result.spans[0]._replace(kind='unknown'),)
        return result._replace(spans=spans)
    monkeypatch.setattr(html, 'trace', broken)
    if fault == 'exception':
        with pytest.raises(HTMLTraceError, match='internal_error'):
            html.same_structure('x', 'y')
    else:
        assert html.analyze('x') == html.HTMLAnalysis(False, None)
        assert not html.same_structure('x', 'y')


def test_ctr02_html_empty_and_immutable_analysis():
    result = html.analyze('')
    assert result == html.HTMLAnalysis(True, ())
    assert not html.same_structure('', 'x')
    with pytest.raises(AttributeError):
        result.accepted = False


def test_ctr02_html_signature_keeps_lexical_boundaries():
    assert html.analyze('<p>A &amp; B</p>').signature == ('<p>', None, '&amp;', None, '</p>')
    assert html.analyze('&notit;').signature == ('&not', None)
    assert html.analyze('2 < 3 &bogus;').signature == (None,)
    assert not html.same_structure('<pre><code>x</pre>y</code>', '<pre><code>x</pre>z</code>')
