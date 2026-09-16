---
contract-id: CTR-04
kind: schema
derives-from: [AC-05-1, AC-05-2, AC-05-3, AC-05-4, AC-05-5, AC-05-6, AC-05-8, AC-05-9, AC-02-6, AC-02-10, AC-02-11]
revision: 2
---

# Configuration and image layout

## Resolution

The server reads `/etc/copyeditor/config.yaml`; no search of the current directory or home directory occurs. A missing file means an empty mapping. An existing empty file, invalid UTF-8/YAML, duplicate key, alias, custom tag, non-mapping root, unknown key, or invalid value is a startup error. YAML 1.2 scalar semantics apply. Booleans are not integers. Unknown nested keys also fail. Parent mappings may be omitted or empty, but not null.

For each leaf: **explicit config value > corresponding environment variable > image default**. Lists and the `pricing` map replace their lower-priority value in full; they never merge. Explicit `[]`, `{}`, and allowed null values suppress the environment value. All present config fields are validated, even when inactive. An overridden environment variable is not parsed. Empty environment strings are present, not missing, and invalid unless the field permits an empty string (none do).

A config scalar consisting exactly of `${NAME}` resolves that environment variable once before type validation. `NAME` matches `[A-Z][A-Z0-9_]*`; unset/empty values fail. A placeholder for a list/map/number is decoded using that field's environment encoding. Embedded, recursive, or default-value substitutions are not supported. Arbitrary shell execution is never performed. Secret environment names cannot be referenced by placeholders. The selected config placeholder has precedence over the field's normal environment variable.

## Fields

This table is the complete leaf schema. Strings are not trimmed or case-folded unless stated. No request may override these settings except `language` as specified in [CTR-01](tools.md#requests).

| Config key | Type / accepted values | Environment variable | Image default |
|---|---|---|---|
| `provider` | string, `vertex` only; other strings produce `unsupported_provider` | `COPYEDITOR_PROVIDER` | `vertex` |
| `model` | string, 1–128 ASCII characters matching `[A-Za-z0-9._-]+` | `COPYEDITOR_MODEL` | `gemini-3.1-flash-lite` |
| `thinking` | enum `minimal`, `low`, `medium`, `high` | `COPYEDITOR_THINKING` | `low` |
| `default_language` | language identifier defined by CTR-03; must have loaded rules | `COPYEDITOR_DEFAULT_LANGUAGE` | `ja` |
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

## Secrets and startup errors

Only `GOOGLE_OAUTH_CLIENT_SECRET` and `OAUTH_SIGNING_KEY` supply OAuth secrets. There are **no config keys** for them. Both must be nonempty in `google`; the signing key must contain at least 32 UTF-8 bytes. It must be generated with adequate entropy by the operator. ADC supplies Vertex credentials; use workload identity or the standard Google credential environment mechanism, never credentials in this config. ADC absence/unusable credentials is a startup error; permission to call a model is confirmed by the deployment smoke test.

Startup failure exits nonzero before binding the listener. Stderr uses `ERROR: <code> at <field>.` with fixed codes `invalid_config`, `missing_required`, `unsupported_provider`, `invalid_rules`, or `credentials_unavailable`. `<field>` comes from this schema or the fixed labels `config`, `rules`, `credentials`; unknown keys map to their nearest known parent. Never print supplied values, parser exception messages, file contents, emails or credentials. An unreadable config is an error, not an absent file. No config dump, SDK trace or access log is enabled.

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

## Defaults and validation

Ratio thresholds and tool limits are initial engineering choices, **not measured quality or capacity guarantees**. Model `gemini-3.1-flash-lite` preserves the source deployment's baseline; its documented thinking levels include low. This is not a model comparison or an upgrade decision. [Google model documentation](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-1-flash-lite)

Startup schema tests and `tests/contracts/test_ctr04_config.py` must exercise the table, absent/empty files, unknown keys, explicit empty overrides, selected/unselected invalid environment values, secret-key rejection, placeholders, and `config.example.yaml` with synthetic environment values. A derived-image test checks both authentication modes and additive rules. The first implementation task introduces fixture helpers and positive/negative self-tests, not the complete config or image consumer. Collection explicitly reports real-consumer tests as unconnected until the corresponding consumer exists; skips/xfails do not represent contract passes. Each config or image implementation task introduces its schema/consumer tests and makes them mandatory in CI. The final integration task executes the complete config and derived-image checks with nonempty coverage and no skips, xfails or unconnected checks. This document-only change does not introduce executable tests.
