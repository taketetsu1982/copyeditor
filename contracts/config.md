---
contract-id: CTR-04
kind: schema
derives-from: [AC-05-1, AC-05-2, AC-05-3, AC-05-4, AC-05-5, AC-05-6, AC-05-8, AC-05-9, AC-02-6, AC-02-15, AC-02-16, AC-02-17, AC-02-18, AC-02-10, AC-02-11, AC-08-1, AC-08-7, AC-08-10, AC-08-11, AC-08-12, AC-08-14, AC-08-18]
revision: 9
---

# Configuration and image layout

## Revision 8 migration proposal

Revision 9 refines this migration to one shared schema generation 4; the anchor is retained for existing references.

This revision is a proposed contract for design r10, not a statement of current executable behavior. Deploy configuration, tools, language assets and their consumers together. All tools and judgment modes use contract generation schema_version=4, not legacy v1/v2/v3. Each tool has its own closed output family; editing judgment keys stay required and are null when disabled as specified in [CTR-01](tools.md#current-version-selection).

The default_language field and COPYEDITOR_DEFAULT_LANGUAGE environment setting are removed. A config containing the old key fails as an unknown key at config. Presence of the removed environment variable fails invalid_config at config, without reading or printing its value. Remove both settings during migration; there is no replacement default. Each request resolves its explicit language or local body-language detection, including lint. Existing files without these removed fields and without old judgment registry IDs keep their other meanings.

The new registry pair is reference-gate-v2 / gate-delta-v2. The latter is reserved until calibration registers concrete floor / gap / meaning_floor values in CTR-01. Disabled startup permits thresholds_version=null; enabled startup requires a registered compatible pair. No guessed threshold values or old-registry aliases are supplied.

## Resolution

The server reads `/etc/copyeditor/config.yaml`; no search of the current directory or home directory occurs. A missing file means an empty mapping. An existing empty file, invalid UTF-8/YAML, duplicate key, alias, custom tag, non-mapping root, unknown key, or invalid value is a startup error. YAML 1.2 scalar semantics apply. Booleans are not integers. Unknown nested keys also fail. Parent mappings may be omitted or empty, but not null.

For each leaf: **explicit config value > corresponding environment variable > image default**. Lists and the `pricing` map replace their lower-priority value in full; they never merge. Explicit `[]`, `{}`, and allowed null values suppress the environment value. All present config fields are validated, even when inactive. An overridden environment variable is not parsed. Empty environment strings are present, not missing, and invalid unless the field permits an empty string (none do).

A config scalar consisting exactly of `${NAME}` resolves that environment variable once before type validation. `NAME` matches `[A-Z][A-Z0-9_]*`; unset/empty values fail. A placeholder for a list/map/number is decoded using that field's environment encoding. Embedded, recursive, or default-value substitutions are not supported. Arbitrary shell execution is never performed. Secret environment names cannot be referenced by placeholders. The selected config placeholder has precedence over the field's normal environment variable.

### Resolution amendment

Preserve the resolution rules above for old and new nonsecret leaves. Add `judgment` as a closed mapping; unknown fields fail, including fields named key/token/secret/endpoint. Exact `${NAME}` placeholders work under the existing rules, but `TYPESAFE_API_KEY` joins the forbidden secret-placeholder name set: reject that reference before looking up its value. Do not expand or dump the whole environment to resolve judgment configuration. Secret retrieval happens only after judgment.enabled resolves to true. A disabled process never looks up, validates, stores or requires TYPESAFE_API_KEY and never constructs its TypeSafe adapter/HTTP client. Merely importing an adapter module or registry does not read credentials.

Inactive nonsecret fields still undergo syntax/known-value validation, consistently with legacy auth configuration. Missing conditional secret values are checked only when enabled. Existing unrelated config fields retain their behavior; removed language defaults and retired registry IDs require the migration above. The existing editing provider remains vertex. Judgment uses TypeSafe Jev directly and has no provider-selection setting. The response provider identifier remains typesafe; the editing provider configuration and its error rules are unchanged.

## Fields

This table and the judgment fields table define the complete leaf schema. Strings are not trimmed or case-folded unless stated. No request may override these settings except `language` as specified in [CTR-01](tools.md#current-requests-and-language).

| Config key | Type / accepted values | Environment variable | Image default |
|---|---|---|---|
| `provider` | string, `vertex` only; other strings produce `unsupported_provider` | `COPYEDITOR_PROVIDER` | `vertex` |
| `model` | string, 1–128 ASCII characters matching `[A-Za-z0-9._-]+` | `COPYEDITOR_MODEL` | `gemini-3.1-flash-lite` |
| `thinking` | enum `minimal`, `low`, `medium`, `high` | `COPYEDITOR_THINKING` | `low` |
| `protected_terms` | array of distinct nonblank strings, each 1–128 code points; at most 1,024 | `COPYEDITOR_PROTECTED_TERMS` | `[]` |
| `length_ratio.min` | finite number, `0 < value <= 1` | `COPYEDITOR_LENGTH_RATIO_MIN` | `0.5` |
| `length_ratio.max` | finite number, `1 <= value <= 4` | `COPYEDITOR_LENGTH_RATIO_MAX` | `2.0` |
| `vertex.project` | string, 1–128 ASCII letters, digits, hyphen or underscore | `GOOGLE_CLOUD_PROJECT` | required |
| `vertex.location` | string, 1–64 lowercase ASCII letters, digits or hyphen | `GOOGLE_CLOUD_LOCATION` | `global` |
| `auth.mode` | enum `none`, `google` | `COPYEDITOR_AUTH_MODE` | `none` |
| `auth.allowed_domains` | distinct domain array, at most 100 entries | `COPYEDITOR_ALLOWED_DOMAINS` | `[]` |
| `auth.allowed_emails` | distinct email array, at most 1,000 entries | `COPYEDITOR_ALLOWED_EMAILS` | `[]` |
| `auth.client_id` | null or nonblank string, at most 256 code points | `GOOGLE_OAUTH_CLIENT_ID` | null |
| `auth.base_url` | null or HTTPS origin; no userinfo, query, fragment or path except `/`; trailing slash removed | `BASE_URL` | null |
| `pricing` | map from model identifier to complete price entry, at most 100 entries | `COPYEDITOR_PRICING` | `{}` |
| `server.host` | IP literal or `localhost` | `COPYEDITOR_HOST` | `0.0.0.0` |
| `server.port` | integer, 1–65,535 | `PORT` | `8080` |

Environment lists/maps use JSON, numbers use JSON numeric syntax, and strings/enums use their literal value. For the two nullable string fields, the environment value `null` denotes null. Price entries have exactly `currency` (three uppercase ASCII letters), `input_per_million`, and `output_per_million` (finite numbers, 0–1,000,000, at most six decimal places). Prices use the configured model's exact key; no alias or fallback lookup. `{}` disables estimates. No price is downloaded. The two prices are a simple estimate, not a billing model for discounts, caching, or regional tiers.

Domains are lowercase ASCII DNS names of at most 253 characters, with at least one dot, labels of 1–63 letters/digits/hyphens, and no leading/trailing hyphens, wildcard or trailing dot. Email values contain one `@`, an ASCII non-whitespace local part of 1–64 characters, and a valid domain; maximum length 254. They are compared **exactly**, without lowercasing, removing dots or stripping `+` suffixes. Operators must enter the address as returned by Google. Domain matching lowercases only the claimed domain. Domain admission requires both verified `hd` and the email domain to equal the same allowed domain. An exact allowed-email match is an alternative and does not require `hd`.

`google` additionally requires a non-null client ID, non-null base URL, at least one nonempty allowlist, and both secrets below. `none` requires no OAuth secrets and does not initialize OAuth or contact Google identity endpoints. Syntactically invalid inactive config fields still fail; missing conditional values do not. Its startup warning is exactly `WARNING: copyeditor is listening without authentication.` on stderr once per process.

### Judgment fields

The bundled policy is reference-gate-v2: one shared worst-single-spot gate, six ordered fixed references, one meaning question, state assembly and deterministic packing. It contains no numeric classification thresholds, axes, Choice, action instructions or tone. The threshold definition has exactly id and three finite values floor / gap / meaning_floor, with domains and compatibility rules in [CTR-01](tools.md#current-gate-registry-and-compatibility). Operators cannot supply arbitrary numeric maps or prompt text through config.

This proposal does not register numerical values. gate-delta-v2 is reserved, not yet an accepted registry ID. A subsequent reviewed registration must fix its definition/hash and compatible policy/hash before enabling judgment. thresholds_version=null is valid only when disabled; when enabled it produces missing_required at judgment.thresholds_version. A non-null unknown, retired or reserved ID produces invalid_config at the corresponding leaf even when disabled. A known but incompatible pair fails at judgment.thresholds_version. Never substitute a latest registry, old cutoffs or guessed numbers. Compute hashes from immutable bundled definitions, not caller-supplied hashes; changing any definition requires a new ID and comparison evaluation.

| Config leaf | Type / accepted values | Environment | Image default |
|---|---|---|---|
| judgment.enabled | boolean (not number or string in YAML; JSON true/false in environment) | COPYEDITOR_JUDGMENT_ENABLED | false |
| judgment.model | string, jev-1.13.0 only for this implementation | COPYEDITOR_JUDGMENT_MODEL | jev-1.13.0 |
| judgment.policy_version | registered string, reference-gate-v2 | COPYEDITOR_JUDGMENT_POLICY_VERSION | reference-gate-v2 |
| judgment.thresholds_version | null or registered compatible string; see registration gate above | COPYEDITOR_JUDGMENT_THRESHOLDS_VERSION | null |
| judgment.timeout_ms | integer, 1..60000 | COPYEDITOR_JUDGMENT_TIMEOUT_MS | 10000 |
| judgment.polish_deadline_ms | integer, 1..120000 | COPYEDITOR_JUDGMENT_POLISH_DEADLINE_MS | 120000 |
| judgment.rewrite_deadline_ms | integer, 1..240000 | COPYEDITOR_JUDGMENT_REWRITE_DEADLINE_MS | 240000 |
| judgment.max_calls | integer, 1..64 | COPYEDITOR_JUDGMENT_MAX_CALLS | 64 |
| judgment.input_budget | integer, 1..262144 estimated judgment input units per tool request | COPYEDITOR_JUDGMENT_INPUT_BUDGET | 262144 |
| judgment.pricing | model-to-complete-price map with the same validation as pricing | COPYEDITOR_JUDGMENT_PRICING | {} |

Integers exclude booleans, numeric strings, fractions, NaN and infinity. Environment numbers/booleans/maps use JSON, version/model strings are literal as with legacy fields; the literal null denotes null for thresholds_version. Explicit empty price map disables judgment cost estimation; no partial map merge. The runtime operation timeout is min(judgment.timeout_ms, remaining whole-request deadline), so a larger operation timeout than the degree-specific deadline is valid but cannot extend it. Limits are shrinkable engineering limits; no configuration increases legacy editing call/token budgets or the existing 120/240-second timing envelope. Protocol request/response caps and deterministic rejection are in [CTR-01's current limits](tools.md#current-limits-and-error-precedence). The enabled polish deadline covers judgment plus editing; disabled processing follows the new CTR-01 v4 contract and its editing preflight budget.

The initial adapter deliberately pins jev-1.13.0 rather than accepting jev-latest/jev-preview aliases: a moving model would change a calibrated gate without a configuration or policy change. A future pinned-model addition requires adapter conformance and the same comparison evaluation, not an automatic alias resolution request at startup. There is no judgment provider protocol or selection factory: a one-value provider selector would add branches and tests without a choice. Adding another judgment service would require an adapter and contract revision, not an already-supported setting. This does not limit the editing provider abstraction.

## Secrets and startup errors

Only `GOOGLE_OAUTH_CLIENT_SECRET` and `OAUTH_SIGNING_KEY` supply OAuth secrets. There are **no config keys** for them. Both must be nonempty in `google`; the signing key must contain at least 32 UTF-8 bytes. It must be generated with adequate entropy by the operator. ADC supplies Vertex credentials; use workload identity or the standard Google credential environment mechanism, never credentials in this config. ADC absence/unusable credentials is a startup error; permission to call a model is confirmed by the deployment smoke test.

Startup failure exits nonzero before binding the listener. Stderr uses `ERROR: <code> at <field>.` with fixed codes `invalid_config`, `missing_required`, `unsupported_provider`, `invalid_rules`, or `credentials_unavailable`. `<field>` comes from this schema or the fixed labels `config`, `rules`, `credentials`, `judgment.credentials`; unknown keys map to their nearest known parent. Never print supplied values, parser exception messages, file contents, emails or credentials. An unreadable config is an error, not an absent file. No config dump, SDK trace or access log is enabled.

### Secrets and startup errors amendment

Keep the existing secret rules and add runtime environment secret TYPESAFE_API_KEY, required only when judgment.enabled=true. Its value must be a nonempty ASCII string with no whitespace or control characters, at most 4096 bytes. Never strip, echo, store in config, include in repr, serialize, package into an image, or put in the public MCP definition. Missing/empty is missing_required at judgment.credentials; invalid shape is invalid_config at judgment.credentials. No key authenticity probe is made at startup; HTTP authentication denial during a request becomes provider_error. An unsupported judgment model or registry is invalid_config at its leaf. There is no judgment-specific unsupported_provider path; an unknown judgment mapping key follows the existing invalid_config rule. The existing `ERROR: <code> at <field>.` format is unchanged; unknown keys report their nearest known parent. No supplied value appears in diagnostics.

Resolve configuration/registry/secret validity and rules before listener binding. Suppress SDK/framework/HTTP logging before creating any adapter. Construct the judgment client only in enabled mode, after these checks; construction failure stops startup with credentials_unavailable at judgment.credentials, without raw exception text. A syntactically valid key does not claim successful live authentication. Turning off judgment does not remove Vertex ADC requirements or alter OAuth mode.

When enabled emit exactly this additional one-line stderr warning once per process, alongside the existing none-auth warning when applicable:

```text
WARNING: copyeditor judgment is enabled; body, permitted context/background and candidates may be sent to TypeSafe AI. Provider retention and processing region follow its policy.
```

Disabled mode emits no judgment warning. The initialization instructions and tool description required by CTR-01, and the English/Japanese README operator instructions, disclose the same destination. Disclosure is not per-request consent for direct MCP clients. New Skill clients include it in their existing permission check. Reconfiguration requires a process restart; no request-level switch, dynamic remote flag or persisted user preference is introduced.

## Rule-backed default style

Each built-in rules/<lang>.md must contain exactly one line beginning with the literal marker `Default style: ` in its existing Context weights section. The remainder is a nonblank, single-line style instruction of 1–1,000 Unicode code points; use the CTR-01 whitespace definition. Missing, duplicated, misplaced or empty markers fail startup as invalid_rules at rules. Do not introduce a new frontmatter field, H2 section or machine-rule JSON key. The raw rule bytes already participate in rules_version. CTR-03 and its loader/tests must incorporate this amendment in the implementation migration.

The instruction must be short and reviewable. ja describes natural Japanese expression and requires the existing native review; en and zh describe their own language's natural expression and remain native-unverified. It may not weaken common preservation, require a register change or introduce facts. This contract fixes the extraction format, not an unmeasured quality claim for particular prose.

Overlays remain additive and may not supply a Default style marker anywhere or replace the built-in default. This keeps one stable default per installed language while preserving existing additive lint/protected-term behavior. Request background.tone, when nonblank, overrides the built-in default only for that request; omission or whitespace selects the built-in value. Do not trim a nonblank supplied tone. Freeze the result for initial generation and retry, with no request cache across users. Send it only to the editing provider; neither desired_style nor background.tone may reach judgment. No config/env default-style leaf is added.

## Image layout

| Path | Purpose |
|---|---|
| `/app` | installed application and read-only built-in assets |
| `/app/rules/common.md` | preservation contract |
| `/app/rules/<lang>.md` | built-in language rules |
| `/app/examples/<lang>/` | benchmark fixtures; not loaded on tool calls |
| `/etc/copyeditor/config.yaml` | optional user configuration |
| `/etc/copyeditor/rules.d/<lang>.md` | optional additive language rules |

These paths are fixed image interfaces, not config fields. The base image must not ship a populated config that masks environment defaults. It runs one non-root Python process, listens on the resolved host/port, exposes Streamable HTTP `/mcp` and unauthenticated `GET /health` returning `{"status":"ok"}`. The external TLS terminator supplies HTTPS; `auth.base_url` is its origin. OAuth callback is `/auth/callback`. Health includes no configuration or identity data.

Derived images use these exact COPY destinations:

```dockerfile
ARG COPYEDITOR_IMAGE
FROM ${COPYEDITOR_IMAGE}
COPY config.yaml /etc/copyeditor/config.yaml
COPY rules/ja.md /etc/copyeditor/rules.d/ja.md
```

`COPYEDITOR_IMAGE` is `ghcr.io/<owner>/copyeditor:vX.Y.Z` (or the same image pinned by digest). Files must be readable by the image user. Never COPY onto `/app/rules/` to add rules. Each overlay uses [CTR-03](../rules/README.md#overlays); it appends section content and protected terms, cannot delete built-in rules or weaken common preservation, and must not reuse a lint ID. Missing overlay directories are empty; unreadable or malformed overlays stop startup. No runtime reload: restart to activate a coherent config/rules snapshot. Tags `vMAJOR.MINOR.PATCH` starting at `v0.1.0` publish the matching image; release tags must not be overwritten.

### Judgment example configuration

The example configuration includes the following mapping and comments alongside the existing values. Do not include a secret value or a key placeholder in YAML.

```yaml
# Optional judgment provider. Disabled selects the new v4 contract.
# Enabling sends body, permitted context/background and candidates to TypeSafe AI.
# Supply TYPESAFE_API_KEY through the runtime secret environment, never this file.
judgment:
  enabled: false
  model: jev-1.13.0
  policy_version: reference-gate-v2
  thresholds_version: null # Enabling requires a calibrated, registered version.
  timeout_ms: 10000
  polish_deadline_ms: 120000
  rewrite_deadline_ms: 240000
  max_calls: 64
  input_budget: 262144
  pricing: {}
# To estimate judgment costs, replace judgment.pricing with a complete entry:
#   jev-1.13.0:
#     currency: USD
#     input_per_million: 0.042
#     output_per_million: 0
# Confirm prices before deployment; the server never downloads prices.
```

The numeric price is an operator example based on the documented price at authoring, not a maintained server default. The secret must be provided to the running container through its normal secret injection; never through a Docker build ARG, COPY or image environment literal. Image scanning extends existing secret non-bundling checks to a synthetic judgment-key sentinel.

## Defaults and validation

Ratio thresholds and tool limits are initial engineering choices, **not measured quality or capacity guarantees**. Model `gemini-3.1-flash-lite` preserves the source deployment's baseline; its documented thinking levels include low. This is not a model comparison or an upgrade decision. [Google model documentation](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-1-flash-lite)

Startup schema tests and `tests/contracts/test_ctr04_config.py` must exercise the table, absent/empty files, unknown keys, explicit empty overrides, selected/unselected invalid environment values, secret-key rejection, placeholders, and `config.example.yaml` with synthetic environment values. A derived-image test checks both authentication modes and additive rules. The first implementation task introduces fixture helpers and positive/negative self-tests, not the complete config or image consumer. Collection explicitly reports real-consumer tests as unconnected until the corresponding consumer exists; skips/xfails do not represent contract passes. Each config or image implementation task introduces its schema/consumer tests and makes them mandatory in CI. The final integration task executes the complete config and derived-image checks with nonempty coverage and no skips, xfails or unconnected checks. This document-only change does not introduce executable tests.

### Defaults and validation additions

Retain existing startup, config precedence, auth and image tests, adding enabled=false with an absent/malformed key and a poisoned key accessor, config/env precedence for every new leaf, unknown/duplicate keys, forbidden secret placeholders, boolean-as-integer, limit endpoints, one-beyond endpoints, unknown immutable registry IDs, no alias fallback, enabled missing/invalid key startup failure before listener binding, HTTP auth failure without retries, warning privacy, and no judgment construction/calls when disabled. Test the example addition through the same strict config parser. Live key validity, Japanese quality and user permission acceptance are separate owner evidence, not fabricated by fixture CI.
