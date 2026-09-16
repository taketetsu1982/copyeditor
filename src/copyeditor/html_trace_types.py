from typing import Literal, NamedTuple

SpanKind = Literal["markup", "reference", "text", "raw"]


class NodeRef(NamedTuple):
    uid: int
    namespace: str
    name: str


class Context(NamedTuple):
    state: str
    open_elements: tuple[NodeRef, ...]


class TokenEvent(NamedTuple):
    start: int
    end: int
    kind: Literal["start_tag", "end_tag", "comment", "doctype", "characters", "space_characters", "eof"]
    before: Context
    after: Context


class LexicalEvent(NamedTuple):
    start: int
    end: int
    kind: SpanKind
    before: Context
    after: Context


class SourceSpan(NamedTuple):
    start: int
    end: int
    kind: SpanKind
    event_index: int


class HTMLTrace(NamedTuple):
    raw: str
    spans: tuple[SourceSpan, ...]
    events: tuple[TokenEvent | LexicalEvent, ...]
    input_error: Literal["nul", "unfinished_tag", "unclassified", "incomplete_coverage"] | None


class HTMLTraceError(Exception):
    def __init__(self):
        super().__init__("internal_error")

    @property
    def code(self):
        return "internal_error"
