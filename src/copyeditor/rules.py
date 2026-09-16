import hashlib
import json
import re
from pathlib import Path
from types import MappingProxyType
from typing import NamedTuple
from .config import ConfigError, array, freeze, matches, nonblank, strict_yaml

HEADINGS = ("Vocabulary", "Syntax", "Structure", "Translation artifacts", "Context weights", "Machine-readable rules")
CATEGORIES = ("vocabulary", "syntax", "structure", "translation", "context")
class LanguageRules(NamedTuple):
    prose: str
    protected_terms: tuple[str, ...]
    detectors: tuple
class RuleSnapshot(NamedTuple):
    common_bytes: bytes
    common_version: str
    rules_version: str
    languages: object
def require(condition):
    if not condition:
        raise ValueError()
def integer(value, low, high):
    return type(value) is int and low <= value <= high
def characters(value, limit):
    return isinstance(value, str) and 1 <= len(value) <= limit and len(set(value)) == len(value)
def detector(value):
    require(isinstance(value, dict))
    kind = value.get("kind")
    fields = {"literal": ("value",), "regex": ("pattern",), "sentence_length": ("max", "terminators"),
              "comma_count": ("max", "commas", "terminators"), "repeated_ending": ("endings", "min_run", "terminators"),
              "brackets": ("pairs",), "width_mix": ("half", "full")}
    require(isinstance(kind, str) and kind in fields and set(value) == {"kind", *fields[kind]})
    if kind in ("literal", "regex"):
        text = value[fields[kind][0]]
        require(isinstance(text, str) and 1 <= len(text) <= 512)
    if "terminators" in value:
        require(characters(value["terminators"], 16))
    if kind in ("sentence_length", "comma_count"):
        require(integer(value["max"], 1 if kind == "sentence_length" else 0, 12000 if kind == "sentence_length" else 100))
    if kind == "comma_count":
        require(characters(value["commas"], 16))
    if kind == "repeated_ending":
        require(array(value["endings"], 32, lambda s: isinstance(s, str) and 1 <= len(s) <= 32) and value["endings"] and integer(value["min_run"], 2, 32))
    if kind == "brackets":
        require(array(value["pairs"], 16, lambda s: isinstance(s, str) and len(s) == 2) and value["pairs"])
        require(characters("".join(value["pairs"]), 32))
    if kind == "width_mix":
        require(characters(value["half"], 256) and characters(value["full"], 256) and len(value["half"]) == len(value["full"]) and not set(value["half"]) & set(value["full"]))
def unique_object(pairs):
    require(len(dict(pairs)) == len(pairs))
    return dict(pairs)
