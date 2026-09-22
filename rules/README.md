---
contract-id: CTR-03
kind: schema
derives-from: [AC-03-1, AC-03-2, AC-03-3, AC-03-4, AC-04-1, AC-04-2, AC-04-3, AC-04-6, AC-05-4, AC-02-6, AC-02-11]
revision: 2
---

# Language rule format

## Document structure

Each UTF-8 `rules/<lang>.md` defines one language. A language identifier matches `[a-z]{2,3}(-[a-z0-9]{2,8})*`, is at most 35 characters and is used exactly in requests. Initial identifiers are `ja`, `en`, `zh`; `zh` targets Simplified Chinese. Aliases and automatic script/region fallback are not defined. Installed valid language files determine the advertised language enum.

Each file has YAML frontmatter with exactly `language` (matching the filename), `revision` (positive integer), and `native_reviewed` (boolean). Duplicate keys, aliases and custom YAML tags are invalid. Its H1 is `# <lang> writing rules`. All languages have these H2 headings, exactly once and in this order, with no other headings outside fenced code:

```text
## Vocabulary
## Syntax
## Structure
## Translation artifacts
## Context weights
## Machine-readable rules
```

The first five sections each explain what to change and what to retain, and contain at least one labeled `bad`, `good`, `reason` example. Headings, criteria and reasons are English; bad/good samples use the target language. `ja` requires human native review recorded in its change PR. `en` and `zh` start with `native_reviewed: false` and the visible sentence `Draft: not reviewed by a native speaker.` immediately below the H1. Native review is not inferred from a model score.

## Machine-readable rules

The final section contains exactly one fenced `json` block with this complete shape (illustrative content, not a shipped English rule):

```json
{
  "schema_version": 1,
  "protected_terms": ["ExampleProduct"],
  "rules": [
    {
      "id": "en-vocabulary-001",
      "section": "vocabulary",
      "description": "Prefer a direct phrase where it preserves the meaning.",
      "detector": {"kind": "literal", "value": "in order to"}
    }
  ]
}
```

All displayed fields are required; null and unknown fields are invalid. `rules` has 0–256 entries per effective language; `protected_terms` is a distinct array of 0–1,024 nonblank strings, each at most 128 code points. After union with config, at most 2,048 distinct terms may apply to one language. `description` is a nonblank English string of at most 160 code points, returned unchanged as the finding's message. An ID matches `<language>-(vocabulary|syntax|structure|translation|context)-[0-9]{3}`, and must be globally unique. The section matches the category in the ID and maps, in order, to the first five H2s above. Keep IDs stable; do not reuse a retired ID for another behavior.

Each rule also has an HTML anchor `<a id="<id>"></a>` in its prose section. A finding links to this anchor. A prose criterion need not have a mechanical detector when it requires meaning or syntax analysis. Do not add a noisy detector just to pretend such analysis is deterministic.

## Detectors

