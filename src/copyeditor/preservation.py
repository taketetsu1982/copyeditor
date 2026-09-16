import re
from collections import Counter
from decimal import Decimal, MAX_EMAX, MIN_EMIN, localcontext
from typing import NamedTuple

WHITE_SPACE = "\u0009\u000a\u000b\u000c\u000d\u0020\u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
NUMBERS = re.compile(r"[+\-＋－−]?\d+(?:[.,/:\-．，／：－]\d+)*[%％]?")
URLS = re.compile(r"https?://[^" + re.escape(WHITE_SPACE + '<>"\'`') + r"]+")
URL_TRAILING = ".,;:!?)]}。、！？）」』"
NAME = r"[A-Za-z_][A-Za-z0-9_.-]*"
VARIABLES = re.compile(r"\$\{" + NAME + r"\}|\{\{" + NAME + r"\}\}|\{" + NAME + r"\}|%\(" + NAME + r"\)[sdf]|%[sdf]")

class CheckResult(NamedTuple):
    failed: tuple[str, ...]
    matched_terms: tuple[str, ...]
    html_valid: bool | None

def occurrences(text, term):
    if not term:
        raise ValueError("Protected terms must be nonempty")
    count, start = 0, 0
    while (start := text.find(term, start)) >= 0:
        count += 1
        start += 1
    return count

def matched_terms(original, effective_terms):
    return tuple(term for term in sorted(set(effective_terms)) if occurrences(original, term))

def request_terms(items, effective_terms):
    terms = tuple(effective_terms)
    return tuple(sorted({term for item in items for term in matched_terms(item.text, terms)}))

def number_tokens(text):
    return Counter(NUMBERS.findall(text))

def url_tokens(text):
    return Counter(token.rstrip(URL_TRAILING) for token in URLS.findall(text))

def variable_tokens(text):
    tokens, position = Counter(), 0
    while position < len(text):
        if text.startswith("%%", position):
            position += 2
            continue
        token = VARIABLES.match(text, position)
        if token:
            tokens[token[0]] += 1
            position = token.end()
        else:
            position += 1
    return tokens

def within_ratio(original_length, candidate_length, ratio):
    low, high = (Decimal(str(ratio[key])) for key in ("min", "max"))
    precision = max(len(value.as_tuple().digits) for value in (low, high)) + len(str(original_length))
    with localcontext(prec=precision, Emax=MAX_EMAX, Emin=MIN_EMIN):
        return low * original_length <= candidate_length <= high * original_length

def check(original, candidate, effective_terms, ratio, format="text"):
    if format not in ("text", "markdown"):
        raise ValueError("HTML structure checking requires its separate consumer")
    if not original:
        raise ValueError("Original text must be nonempty")
    terms = matched_terms(original, effective_terms)
    checks = (
        ("protected_terms", all(occurrences(original, term) == occurrences(candidate, term) for term in terms)),
        ("numbers", number_tokens(original) == number_tokens(candidate)),
        ("urls", url_tokens(original) == url_tokens(candidate)),
        ("variables", variable_tokens(original) == variable_tokens(candidate)),
        ("length_ratio", within_ratio(len(original), len(candidate), ratio)),
    )
    return CheckResult(tuple(name for name, passed in checks if not passed), terms, None)
