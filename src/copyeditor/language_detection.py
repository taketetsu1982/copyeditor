"""Offline request-language resolution; not connected to the public runtime yet."""
from fractions import Fraction
from functools import lru_cache
from html import unescape
from math import isfinite
import re

from .html_trace import trace
from .html_trace_types import HTMLTraceError
from .requests import ValidationError


@lru_cache(maxsize=1)
def _detector():
    from lingua import LanguageDetectorBuilder
    # Installed rule languages must not constrain recognition of unsupported languages.
    return LanguageDetectorBuilder.from_all_languages().build()


def _prose(raw):
    traced = trace(raw)
    if traced.input_error is not None:
        raise ValidationError("invalid_input", "language")
    pieces = []
    for span in traced.spans:
        context = traced.events[span.event_index].after.open_elements
        protected = any(node.namespace == "http://www.w3.org/1999/xhtml" and
                        node.name in ("pre", "code", "script", "style", "template", "head")
                        for node in context)
        if span.kind in ("text", "reference") and not protected:
            text = raw[span.start:span.end]
            pieces.append(unescape(text) if span.kind == "reference" else text)
        elif protected:
            pieces.append(" ")
        elif span.kind == "markup":
            # Inline markup and comments do not introduce visible word boundaries.
            tag = re.match(r"</?([a-zA-Z][^\s/>]*)", raw[span.start:span.end])
            if tag and tag[1].lower() in {
                "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt",
                "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3",
                "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre",
                "section", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
            }:
                pieces.append(" ")
    return "".join(pieces)


def resolve_language(items, loaded_languages, *, language=None, format="text", detector=None):
    """Resolve validated SourceItems without changing their raw text or context.

    Explicit IDs are matched exactly. An injected detector implements Lingua's
    compute_language_confidence_values API; production uses the fixed local model.
    """
    if language is not None:
        if language not in loaded_languages:
            raise ValidationError("unsupported_language", "language")
        return language
    try:
        body = "\n".join(_prose(item.text) if format == "html" else item.text for item in items)
    except HTMLTraceError:
        raise ValidationError("invalid_input", "language") from None
    if not any(char.isalpha() for char in body):
        raise ValidationError("invalid_input", "language")
    model = _detector() if detector is None else detector
    values = model.compute_language_confidence_values(body)
    if not values or len(values) < 2:
        raise ValidationError("invalid_input", "language")
    if any(not isfinite(v.value) or not 0 <= v.value <= 1 for v in values):
        raise ValidationError("invalid_input", "language")
    first, second = sorted(values, key=lambda v: v.value, reverse=True)[:2]
    # Decimal representations keep the boundary exact without ambient rounding.
    if Fraction(str(first.value)) - Fraction(str(second.value)) < Fraction("0.20"):
        raise ValidationError("invalid_input", "language")
    iso = first.language.iso_code_639_1
    if iso is None:
        raise ValidationError("invalid_input", "language")
    resolved = iso.name.lower()
    if resolved not in loaded_languages:
        raise ValidationError("unsupported_language", "language")
    return resolved