No dictionary, tokenizer, morphology, network call, model or locale-dependent analysis is used. Regex syntax is the [RE2 syntax](https://github.com/google/re2/wiki/Syntax), UTF-8 mode, case-sensitive by default. Compile at startup; unsupported patterns fail startup. All match spans refer to the original code-point offsets (convert from bytes if needed). Ignore zero-length regex matches. Matches per rule are nonoverlapping, left-to-right. Inline regex flags may be used only for `i`, `m`, `s`; patterns are 1–512 code points.

| `kind` | Required additional fields (no others) | Findings |
|---|---|---|
| `literal` | `value`: nonempty string, at most 512 code points | Each exact nonoverlapping occurrence |
| `regex` | `pattern`: regex | Each nonempty full match |
| `sentence_length` | `max`: integer 1–12,000; `terminators`: distinct nonempty string of at most 16 single-character delimiters | One finding covering each sentence with code-point length greater than max |
| `comma_count` | `max`: integer 0–100; `commas`: distinct string of 1–16 characters; `terminators`: as above | One finding over each sentence containing more than max comma characters |
| `repeated_ending` | `endings`: distinct array of 1–32 nonempty strings, each at most 32 code points; `min_run`: integer 2–32; `terminators`: as above | For a maximal run of at least min_run adjacent sentences with the same ending, one span from first to last sentence |
| `brackets` | `pairs`: array of 1–16 two-character strings with distinct, nonshared opening and closing characters | Each unmatched bracket character; stack-based matching, no quote or escape inference |
| `width_mix` | `half`: distinct string of 1–256 characters; `full`: equally long distinct string, disjoint from half | One span covering a sentence containing any half and any full member; uses newline as its only sentence terminator |

Sentence segmentation scans until any configured terminator or LF, includes that delimiter, and also emits a nonempty final remainder. Trim Unicode whitespace at each edge for the reported span/length; omit empty segments. CR in CRLF is removed by this trimming. No abbreviation recognition is performed. `repeated_ending` strips one terminal delimiter and trailing whitespace before choosing the longest literal suffix in endings (ties use code-point order); nonmatching sentences break a run. Newline also terminates the sentence. Bracket scanning pops only when the closing character matches the stack top; other closing characters are reported and do not pop; openings left on the stack are reported. All these algorithms run on the complete supplied string, including markup; lint is advisory and not a document parser.

Collect all detector matches, deduplicate identical `(rule_id,start,end)` tuples, and sort by `(start,end,rule_id)` using numeric offsets and ASCII ID order. Findings carry `matched = text[start:end]` and the rule description. Return the first 100 per item, with `findings_truncated: true` iff additional matches exist. Truncation is deterministic. This cap affects findings only, never whether a candidate is adopted.

Each built-in language requires one `Default style: ` instruction in Context weights; overlays cannot define it. See [the default-style contract](../contracts/config.md#rule-backed-default-style).

## Overlays

An additive file in `/etc/copyeditor/rules.d/<lang>.md` has the same frontmatter, headings and JSON shape. It requires an existing built-in language and uses new lint IDs. Each prose section is appended to its built-in section, built-in first; no overwrite directive exists. Protected terms are unioned exactly and sorted. The combined rules/terms must meet the effective limits above. Empty prose sections and empty rules arrays are allowed in overlays, but not empty required prose sections in built-ins. `common.md` and `README.md` are not overlay language names. Unknown files, symlinks, unreadable files, mismatched language metadata, duplicate anchors/IDs, or malformed JSON stop startup. Adding a new language requires a built-in file and examples through a normal release.

An overlay may add style guidance but cannot override common preservation or remove a built-in criterion. Mechanically reject conflicting IDs; semantic conflicts in prose are resolved in review, with common preservation always taking precedence at runtime. Do not imply that a schema validator can detect every prose contradiction.

## Versioning

`revision` records human changes; increment it for any file change. Runtime `rules_version` is `sha256:` followed by 64 lowercase hex digits. Build a manifest of `[logical_path, sha256(file_bytes)]` pairs sorted lexicographically by ASCII path; serialize as compact UTF-8 JSON with no extra whitespace; hash those bytes. Include exactly `common.md`, `base/<lang>.md` for every built-in language, and `overlay/<lang>.md` for every overlay. README, examples and config are excluded. Hash raw bytes, including frontmatter and line endings. `common_version` is the same `sha256:` format over `common.md` bytes only. Thus prose-only edits and overlays change the effective version without relying on a manually remembered release number.

Config terms and ratios do not change `rules_version`; responses expose matched terms and the effective ratio separately. Each process reads a single immutable snapshot at startup. The build writes the base rules manifest/version into both plugin distributions; their bundled common copy has its own verifiable common hash. A caller can distinguish changed language rules from a changed preservation contract.

## Verification

`tests/contracts/test_ctr03_rules.py` validates all language frontmatter, H2 sequences, JSON fields, detector compilation, ID/anchor consistency, overlays and digest stability. It excludes `common.md` and this README from language enumeration. CI fails mismatched headings, unknown fields or invalid rules. Bad/good behavior and section coverage are checked through [CTR-05](../examples/README.md#deterministic-assertions). The first implementation task introduces fixture helpers and their positive/negative self-tests. Collection explicitly reports real-consumer tests as unconnected until their consumers exist, without executing them or treating skips/xfails as passes. Each rule-loader or language-asset task makes the corresponding real contract checks mandatory in CI. The final integration task checks every built-in language and fixture, requires a nonempty asset set and rejects skips, xfails and unconnected checks; missing language assets must not be mistaken for a passing empty glob.
