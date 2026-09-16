from collections import deque
from functools import wraps
from typing import NamedTuple
from html5lib._tokenizer import entitiesTrie
from html5lib.constants import tokenTypes
from .html_trace_types import LexicalEvent, SpanKind


class LexicalPacket(NamedTuple):
    token: dict | None
    start: int
    end: int
    parts: tuple[tuple[int, int, SpanKind], ...]
    lexical: LexicalEvent | None


class LexicalEOF(NamedTuple):
    offset: int
    pending: tuple[int, int] | None
    input_error: str | None


class LexicalAdapter:
    def __init__(self, tokenizer, source, capture):
        self.tokenizer, self.source, self.capture = tokenizer, source, capture
        tokenizer.stream = source
        self.eof = None
        self._start, self._before, self._reference = 0, None, None
        self._metadata, self._silent = {}, deque()
        self._preludes = {}
        self._queued = set()
        self._unfinished = False
        initial = tokenizer.state.__name__
        for name in dir(tokenizer):
            if name.endswith('State'):
                setattr(tokenizer, name, self._state(getattr(tokenizer, name)))
        tokenizer.state = getattr(tokenizer, initial)
        number = tokenizer.consumeNumberEntity
        def consume_number(isHex):
            self._number = True
            return number(isHex)
        tokenizer.consumeNumberEntity = consume_number
        entity = tokenizer.consumeEntity
        @wraps(entity)
        def consume(allowedChar=None, fromAttribute=False):
            start = source.raw_offset - 1
            self._number = False
            entity(allowedChar, fromAttribute)
            if not fromAttribute:
                spelling = source.raw[start + 1:source.raw_offset]
                if self._number:
                    end = source.raw_offset
                else:
                    try:
                        end = start + 1 + len(entitiesTrie.longest_prefix(spelling))
                    except KeyError:
                        end = start
                if end > start:
                    self._reference = (start, end)
        tokenizer.consumeEntity = consume
        self._iterator = self._iterate()

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._iterator)

    def _state(self, method):
        @wraps(method)
        def run():
            if self._before is None:
                self._before = self.capture()
                self._kind = 'raw' if method.__name__.startswith(('scriptData', 'rawtext', 'plaintext')) else 'text'
            result = method()
            tokens = list(self.tokenizer.tokenQueue)
            self._queued.update(id(t) for t in tokens)
            end = self.source.raw_offset
            if tokens and self._silent:
                self._preludes[id(tokens[0])] = tuple(self._silent)
                self._silent.clear()
            real = [t for t in tokens if t['type'] != tokenTypes['ParseError']]
            errors = [t['data'] for t in tokens if t['type'] == tokenTypes['ParseError']]
            self._unfinished |= any('eof' in e.lower() for e in errors) and ('attribute' in method.__name__.lower() or method.__name__ in ('tagNameState', 'selfClosingStartTagState'))
            silent = method.__name__ == 'cdataSectionState' or 'expected-closing-tag-but-got-right-bracket' in errors
            if real or silent:
                kind = 'markup' if silent or (real and real[0]['type'] in (0, 3, 4, 5, 6)) else self._kind
                parts = [(self._start, end, kind)]
                if self._reference and kind == 'text':
                    a, b = self._reference
                    parts = [(self._start, a, kind), (a, b, 'reference'), (b, end, kind)]
                parts = tuple(p for p in parts if p[0] < p[1])
                if real:
                    for index, token in enumerate(real):
                        self._metadata[id(token)] = LexicalPacket(token, self._start, end, parts if index == 0 else (), None)
                else:
                    event = LexicalEvent(self._start, end, kind, self._before, self.capture())
                    self._silent.append(LexicalPacket(None, self._start, end, parts, event))
                self._start, self._before, self._reference = end, None, None
            return result
        return run

    def _iterate(self):
        for token in self.tokenizer:
            if id(token) not in self._queued and self._preludes:
                yield from self._preludes.pop(next(iter(self._preludes)))
            self._queued.discard(id(token))
            yield from self._preludes.pop(id(token), ())
            if token['type'] == tokenTypes['ParseError']:
                offset = self.source.raw_offset
                yield LexicalPacket(token, offset, offset, (), None)
                continue
            yield self._metadata.pop(id(token))
        while self._silent:
            yield self._silent.popleft()
        pending = (self._start, len(self.source.raw)) if self._start < len(self.source.raw) else None
        error = 'nul' if '\0' in self.source.raw else 'unfinished_tag' if self._unfinished else 'unclassified' if pending else None
        self.eof = LexicalEOF(len(self.source.raw), pending, error)
