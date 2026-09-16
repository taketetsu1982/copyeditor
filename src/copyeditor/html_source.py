from html5lib._inputstream import HTMLUnicodeInputStream
from html5lib.constants import EOF


class TracedInputStream(HTMLUnicodeInputStream):
    def __init__(self, raw: str):
        self._raw = raw
        self._cursor = 0
        self._boundaries = [0]
        for index, char in enumerate(raw):
            if char == "\n" and index and raw[index - 1] == "\r":
                self._boundaries[-1] = index + 1
            else:
                self._boundaries.append(index + 1)
        # Pre-normalizing the parser input would erase source spelling.
        super().__init__(raw)

    @property
    def raw(self) -> str:
        return self._raw

    @property
    def cursor(self) -> int:
        return self._cursor

    @property
    def raw_offset(self) -> int:
        return self.offset_at(self.cursor)

    def offset_at(self, cursor: int) -> int:
        if not 0 <= cursor < len(self._boundaries):
            raise ValueError("Normalized offset out of range")
        return self._boundaries[cursor]

    def char(self):
        char = super().char()
        self._cursor += char is not EOF
        return char

    def charsUntil(self, characters, opposite=False):
        chars = super().charsUntil(characters, opposite)
        self._cursor += len(chars)
        return chars

    def unget(self, char):
        super().unget(char)
        self._cursor -= char is not EOF