def parse(raw, language, overlay):
    text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    require(text.startswith("---\n"))
    front, body = text[4:].split("\n---\n", 1)
    metadata = strict_yaml(front)
    require(isinstance(metadata, dict) and set(metadata) == {"language", "revision", "native_reviewed"})
    require(metadata["language"] == language and type(metadata["revision"]) is int and metadata["revision"] > 0 and type(metadata["native_reviewed"]) is bool)
    headings, sections, visible, blocks, fence, index = [], [[] for _ in HEADINGS], [[] for _ in HEADINGS], [], None, -1
    for line in body.splitlines():
        if fence:
            if re.fullmatch(r" {0,3}" + re.escape(fence[0][0]) + "{" + str(len(fence[0])) + r",}\s*", line):
                blocks.append((index, fence[1], "\n".join(fence[2])))
                fence = None
            else:
                fence[2].append(line)
        else:
            opening = re.fullmatch(r" {0,3}(`{3,}|~{3,})(.*)", line)
            if opening:
                fence = [opening[1], opening[2].strip(), []]
            elif re.match(r" {0,3}#{1,6}(?:\s|$)", line):
                headings.append(line)
                index = len(headings) - 2
                require(-1 <= index < len(HEADINGS))
                continue
            else:
                require(not re.fullmatch(r" {0,3}(?:=+|-+)\s*", line) or not index >= 0 or not sections[index] or not sections[index][-1].strip())
                if index >= 0:
                    visible[index].append(line)
        if index >= 0:
            sections[index].append(line)
    require(fence is None and headings == [f"# {language} writing rules", *["## " + h for h in HEADINGS]])
    if language in ("en", "zh") and not metadata["native_reviewed"]:
        require(body.split(f"# {language} writing rules\n", 1)[1].lstrip("\n").startswith("Draft: not reviewed by a native speaker.\n"))
    prose = ["\n".join(s) for s in sections[:5]]
    require(overlay or all(nonblank(p, len(p)) and all(re.search(r"\b" + label + r"\b", p) for label in ("bad", "good", "reason")) for p in prose))
    machine = [content for section, kind, content in blocks if section == 5 and kind == "json"]
    require(len(machine) == 1)
    data = json.loads(machine[0], object_pairs_hook=unique_object, parse_constant=lambda _: require(False))
    require(isinstance(data, dict) and set(data) == {"schema_version", "protected_terms", "rules"} and type(data["schema_version"]) is int and data["schema_version"] == 1)
    require(array(data["protected_terms"], 1024, lambda s: nonblank(s, 128)) and isinstance(data["rules"], list) and len(data["rules"]) <= 256)
    anchors = [(anchor, i) for i, lines in enumerate(visible) for anchor in re.findall(r'<a id="([^"]+)"></a>', "\n".join(lines))]
    require(len({a for a, _ in anchors}) == len(anchors))
    for rule in data["rules"]:
        require(isinstance(rule, dict) and set(rule) == {"id", "section", "description", "detector"})
        require(rule["section"] in CATEGORIES and matches(re.escape(language) + "-" + rule["section"] + r"-[0-9]{3}", rule["id"]))
        require((rule["id"], CATEGORIES.index(rule["section"])) in anchors and nonblank(rule["description"], 160))
        detector(rule["detector"])
    return prose, data["protected_terms"], data["rules"], anchors
def load_rules(base=Path("/app/rules"), overlay=Path("/etc/copyeditor/rules.d"), config_terms=()):
    try:
        base, overlay = Path(base), Path(overlay)
        require(not base.is_symlink() and not overlay.is_symlink())
        documents, manifest = {}, []
        for root, prefix in ((base, "base"), (overlay, "overlay")):
            if prefix == "overlay" and not root.exists():
                continue
            for path in sorted(root.iterdir()):
                require(not path.is_symlink() and path.is_file())
                if prefix == "base" and path.name == "README.md":
                    continue
                raw = path.read_bytes()
                logical = "common.md" if prefix == "base" and path.name == "common.md" else f"{prefix}/{path.name}"
                manifest.append([logical, hashlib.sha256(raw).hexdigest()])
                if logical == "common.md":
                    common = raw
                    common.decode("utf-8")
                    continue
                language = path.stem
                require(path.suffix == ".md" and matches(r"[a-z]{2,3}(-[a-z0-9]{2,8})*", language) and len(language) <= 35)
                require(prefix == "base" or language in documents)
                documents.setdefault(language, []).append(parse(raw, language, prefix == "overlay"))
        require(documents and array(list(config_terms), 1024, lambda s: nonblank(s, 128)))
        languages, ids, anchors_seen = {}, set(), set()
        for language, parts in documents.items():
            terms, rules = sorted({term for _, terms, _, _ in parts for term in terms}), [rule for _, _, rules, _ in parts for rule in rules]
            anchors = [a for _, _, _, anchors in parts for a, _ in anchors]
            require(len(set(anchors)) == len(anchors) and not anchors_seen.intersection(anchors))
            anchors_seen.update(anchors)
            require(len(terms) <= 1024 and len(rules) <= 256)
            for rule in rules:
                require(rule["id"] not in ids)
                ids.add(rule["id"])
            terms = tuple(sorted(set(terms) | set(config_terms)))
            require(len(terms) <= 2048)
            prose = "\n\n".join("## " + HEADINGS[i] + "\n" + "\n".join(p[i] for p, _, _, _ in parts) for i in range(5))
            languages[language] = LanguageRules(prose, terms, tuple(freeze(r) for r in rules))
        version = hashlib.sha256(json.dumps(sorted(manifest), ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        return RuleSnapshot(common, "sha256:" + hashlib.sha256(common).hexdigest(), "sha256:" + version, MappingProxyType(languages))
    except Exception:
        raise ConfigError("invalid_rules", "rules") from None
