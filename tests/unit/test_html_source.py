import pytest
from html5lib.constants import EOF
from copyeditor.html_source import TracedInputStream
from copyeditor.html_trace_types import Context, HTMLTrace, HTMLTraceError, LexicalEvent, NodeRef, SourceSpan, TokenEvent


@pytest.mark.parametrize("raw,text,bounds", [
    ("", "", [0]), ("a\rb\nc", "a\nb\nc", [0, 1, 2, 3, 4, 5]),
    ("a\r\nb", "a\nb", [0, 1, 3, 4]), ("\r\r\n", "\n\n", [0, 1, 3]),
    ("😀\r\nx", "😀\nx", [0, 1, 3, 4]),
])
@pytest.mark.parametrize("chunk", [2, 3, 10240])
def test_ctr02_source_boundaries(raw, text, bounds, chunk):
    stream = TracedInputStream(raw)
    stream._defaultChunkSize = chunk
    assert stream.raw == raw and stream.dataStream.getvalue() == raw
    for index, char in enumerate(text):
        assert (stream.cursor, stream.raw_offset) == (index, bounds[index])
        assert stream.char() == char
        stream.unget(char)
        assert (stream.cursor, stream.raw_offset) == (index, bounds[index])
        assert stream.char() == char
    assert stream.char() is EOF
    stream.unget(EOF)
    assert (stream.cursor, stream.raw_offset) == (len(text), len(raw))
    assert [stream.offset_at(i) for i in range(len(bounds))] == bounds
    for invalid in [-1, len(bounds)]:
        with pytest.raises(ValueError):
            stream.offset_at(invalid)
    for name in ["raw", "cursor", "raw_offset"]:
        with pytest.raises(AttributeError):
            setattr(stream, name, 0)


def test_ctr02_source_bulk_and_chunk_start_unget():
    stream, other = TracedInputStream("ab\r\ncd!"), TracedInputStream("x")
    stream._defaultChunkSize = 2
    assert stream.charsUntil("!") == "ab\ncd"
    assert (stream.cursor, stream.raw_offset, stream.position()) == (5, 6, (2, 2))
    assert stream.char() == "!" and stream.char() is EOF
    assert stream.chunkOffset == 0
    stream.unget("!")
    assert (stream.cursor, stream.raw_offset) == (5, 6)
    assert stream.charsUntil("!", opposite=True) == "!"
    assert (stream.cursor, stream.raw_offset) == (6, 7)
    assert other.cursor == 0 and other.char() == "x"
    assert isinstance(stream.errors, list)


def test_ctr02_trace_records_are_immutable():
    node = NodeRef(0, "http://www.w3.org/1999/xhtml", "svg")
    context = Context("dataState", (node,))
    lexical = LexicalEvent(5, 17, "markup", context, context)
    token = TokenEvent(17, 23, "end_tag", context, context)
    span = SourceSpan(5, 17, "markup", 0)
    trace = HTMLTrace("<svg><![CDATA[]]></svg>", (span,), (lexical, token), None)
    assert trace.events[trace.spans[0].event_index] is lexical
    assert isinstance(trace.events[1], TokenEvent)
    for record in [node, context, lexical, token, span, trace]:
        with pytest.raises(AttributeError):
            setattr(record, record._fields[0], None)
    error = HTMLTraceError()
    assert error.code == str(error) == "internal_error"


def test_ctr02_bulk_crlf_reconsumption():
    stream = TracedInputStream("ab\r\n")
    stream._defaultChunkSize = 3
    assert stream.charsUntil("!") == "ab\n"
    stream.unget("\n")
    assert (stream.cursor, stream.raw_offset) == (2, 2)
    assert stream.char() == "\n"
    assert (stream.cursor, stream.raw_offset) == (3, 4)
