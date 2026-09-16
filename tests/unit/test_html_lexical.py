import ast
import inspect
from html5lib._tokenizer import HTMLTokenizer
import pytest
from html5lib.html5parser import HTMLParser
from copyeditor.html_lexical import LexicalAdapter
from copyeditor.html_source import TracedInputStream
from copyeditor.html_trace_types import Context, NodeRef


class Bridge:
    def __init__(self, adapter, packets):
        object.__setattr__(self, 'adapter', adapter)
        object.__setattr__(self, 'packets', packets)

    def __getattr__(self, name):
        return getattr(self.adapter.tokenizer, name)

    def __setattr__(self, name, value):
        setattr(self.adapter.tokenizer, name, value)

    def __iter__(self):
        for packet in self.adapter:
            self.packets.append(packet)
            if packet.token is not None:
                yield packet.token


class Parser(HTMLParser):
    def reset(self):
        super().reset()
        source = TracedInputStream(self.raw)
        source._defaultChunkSize = self.chunk
        def capture():
            return Context(self.tokenizer.state.__name__, tuple(NodeRef(id(n), n.namespace, n.name) for n in self.tree.openElements))
        self.packets = []
        self.adapter = LexicalAdapter(self.tokenizer, source, capture)
        self.tokenizer = Bridge(self.adapter, self.packets)


def parse(raw, chunk=10240):
    parser = Parser(strict=False, namespaceHTMLElements=True)
    parser.raw, parser.chunk = raw, chunk
    parser.parse(raw, scripting=True)
    return parser


@pytest.mark.parametrize('raw,expected,error', [
    ('a\r\nb', [('a\r\nb', 'text')], None),
    ('&notit;', [('&not', 'reference'), ('i', 'text'), ('t;', 'text')], None),
    ('&#x80;!', [('&#x80;', 'reference'), ('!', 'text')], None),
    ('&#x;', [('&#x', 'text'), (';', 'text')], None),
    ('<p X="1" X="2">', [('<p X="1" X="2">', 'markup')], None),
    ('<tr>x</tr>', [('<tr>', 'markup'), ('x', 'text'), ('</tr>', 'markup')], None),
    ('<svg><![CDATA[]]></svg>', [('<svg>', 'markup'), ('<![CDATA[]]>', 'markup'), ('</svg>', 'markup')], None),
    ('<svg><![CDATA[x]]></svg>', [('<svg>', 'markup'), ('<![CDATA[x]]>', 'markup'), ('</svg>', 'markup')], None),
    ('<!DOCTYPE html>', [('<!DOCTYPE html>', 'markup')], None),
    ('</>', [('</>', 'markup')], None), ('<?x>', [('<?x>', 'markup')], None),
    ('<!--x', [('<!--x', 'markup')], None), ('<', [('<', 'text')], None),
    ('<p x="', [], 'unfinished_tag'), ('\0', [('\0', 'text')], 'nul'),
    ('<script>a</x>', [('<script>', 'markup'), ('a', 'raw'), ('</x', 'raw'), ('>', 'raw')], None),
    ('<textarea>a</x>', [('<textarea>', 'markup'), ('a', 'text'), ('</x', 'text'), ('>', 'text')], None),
])
@pytest.mark.parametrize('chunk', [2, 10240])
def test_ctr02_lexical_source_parts(raw, expected, error, chunk):
    parser = parse(raw, chunk)
    actual = [(raw[a:b], kind) for packet in parser.packets for a, b, kind in packet.parts]
    assert actual == expected
    assert parser.adapter.eof.input_error == error
    assert parser.adapter.eof.offset == len(raw)
    assert parser.adapter.eof.pending == ((0, len(raw)) if error == 'unfinished_tag' else None)
    assert list(parser.adapter) == []


def test_ctr02_lexical_context_and_isolation():
    parser = parse('<svg><![CDATA[]]></svg>')
    packet = next(p for p in parser.packets if p.token is None)
    assert (packet.start, packet.end) == (5, 17)
    assert packet.lexical.before.open_elements[-1].name == 'svg'
    assert packet.lexical.after.open_elements == packet.lexical.before.open_elements
    assert packet.lexical.before.state == packet.lexical.after.state == 'dataState'
    assert parse('x').adapter.eof.input_error is None
    with pytest.raises(AttributeError):
        packet.start = 0


def test_ctr02_lexical_notification_order_and_eof():
    parser = parse('</><?x>')
    assert [p.token['type'] if p.token is not None else 'lexical' for p in parser.packets] == [7, 'lexical', 7, 6]
    assert parse('<p/').adapter.eof.input_error == 'unfinished_tag'
    assert parse('&#;').adapter.eof.input_error is None
    assert parse('<script>x</script>y').packets[-1].parts[-1][2] == 'text'


def test_ctr02_lexical_precedes_next_chunk_stream_error():
    packets = parse('</>\v', 3).packets
    assert packets[0].token['data'] == 'expected-closing-tag-but-got-right-bracket'
    assert packets[1].lexical is not None
    assert packets[2].token['data'] == 'invalid-codepoint'


@pytest.mark.parametrize('tag', ['title', 'textarea'])
@pytest.mark.parametrize('chunk', [2, 10240])
@pytest.mark.parametrize('body,references,text', [
    ('&notit;', ['&not'], 'it;'), ('&#x80;', ['&#x80;'], ''),
    ('&bogus;', [], '&bogus;'), ('a</x>&amp;', ['&amp;'], 'a</x>'),
])
def test_ctr02_rcdata_reference_spans(tag, chunk, body, references, text):
    raw = f'<{tag}>{body}</{tag}>'
    parser = parse(raw, chunk)
    parts = [part for packet in parser.packets for part in packet.parts]
    assert parser.adapter.eof.input_error is None
    assert ''.join(raw[a:b] for a, b, kind in parts) == raw
    assert [raw[a:b] for a, b, kind in parts if kind == 'reference'] == references
    assert ''.join(raw[a:b] for a, b, kind in parts if kind == 'text') == text


def test_ctr02_all_fixed_tokenizer_state_targets_are_instrumented():
    tree = ast.parse(inspect.getsource(HTMLTokenizer))
    targets = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
            and target.value.id == 'self' and target.attr == 'state'
            for target in node.targets
        ):
            assert isinstance(node.value, ast.Attribute)
            assert isinstance(node.value.value, ast.Name) and node.value.value.id == 'self'
            targets.add(node.value.attr)
    tokenizer = parse('<p>x</p>').adapter.tokenizer
    assert targets
    for name in targets:
        wrapped = getattr(tokenizer, name).__wrapped__
        assert wrapped.__self__ is tokenizer
        assert wrapped.__func__ is getattr(HTMLTokenizer, name)
