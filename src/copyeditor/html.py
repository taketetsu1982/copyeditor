from itertools import groupby
from typing import NamedTuple
from .html_trace import trace

_WHITE_SPACE = '\t\n\v\f\r \u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000'
_HTML_NAMESPACE = 'http://www.w3.org/1999/xhtml'


class HTMLAnalysis(NamedTuple):
    accepted: bool
    signature: tuple[str | None, ...] | None


def analyze(raw: str) -> HTMLAnalysis:
    traced = trace(raw)
    if traced.input_error is not None:
        return HTMLAnalysis(False, None)
    pieces, ordinary = [], []
    end = 0

    def flush():
        if ordinary:
            text = ''.join(ordinary)
            pieces.append(None if text.strip(_WHITE_SPACE) else text)
            ordinary.clear()

    for span in traced.spans:
        if (span.start != end or not span.start < span.end <= len(raw)
                or span.kind not in ('markup', 'reference', 'text', 'raw')
                or not 0 <= span.event_index < len(traced.events)):
            return HTMLAnalysis(False, None)
        event = traced.events[span.event_index]
        # Before-context would keep implicitly closed ancestors protected.
        protected = any(node.namespace == _HTML_NAMESPACE and node.name in ('pre', 'code')
                        for node in event.after.open_elements)
        text = raw[span.start:span.end]
        if span.kind == 'text' and not protected:
            ordinary.append(text)
        else:
            flush()
            pieces.append(text)
        end = span.end
    if end != len(raw):
        return HTMLAnalysis(False, None)
    flush()
    signature = []
    for editable, run in groupby(pieces, key=lambda piece: piece is None):
        signature.extend(run if editable else (''.join(run),))
    return HTMLAnalysis(True, tuple(signature))


def same_structure(original: str, candidate: str) -> bool:
    before, after = analyze(original), analyze(candidate)
    return before.accepted and after.accepted and before.signature == after.signature
