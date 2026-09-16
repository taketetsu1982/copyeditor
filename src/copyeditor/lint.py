import re
from typing import NamedTuple
import re2
from .config import ConfigError
class Finding(NamedTuple):
    rule_id: str
    start: int
    end: int
    matched: str
    matched_truncated: bool
    message: str
class LintError(RuntimeError): code = "internal_error"
def compile_rule(rule):
    try:
        if rule["detector"]["kind"] != "regex": return rule
        pattern, inside, quoted, i = rule["detector"]["pattern"], False, False, 0
        while i < len(pattern):
            if pattern[i] == "\\":
                quoted = True if pattern[i:i+2] == r"\Q" else False if pattern[i:i+2] == r"\E" else quoted
                i += 2
                continue
            if not quoted:
                if inside and pattern[i:i+2] == "[:":
                    i = pattern.index(":]", i) + 2
                    continue
                flag = re.match(r"\(\?([A-Za-z-]+)[:)]", pattern[i:]) if not inside else None
                if flag and set(flag[1]) - set("ims-"): raise ValueError()
                inside = True if pattern[i] == "[" else False if pattern[i] == "]" else inside
            i += 1
        options = re2.Options()
        options.log_errors = False
        return dict(rule, compiled=re2.compile(pattern, options=options))
    except Exception:
        raise ConfigError("invalid_rules", "rules") from None
def sentences(text, terminators):
    start = 0
    for i, char in enumerate(text):
        if char in terminators or char == "\n" or i == len(text) - 1:
            end = i + 1
            while start < end and text[start].isspace(): start += 1
            while end > start and text[end - 1].isspace(): end -= 1
            if start < end: yield start, end
            start = i + 1
def spans(text, rule):
    d, kind = rule["detector"], rule["detector"]["kind"]
    if kind == "literal":
        start = 0
        while (start := text.find(d["value"], start)) >= 0:
            yield start, start + len(d["value"])
            start += len(d["value"])
    elif kind == "regex":
        yield from ((m.start(), m.end()) for m in rule["compiled"].finditer(text) if m.start() < m.end())
    elif kind == "brackets":
        pairs, stack = dict(d["pairs"]), []
        for i, char in enumerate(text):
            if char in pairs: stack.append((char, i))
            elif char in pairs.values():
                if stack and pairs[stack[-1][0]] == char: stack.pop()
                else: yield i, i + 1
        yield from ((i, i + 1) for _, i in stack)
    elif kind == "repeated_ending":
        last, count, first, end = None, 0, 0, 0
        for a, b in [*sentences(text, d["terminators"]), (len(text), len(text))]:
            piece = text[a:b]
            piece = piece[:-1].rstrip() if piece and piece[-1] in d["terminators"] + "\n" else piece
            ending = next((e for e in sorted(d["endings"], key=lambda e: (-len(e), e)) if piece.endswith(e)), None)
            if ending != last or ending is None:
                if count >= d["min_run"]: yield first, end
                first, count = a, 0
            last, count, end = ending, count + 1 if ending is not None else 0, b
    else:
        for a, b in sentences(text, d.get("terminators", "\n")):
            piece = text[a:b]
            if ((kind == "sentence_length" and b - a > d["max"]) or (kind == "comma_count" and sum(c in d["commas"] for c in piece) > d["max"])
                    or (kind == "width_mix" and any(c in d["half"] for c in piece) and any(c in d["full"] for c in piece))):
                yield a, b
def lint(text, rules):
    try:
        descriptions = {r["id"]: r["description"] for r in rules.detectors}
        found = sorted({(a, b, r["id"]) for r in rules.detectors for a, b in spans(text, r)})
        return tuple(Finding(id, a, b, text[a:min(b, a + 160)], b - a > 160, descriptions[id]) for a, b, id in found)
    except Exception:
        raise LintError("internal_error") from None
def lint_response(text, rules):
    found = lint(text, rules)
    return dict(findings=[f._asdict() for f in found[:100]], findings_truncated=len(found) > 100)
