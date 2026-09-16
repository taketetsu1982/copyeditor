from html5lib.html5parser import HTMLParser
from .html_lexical import LexicalAdapter
from .html_source import TracedInputStream
from .html_trace_types import Context, HTMLTrace, HTMLTraceError, NodeRef, SourceSpan, TokenEvent

_KINDS = {0: 'doctype', 1: 'characters', 2: 'space_characters', 3: 'start_tag', 4: 'end_tag', 5: 'start_tag', 6: 'comment'}


class _Proxy:
    def __init__(self, parser, adapter):
        object.__setattr__(self, 'parser', parser)
        object.__setattr__(self, 'adapter', adapter)

    def __getattr__(self, name):
        return getattr(self.adapter.tokenizer, name)

    def __setattr__(self, name, value):
        setattr(self.adapter.tokenizer, name, value)

    def __iter__(self):
        parser = self.parser
        for packet in self.adapter:
            if packet.token is not None:
                if packet.token['type'] == 7:
                    yield packet.token
                    continue
                kind, before = _KINDS[packet.token['type']], parser.capture()
                yield packet.token
                event = TokenEvent(packet.start, packet.end, kind, before, parser.capture())
            else:
                event = packet.lexical
            index = len(parser.events)
            parser.events.append(event)
            parser.spans.extend(SourceSpan(a, b, kind, index) for a, b, kind in packet.parts)
        parser.eof_before = parser.capture()


class _Parser(HTMLParser):
    def __init__(self, raw):
        self.raw = raw
        super().__init__(strict=False, namespaceHTMLElements=True)

    def reset(self):
        super().reset()
        self.nodes, self.events, self.spans = {}, [], []
        self.adapter = LexicalAdapter(self.tokenizer, TracedInputStream(self.raw), self.capture)
        self.tokenizer = _Proxy(self, self.adapter)

    def capture(self):
        refs = []
        for node in self.tree.openElements:
            key = id(node)
            if key not in self.nodes:
                # Keeping the node alive prevents recycled object IDs from aliasing nodes.
                self.nodes[key] = (node, len(self.nodes))
            refs.append(NodeRef(self.nodes[key][1], node.namespace, node.name))
        return Context(self.tokenizer.state.__name__, tuple(refs))


def trace(raw: str) -> HTMLTrace:
    try:
        parser = _Parser(raw)
        parser.parse(raw, scripting=True)
        parser.events.append(TokenEvent(len(raw), len(raw), 'eof', parser.eof_before, parser.capture()))
        spans = tuple(sorted(parser.spans, key=lambda span: span.start))
        error, end = parser.adapter.eof.input_error, 0
        coverage_error = None
        for span in spans:
            if span.kind not in ('markup', 'reference', 'text', 'raw'):
                coverage_error = 'unclassified'
            elif coverage_error is None and (span.start != end or not span.start < span.end <= len(raw) or not 0 <= span.event_index < len(parser.events)):
                coverage_error = 'incomplete_coverage'
            end = span.end
        if end != len(raw) and coverage_error is None:
            coverage_error = 'incomplete_coverage'
        return HTMLTrace(raw, spans, tuple(parser.events), error or coverage_error)
    except Exception:
        raise HTMLTraceError() from None
