---
contract-id: CTR-01
kind: api
derives-from: [AC-01-6, AC-01-8, AC-01-9, AC-01-14, AC-02-1, AC-02-2, AC-02-3, AC-02-4, AC-02-5, AC-02-6, AC-02-7, AC-02-8, AC-02-9, AC-02-10, AC-02-11, AC-02-12, AC-02-13, AC-03-1, AC-03-2, AC-03-4, AC-07-1, AC-07-2, AC-07-3, AC-07-4, AC-07-6, AC-07-7, AC-07-10, AC-07-11, AC-07-12, AC-07-13, AC-08-1, AC-08-2, AC-08-3, AC-08-4, AC-08-5, AC-08-6, AC-08-7, AC-08-8, AC-08-9, AC-08-10, AC-08-11, AC-08-12, AC-08-14, AC-08-15, AC-08-16, AC-08-17, AC-08-18]
revision: 9
---

# MCP tool contract

## Transport and discovery

Streamable HTTP endpoint: `/mcp`. Tools are `polish_text` and `lint_text`. Both use the same configured authentication. Authentication failures are HTTP 401 before tool execution, with OAuth resource discovery in `WWW-Authenticate` for `google`; there is no fabricated tool success containing an auth error. Malformed JSON-RPC/unknown methods/tools use standard protocol errors. Known tool calls with invalid arguments use the application error envelope below, including framework validation failures.

Tools advertise complete JSON Schema input/output definitions derived from this contract. Input object schemas reject additional properties; `polish_text` uses `oneOf` to express text/items exclusivity. `language` is an enum of loaded language identifiers and advertises the resolved default. Bounds and combined-budget descriptions are included in discovery. `polish_text.degree` advertises the enum `["polish", "rewrite"]` and default `"polish"`. A client requires this explicit enum member before sending rewrite; absence, an unconstrained string, or an unrecognized schema is not evidence of rewrite support. Do not probe with body text or silently substitute polish. Both tools have `readOnlyHint: true`, `destructiveHint: false`; `polish_text` has `openWorldHint: true`, `lint_text` false. No annotation bypasses the client's approval policy.

Tool results contain `structuredContent` equal to the payload defined here and one `content` text block containing the same JSON. `isError` is false for success (including flags/rejections), true for application errors. Output schema is the union of the success/error shapes. No partial streaming results are exposed. Clients check `isError` and payload `status`. This follows the [MCP tool-result structure](https://modelcontextprotocol.io/specification/2025-11-25/server/tools).

The `polish_text` output schema advertises only the versions the running server can return: v1/v2 when disabled, v3 when enabled. Its description contains exactly one of these machine-recognizable lines:

```text
copyeditor.judgment=off; destinations=Vertex AI
copyeditor.judgment=on; destinations=Vertex AI, TypeSafe AI
```

These lines are disclosure, not permission. The enabled description additionally says: `Body, permitted context and background, and candidates may be sent to TypeSafe AI. Provider retention and processing region are governed by that provider. Judgment does not replace your meaning comparison or approval.` No key or operator-supplied text is interpolated. A new Skill reads both the description marker and schema on every request; missing, contradictory, or unknown information is treated as potentially enabled for permission purposes. An unsupported output version still prevents body submission; consent does not make an unreadable schema safe. Existing clients may reject v3; enabling judgment is an explicit deployment compatibility change, not negotiated fallback. The disabled new-server description may add this marker without changing any v1/v2 payload.

## Requests

All lengths count decoded Unicode code points without normalization. Null is forbidden in every input field. An omitted optional string means the stated default; an explicitly empty background/context string is valid. Empty or whitespace-only body text is invalid. Invalid UTF-8, unpaired surrogates, duplicate JSON object keys and unknown properties are rejected. Request IDs are opaque identifiers, never paths or body snippets.

| Tool / field | Type and requiredness | Default / restrictions |
|---|---|---|
| polish `text` | string, required exactly when items is absent | 1–12,000 code points |
| polish `items` | array, required exactly when text is absent | 1–32 objects, each exactly `id`, `text`, optional `context` |
| item `id` | required string | `[A-Za-z0-9_-]{1,64}`, unique in request |
| item `text` | required string | 1–12,000 code points |
| item `context` | optional string | `""`; at most 1,000 code points; never rewritten |
| polish `degree` | optional enum `polish`, `rewrite` | `polish`; null, other strings and nonstrings are invalid |
| polish `format` | optional enum `text`, `markdown`, `html` | `text`; html is legal only with text, never items |
| polish `audience`, `purpose`, `tone`, `message` | optional strings | each `""`, at most 1,000 code points |
| either tool `language` | optional language enum | resolved server default from CTR-04; no automatic fallback |
| lint `text` | required string | 1–12,000 code points |

`lint_text` accepts only `text` and `language`, not items, background, format, or degree. It sends no input to a model. Supplying text and items, neither, a duplicate ID, unsupported language, or an invalid field fails before any provider call. No client-provided model/thinking/term override is accepted.

### Limits

For polish, all body texts total at most 12,000 code points; contexts total at most 4,000; the four background fields total at most 4,000; contexts plus background total at most 4,000; body plus contexts plus background total at most 16,000. A text request has zero context. These overlapping constraints are intentional, retaining the old input budget. The JSON-RPC request body is capped at 262,144 UTF-8 bytes before parsing (HTTP 413 may occur here, before the tool envelope is possible).

Each model generation has an 8,192-output-token budget for its whole batch, not per item. For polish, a retry subset has a new budget and an item participates in at most two generations. Rewrite adds one diagnostic generation; its candidate generations retain the same per-item shared retry limit. See [rewrite limits](#rewrite-limits-and-accounting). Success payload JSON is at most 1,048,576 UTF-8 bytes using compact JSON, `ensure_ascii=false`, no NaN/Infinity. Findings contain at most 100 entries per item; each `matched` is limited to 160 code points as specified below. Provider output limits are not silently clipped.

These are initial engineering limits, not measured success or latency guarantees. Typical Skill batches target at most 16 items / 6,000 body code points while obeying all hard budgets. Long text is split at meaningful boundaries; HTML is never split. A permitted split retry is a client decision, not automatic server retry on an output-limit error.

### Output text acceptance

Input text limits are not output text limits. Every model candidate is a string of 1–16,000 decoded Unicode code points; the sum of candidate lengths in **each generation batch** is at most 16,000. This applies separately to the initial batch and the retry subset. Every final returned text is also 1–16,000 code points; the sum across the **whole merged request**, including originals returned for flagged items, is at most 16,000. No trimming, normalization or clipping is permitted.

Empty and whitespace-only strings are invalid. For this contract, whitespace is exactly Unicode White_Space: U+0009–000D, U+0020, U+0085, U+00A0, U+1680, U+2000–200A, U+2028–2029, U+202F, U+205F, U+3000. This definition also applies to input blank-body validation. A nonempty string is blank iff every code point is in that set.

Apply the following order, including on retry:

1. Map provider failure/finish first: truncated to `generation_truncated`, blocked to `provider_error`, any other non-STOP finish to `invalid_response`.
2. Validate the complete batch's JSON, closed schema, string types, Unicode validity, IDs and nonblank texts. Any failure is `invalid_response`, including blank text with an `unfixable` flag. Do not classify a text-length maximum here.
3. Check candidate per-item and generation-batch length maxima. Any violation is `output_limit`, even when preservation would also fail. An overlong nonblank string is not an `invalid_response` merely because of its length. Steps 2–3 fail the whole request without regeneration; a blank item takes precedence over an overlong item in the same batch.
4. Restore originals for `unfixable` items, perform preservation/HTML checks, and use the shared retry procedure below. Candidate acceptance never reuses the 12,000-character input cap.
5. After merging all results and restoring any rejected originals, validate final text types/Unicode/nonblankness (`invalid_response`), then final per-item and whole-request length maxima (`output_limit`). Perform lint and metadata calculation, then validate the complete success schema and the serialized payload byte limit (`output_limit` for that byte limit). Return only after all checks pass.

No output acceptance error returns partial items or triggers another model call. A retry subset can fit its own budget while making the final merged result exceed the request budget; that is a whole-request `output_limit` after two calls.

## Success payloads

The following v1/v2 shapes apply when judgment is disabled; [version selection](#version-selection-and-compatibility) defines the v3 override.

All fields in these shapes are required. No additional fields. Keys are unordered; arrays have defined order. `schema_version` is integer 1 for lint and polish. Rewrite uses the version 2 extension below. Omitting degree or supplying `polish` preserves the version 1 payload, including errors; no degree or diagnosis fields are added to it.

```text
PolishText = Metadata + {
  status: "ok", text: string, flag: Flag|null,
  regenerated: boolean, protected_terms: string[],
  findings: Finding[], findings_truncated: boolean
}
PolishItems = Metadata + {status: "ok", items: ItemResult[]}
ItemResult = {
  id: string, text: string, flag: Flag|null, regenerated: boolean,
  protected_terms: string[], findings: Finding[], findings_truncated: boolean
}
Lint = Metadata + {
  status: "ok", findings: Finding[], findings_truncated: boolean
}
Metadata = {
  schema_version: 1, language: string, rules_version: string,
  common_version: string, model: string|null,
  usage: Usage, cost: Cost|null, latency_ms: integer,
  model_calls: integer, protected_terms_checked: integer,
  preservation: {length_ratio: {min: number, max: number}}|null
}
Usage = {input_tokens: integer|null, output_tokens: integer|null,
         total_tokens: integer|null}
Cost = {amount: string, currency: string}
Flag = {kind: "unfixable"|"rejected", reason: string, checks: string[]}
Finding = {rule_id: string, start: integer, end: integer,
           matched: string, matched_truncated: boolean, message: string}
```

For polish, model is the configured nonempty model identifier; preservation contains resolved ratios. The text route has no `id` and no `items` field. The items route returns exactly all submitted IDs in input order, never model order. `regenerated` is true iff that item was included in the one retry. Flags have a nonblank reason of at most 160 code points. `unfixable` has `checks: []`; the provider may propose this flag but the server forces `text` to the exact original. `rejected` is server-only, with the nonempty failed-check array from CTR-02 and a fixed English reason `Preservation checks failed.`. No flag means literal null, not omission. Unchanged unflagged text is valid. Rejected/unfixable items are successful results, not whole-batch errors.

Matched protected terms and their count follow [CTR-02](../rules/common.md#deterministic-checks). Findings are evaluated on the final returned text (the original for flagged items), not on discarded candidates. Lint results use model null, zero usage, model_calls 0, cost null, preservation null, and protected_terms_checked 0. Lint does not perform preservation comparisons.

Finding offsets are zero-based code points, end-exclusive, with `0 <= start < end <= len(text)`. For spans longer than 160 code points, matched contains exactly the first 160 and matched_truncated is true; end still points to the complete match. Otherwise matched equals the full slice and matched_truncated is false. Messages are fixed rule descriptions; no provider explanation is used for lint. Ordering, rule IDs and caps are defined by [CTR-03](../rules/README.md#detectors).

## Usage and cost

These rules apply unchanged to v1/v2; v3 uses [judgment accounting and limits](#judgment-accounting-and-limits).

Counts are nonnegative integers or null. Zero means known zero, null means unavailable. Input tokens use provider prompt usage; output tokens include candidate **and thinking** tokens. Total tokens use provider total usage, not an invented sum. Aggregate each component across every attempted model call in the request, including diagnosis, all candidate batches, discarded candidates and failed attempts; if any attempted call lacks that component, its aggregate is null. If no call was made, all three are zero. The adapter must not map missing thinking usage to zero without a provider guarantee.

`model_calls` counts invocations actually started, including failures: 0–2 for polish (one initial batch plus one batch containing all failed items), 0–17 for rewrite, and 0 for lint. Count preflight token-estimation RPCs separately from model generations; they generate no text and add no invented generation usage, but their time is included in latency. Latency is rounded elapsed monotonic wall time in milliseconds, from tool entry through final validation and lint, including regeneration; no OAuth/login duration. `cost` is null when there is no call, no exact price entry, or either input/output aggregate is null. Otherwise amount is `(input_tokens * input_per_million + output_tokens * output_per_million) / 1000000`, decimal arithmetic, rounded half-up to six decimal places, encoded as a fixed six-place string; currency is the configured entry. It is explicitly an estimate.

## Errors and retry boundary

```text
Error = {
  status: "error", schema_version: 1,
  error: {code: Code, message: string, field: string|null},
  language: string|null, rules_version: string, common_version: string,
  model: string|null, usage: Usage, cost: Cost|null,
  latency_ms: integer, model_calls: integer, model_called: boolean,
  regeneration_attempted: boolean
}
```

No text, candidate, items, flags, diagnoses or findings occur in an error, even after diagnosis or an earlier internal batch succeeded. Original text remains with the caller. `language` is null if no supported effective language could be resolved; otherwise it is the selected language. Model is null for lint, configured model for polish even before a call. Version fields identify the immutable loaded snapshot. `model_called == (model_calls > 0)` reports whether a generation started. In version 1 this distinguishes MCP receipt from provider transmission. In version 2, false does not prove that no text reached Vertex: token estimation may already have sent the prompt before a budget/estimation error. Clients report MCP receipt and generation status separately and never infer non-transmission from false. `field` is null or a safe schema path (for example `items[0].text`), never a submitted ID/key/value. Messages use the exact fixed strings below. If multiple input errors exist, use request shape/types, then language, then size validation, in that order; within a stage use schema table order then array order.

| Code | Message | Trigger / caller action |
|---|---|---|
| `invalid_input` | `Invalid tool arguments.` | shape/type/null/empty body/ID/format; no call, no retry |
| `unsupported_language` | `Language is not installed; the model was not called.` | supported-shaped language identifier absent from loaded rules; no call, no language-changing retry |
| `input_limit` | `Input limits exceeded; the model was not called.` | count/length/aggregate limit; text/markdown may split once |
| `generation_truncated` | `Generation was truncated; preserve the original.` | provider explicitly reports token-limit termination; text/markdown may split once |
| `invalid_response` | `Model response is incomplete or invalid; preserve the original.` | invalid JSON/schema, missing/extra/duplicate IDs, empty or whitespace-only candidate/final body, other incomplete finish; no retry |
| `output_limit` | `Output limits exceeded; preserve the original.` | candidate/final-text/payload hard limit, or rewrite diagnosis limit; rewrite text/markdown may split once as for truncation; polish keeps no retry |
| `html_structure` | `HTML structure changed; preserve the original.` | structure failed after shared retry; no retry |
| `provider_timeout` | `The model request timed out; preserve the original.` | provider or whole-request deadline; no automatic retry |
| `provider_error` | `The model request failed; preserve the original.` | transport, API or safety-block failure; no automatic retry |
| `request_budget` | `Request processing budget exhausted; preserve the original.` | rewrite reservation budget exhausted; whole-request error, no automatic retry |
| `internal_error` | `Processing failed; preserve the original.` | unexpected internal failure; no automatic retry |

Output errors have field null. A provider-safety block is provider_error, not a claim that the text was successfully checked. Retry model JSON integrity failure invalidates the **original whole request**, including initially valid items. No partial payload is returned. SDK/HTTP automatic retries are disabled.

For polish, initial generation follows the [output acceptance order](#output-text-acceptance), including batch integrity and length limits, before any item evaluation. Then run all applicable preservation and HTML checks. Collect only failing items into one retry batch with their same original text/context/background/language and the same system rules; do not send the failed candidate back. On retry, rerun integrity and both checks. HTML failure takes precedence over preservation rejection. An unfixable result returns the original and passes through the same final checks. A retry never resets a budget or attempts a third generation. Input/result ordering is maintained locally.

## Rewrite payloads and validation

`degree: "rewrite"` is accepted for every installed language, for both text and items. Text/markdown/HTML rules and all original input budgets remain unchanged. HTML stays a single whole-document text item. No diagnosis or per-item editing instruction is accepted from the client. Context remains surrounding source material, not an instruction channel. Japanese quality evaluation does not imply native verification for en/zh.

Version 2 is selected for an entered `polish_text` call whose argument object has exactly the string `degree: "rewrite"`, including application errors before generation. Otherwise version 1 applies; transport/protocol errors remain outside these envelopes. Version 2 has the following closed shapes:

```text
RewriteText = PolishText with schema_version: 2,
  plus required degree: "rewrite", diagnosis: Diagnosis
RewriteItems = PolishItems with schema_version: 2,
  plus required degree: "rewrite",
  each ItemResult plus required diagnosis: Diagnosis
RewriteError = Error with schema_version: 2,
  plus required degree: "rewrite"
Diagnosis = {status: "issue"|"no_issue", expression: string|null, reason: string|null}
```

All other fields and their constraints are inherited, except the rewrite model_calls range is 0–17. Every integer constraint excludes booleans. The output-schema union distinguishes v1 polish/lint, v2 rewrite success and both versioned errors; v2 shapes require degree, v1 shapes forbid it. Version 1 retains its previous error-code enum; request_budget is version 2 only. `diagnosis` never appears in a version 2 error or lint result. The text route has exactly one diagnosis and no public synthetic ID. Each items result carries its own diagnosis under the original ID and in original order. Diagnosis is not a finding, flag, adoption permission, or proof of preserved meaning.

For `issue`, expression and reason are required nonblank Unicode strings: expression is an exact contiguous substring of that item's original text, at most 160 code points; reason is at most 320 code points, explaining the expression problem in the selected language. One expression can identify a representative part of a recurring problem. For `no_issue`, both fields must be null; clients display a localized “No issue” label. Unknown keys, invalid Unicode/types/status, duplicate keys, non-substring expressions and inconsistent nulls are `invalid_response`. Do not clip or normalize these values.

Diagnosis IDs must exactly cover the original request once, with no missing, added or duplicate IDs. Candidate IDs must exactly cover each requested internal subset once. Before flag normalization or preservation checks, every `no_issue` candidate must equal its original text exactly, including whitespace and newlines; violation is `invalid_response`, even with an unfixable flag. Recheck this invariant in the merged response. Never repair an inconsistent candidate by copying its original or spending a regeneration. Flagged items still have diagnoses and exact original text on success. A frozen issue diagnosis may accompany a rejected/unfixable original; it is a proposed problem, not a claim that an edit was applied.

Subject to the deadline precedence below, apply provider finish mapping first, then validate the entire diagnostic response's shape/IDs/strings and no-issue null constraints, then its length maxima. Invalid shape wins over an overlong string; length violations are `output_limit`. Candidate validation uses the existing output acceptance order, with no-issue exact equality at step 2 before size checks and flag restoration. The same ordering applies on regeneration and final merge. Any failure discards the entire user request, including all earlier diagnoses and candidates.

### Rewrite limits and accounting

The sum of expression and reason lengths over all diagnoses is at most 8,192 code points. Candidate/final text maxima remain 16,000 per item and per merged request. The compact complete success payload, including diagnoses, remains capped at 1,048,576 UTF-8 bytes. Diagnostic field/aggregate maxima and payload maxima are inclusive; one above is `output_limit`. No diagnostics are dropped to fit a cap. These output caps do not reduce the original input budgets.

Rewrite performs one diagnostic generation for the request, then candidate batches of at most four original items, and at most one failed-item regeneration subset per candidate batch. Original item boundaries and order are retained; no internal splitting of text or HTML. Thus for N items there are at most `1 + 2 * ceil(N / 4)` started generations, with N=1 on the text route, and an absolute maximum of 17. Errors can have zero or fewer calls. Every item, including a no-issue item, receives a candidate for exact-equality verification. Provider failures never cause resizing or retransmission. Successful internal batches remain private until the whole request passes.

The server uses a 240-second monotonic processing deadline from tool entry for rewrite, including preflight, all generation and validation. A provider operation has at most 60 seconds and never exceeds the remaining deadline. Deadline exhaustion is `provider_timeout`. Cancellation prevents any further calls; it cannot retract a request already delivered to Vertex or guarantee that remote billing stops immediately.

Rewrite reserves at most 262,144 estimated input tokens and 139,264 output tokens over the request. Before each generation, the same Vertex model's token-count operation estimates its complete prompt (system rules, source/context/background, frozen diagnosis when used, and response schema). Reserve `ceil(1.25 * estimated_input) + 1024` input tokens and 8,192 output tokens before starting it; no reservation is refunded or reused. An invalid/missing estimate or estimation API error is `provider_error`; estimation timeout is `provider_timeout`. If the next reservation would exceed either budget, return `request_budget` without starting that generation. SDK/HTTP retries are disabled for estimation as well as generation. At most one estimation RPC is made for each intended generation (at most 17 per request); it does not count as a model generation.

With configured nonnegative finite input/output prices P and Q per million, the admitted estimated cost ceiling is `(262144 * P + 139264 * Q) / 1000000` in that configured currency. This is a reservation ceiling, not a guaranteed cloud invoice ceiling: token counts are estimates, prices are operator supplied, and remote cancellation is not a billing rollback. Without pricing, the same token reservations apply and public cost remains null. The public usage/cost always use actual reported generation usage, never reservations. If reported input or candidate-plus-thinking output exceeds its reservation, apply the [rewrite error precedence](#rewrite-error-precedence); select `request_budget` only when no higher-priority error applies. Regardless of the selected code, retain the reported usage and start no further call. If usage is missing, retain the full reservation and expose null according to the existing aggregation rule. Never assume missing thinking usage is zero. Resource limits do not silently change model/thinking settings.

`regenerated` and `regeneration_attempted` refer only to a preservation/HTML retry, not to a second batch or diagnosis. Errors include all started generation calls and their available usage; no partial-content success is substituted when budgets run out. A rewrite output-limit error permits only the client's one-generation split retry for text/markdown. HTML and request-budget/provider errors are never split-retried automatically. Diagnosis is ephemeral request data and excluded from every log sink in both successful and failed processing.

### Rewrite error precedence

For version 2, record the started generation slot and all available actual usage before selecting an error. Recording usage must not itself throw a budget error or bypass response validation. An unknown component stays null and does not establish an overrun; a known component exceeding its corresponding reservation does. At each completed provider operation (diagnosis, candidate, or regeneration), and at final merge/validation, select the first applicable row below. Only conditions observable at that checkpoint participate; do not start another call to discover a higher-priority error in a future batch.

| Priority | Condition | Public code / action |
|---|---|---|
| 1 | Whole-request deadline reached (`now >= deadline`), or the current provider operation timed out | `provider_timeout`; no automatic client retry |
| 2 | ProviderFailure other than timeout, or blocked finish | `provider_error`; no automatic client retry |
| 2 | Truncated finish | `generation_truncated`; text/markdown may split once; HTML may not |
| 2 | Other non-STOP finish | `invalid_response`; no automatic client retry |
| 3 | STOP response with invalid diagnostic/candidate JSON, closed shape, IDs, Unicode/nonblankness, diagnostic null/substr constraints, or no_issue original inequality; final merged integrity/schema failure (excluding length/byte maxima classified in row 4) | `invalid_response`; no automatic client retry |
| 4 | Otherwise valid response exceeds a diagnostic field/aggregate, candidate text, or currently available merged-text/complete-payload maximum | `output_limit`; text/markdown may split once; HTML may not |
| 5 | Reported input or candidate-plus-thinking output exceeds the completed generation's reservation | `request_budget`; no automatic client retry |

ProviderFailure and a generation result are mutually exclusive adapter outcomes; a result has exactly one finish. At a checkpoint, snapshot time when choosing the outcome after any required local validation; a deadline reached during validation takes priority. Once an error is selected, freeze that code: error serialization and accounting cleanup do not replace it with a later timeout. Final validation uses rows 1, 3 and 4; an earlier selected error is already terminal and cannot be reconsidered at merge.

The table runs before flag restoration, preservation/HTML checks or scheduling a retry/next batch. Those existing checks run only if no row applies. Thus an overrun with an otherwise valid but preservation-failing candidate cannot spend the retry allowance. Final payload limits are checked when a complete payload exists, not speculated about during an earlier overrun. Before any new generation, deadline and estimation failure take precedence over a prospective reservation refusal; if the estimate is valid but the next reservation cannot fit, use request_budget without starting a generation.

Every selected error discards all diagnoses and candidate results from the user request, preserves all started-call counts and available actual usage/cost (including the failing attempt), and permits zero further server generation or estimation calls. Higher-priority truncated/output_limit errors retain their client split permission even when an overrun also occurred; do not add a hidden budget override to client retry decisions. The existing one-generation split allowance and meaningful-boundary requirement still apply. No new error code or retry mechanism is introduced, and version 1 is unchanged.

Fake-provider/clock verification must cover the following collisions, with nonzero known usage on the completed call. For each, assert the exact code, whole-request content discard, retained usage/call count, zero subsequent estimation/generation calls, and the stated client decision; exercise both text/markdown split eligibility and HTML non-retry where applicable.

| Completed outcome / competing condition | Expected code | Client decision |
|---|---|---|
| truncated + measured input over reservation | `generation_truncated` | text/markdown may split once; HTML never |
| STOP + changed no_issue candidate + measured overrun | `invalid_response` | no automatic retry |
| Valid STOP diagnostic or candidate response + measured overrun | `request_budget` | no automatic retry |
| Deadline reached + truncated + measured overrun | `provider_timeout` | no automatic retry |
| ProviderFailure / blocked finish + measured overrun | `provider_error` | no automatic retry |
| Other non-STOP finish + measured overrun | `invalid_response` | no automatic retry |
| STOP + invalid diagnostic IDs + diagnostic length overrun + measured overrun | `invalid_response` | no automatic retry |
| STOP + valid shape + diagnostic/candidate length overrun + measured overrun | `output_limit` | text/markdown may split once; HTML never |
| Valid STOP candidate failing preservation/HTML + measured overrun | `request_budget` | no automatic retry; no shared regeneration |

Also test equality at reservation (not an overrun), unknown usage without invented excess, the deadline reached during local validation, a frozen error followed by deadline expiry during cleanup, and final merged integrity/size checks with a reached deadline. These are consumer test requirements, not claims that the unimplemented v2 pipeline already passes them.

## Version selection and compatibility

Servers without the judgment extension retain existing discovery and initialization. Do not advertise v3 before its consumers are connected.

For an entered `polish_text` call, the immutable server setting `judgment.enabled=true` selects v3 for both successes and application errors, including argument/framework validation errors. Otherwise retain exactly the existing v1/v2 selection, closed shapes, bounds, usage, call counts, retry rules, and behavior. `lint_text` always retains v1, never calls either provider, and does not read the judgment key. Authentication, raw transport limits, and JSON-RPC protocol failures remain outside the tool payload. Input fields, language resolution, text/items exclusivity, and existing input limits are unchanged. Clients cannot enable, disable, or reconfigure judgment through arguments.

## Judgment payloads and validation

This section overrides inherited v1/v2 shape rules only for v3. All fields below are required, unknown fields forbidden, integers exclude booleans, probabilities are finite numbers in [0,1], and null is distinct from omission. Reuse existing `Usage`, `Cost`, `Finding`, `Diagnosis`, input-ID and Unicode rules. References to flags below use `JudgedFlag`, not the legacy closed `Flag`.

```text
JudgedMetadata = {
  schema_version: 3, degree: "polish"|"rewrite", language: string,
  rules_version: string, common_version: string,
  providers: [ProviderMeasurement, ProviderMeasurement],
  cost: Cost|null, latency_ms: integer,
  policy_version: string, policy_hash: string,
  thresholds_version: string, thresholds_hash: string,
  protected_terms_checked: integer,
  preservation: {length_ratio: {min: number, max: number}}
}
ProviderMeasurement = {
  role: "editing"|"judgment", provider: string, model: string,
  model_calls: integer, estimation_calls: integer,
  usage: Usage, cost: Cost|null, latency_ms: integer
}
JudgedText = JudgedMetadata + {status: "ok"} + JudgedResult
JudgedItems = JudgedMetadata + {status: "ok", items: [{id: string} + JudgedResult]}
JudgedResult = {
  text: string, flag: JudgedFlag|null, regenerated: boolean,
  protected_terms: string[], findings: Finding[], findings_truncated: boolean,
  diagnosis: Diagnosis|null,
  editing: "not_run"|"diagnosed_no_issue"|"generated",
  detection: Detection, verification: Verification
}
Detection = {
  status: "eligible"|"insufficient"|"indeterminate"|"not_run",
  reason: "evaluated"|"no_editable_prose",
  gate: {probability: number, result: "present"|"absent"}|null,
  checks: [{id: string, probability: number}],
  action: {selected: Action, probabilities: {Action: number}, confidence: number,
           effective: Action, source: "choice"|"axis_fallback"}|null
}
Action = "preserve_as_is"|"simplify_vocabulary"|"simplify_phrasing"|"make_more_concrete"|"make_more_specific"|"simplify_structure"|"trim_explanation"
Verification = {
  status: "pass"|"fail"|"indeterminate"|"not_run",
  reason: "evaluated"|"not_generated"|"unfixable"|"preservation_rejected",
  checks: [{id: string, probability: number, result: "pass"|"fail"|"indeterminate"}]
}
JudgedFlag = {
  kind: "unfixable"|"rejected"|"verification_rejected",
  reason: string, checks: string[]
}
JudgedError = {
  status: "error", schema_version: 3, degree: "polish"|"rewrite"|null,
  error: {code: CodeV3, message: string, field: string|null},
  language: string|null, rules_version: string, common_version: string,
  providers: [ProviderMeasurement, ProviderMeasurement], cost: Cost|null,
  latency_ms: integer, model_called: boolean, regeneration_attempted: boolean,
  policy_version: string, policy_hash: string,
  thresholds_version: string, thresholds_hash: string
}
```

`CodeV3` is exactly the existing v2 code set, including `request_budget`. Fixed messages and field rules remain those of the corresponding existing code. Error degree is polish for an absent degree in an object or exact polish; rewrite for exact rewrite; null for a non-object or any invalid degree. Invalid language is null unless a supported language was resolved by the existing input rules. No error contains items, text, diagnoses, detection, verification, or partial content. Provider failures still use a tool error, not a flagged success.

Provider rows are always present, in editing then judgment order, even with zero calls. For the initial adapters provider/model are `vertex`/the configured editing model and `typesafe`/`jev-1.13.0`. Identifiers follow CTR-04; no raw upstream identifier is copied unchecked. Hashes are lowercase 64-character SHA-256, versions are the configured registry IDs in CTR-04. No top-level legacy `model`, `usage`, or `model_calls` exists in v3: readers must use the provider rows. `latency_ms` is nonnegative monotonic elapsed time. Terms, findings, ratios, final-text bounds, full-request identity/order, and flag-original restoration retain their existing meaning. Text has no synthetic public ID. Diagnosis is null for polish and for rewrite when detection did not admit the item; it is the complete existing Diagnosis otherwise.

### Registered threshold classification

This is the public source of truth for probability/result and aggregate-state consistency. Server, Skill and independent clients use it without private documents. New policy/threshold versions require a contract revision with their complete vocabulary, thresholds and rules. Hash equality is not a replacement. Reject unknown or incompatible versions; compare finite JSON numbers without rounding before classification.

The active pair is policy `reference-gate-action-v1` and thresholds `gate-verify-v1`. The threshold definition is exactly `{id: "gate-verify-v1", floor: 0.53, verification: {pass_min: 0.70, fail_max: 0.30}}`. All three decision parameters belong to the threshold version; they are provisional starting values, not evidence of completed calibration. Policy owns questions, references, action vocabulary/instruction mapping and packing, but no numeric decision boundary. Verification order and aggregation remain unchanged. Changing any decision parameter requires a new immutable threshold ID, a contract revision and renewed comparison evaluation.

Migration from `gate-floor-v1` changes the published threshold version. That old pair retains its historical 0.53 gate and 0.20/0.80 verification meaning and is not an alias accepted by this registry. Version-checking clients reject the new pair until updated to this contract. The policy ID stays unchanged because no question, reference, instruction or packing changes. Removing fail_max/pass_min from its legacy verification definition changes its canonical hash: this explicit ownership migration supersedes the old hash without reusing the old threshold ID. Hashes are computed from the actual new definitions; never retain a stale hash or silently treat old records as new. Subsequent policy-content changes require a new policy ID.

| Layer | Ordered IDs | Classification |
|---|---|---|
| Gate | gate | p >= 0.53: present; p < 0.53: absent |
| Axes | stiff, abstract, formulaic, roundabout, repetitive | Raw probabilities only; no per-axis thresholds or low/high classes |
| Verification | meaning, scope, natural, achieved | p <= 0.30: fail; p >= 0.70: pass; otherwise indeterminate |

Calibration measures the gate floor separately from the two shared verification boundaries. The midpoint-of-gap rule applies only to the gate. All four verification checks use the same fail_max and pass_min, with 0 <= fail_max < pass_min <= 1. Boundary equality belongs to fail/pass respectively; the open interval is indeterminate. No per-check thresholds are introduced. Noul values are probabilities of yes, not severity. The gate explicitly covers stiff, abstract, generic, formulaic, roundabout and mechanically repetitive expression. formulaic covers both generic and formulaic wording; roundabout includes redundant explanation. Axis values neither admit nor veto editing.

| Action | Bounded editing intent |
|---|---|
| preserve_as_is | Keep the original wording. |
| simplify_vocabulary | Use familiar equivalent words while retaining necessary technical terms and precision. |
| simplify_phrasing | Simplify roundabout phrasing without losing meaning, hedging or politeness. |
| make_more_concrete | Express existing actions and relationships concretely without adding facts or examples. |
| make_more_specific | Replace generic or formulaic wording using only specifics in the permitted input. |
| simplify_structure | Simplify mechanically repeated sentence structures while preserving meaningful repetition. |
| trim_explanation | Remove redundant explanation without removing distinct facts, conditions or qualifications. |

For every evaluated detection validate the complete gate, five ordered axes and Choice, including when the gate is absent. Then apply this complete aggregation table:

| Gate result | Choice selected | Detection.status | action.effective | action.source |
|---|---|---|---|---|
| absent | any valid option | insufficient | selected | choice |
| present | non-preserve option | eligible | selected | choice |
| present | preserve_as_is | eligible | mapped maximum axis | axis_fallback |

The fallback mapping is stiff→simplify_vocabulary, abstract→make_more_concrete, formulaic→make_more_specific, roundabout→simplify_phrasing, repetitive→simplify_structure. Choose the greatest raw axis probability; ties use the axis order in the table. All-zero or all-equal axes still use this deterministic order. No axis minimum is required. Only an eligible preserve_as_is selection triggers fallback. Confidence, including zero, cannot change eligibility, effective action, tie handling or fallback. The editor and candidate verification use effective, while selected/probabilities/confidence retain the original Choice observation; fallback does not relabel that distribution or its maximum.

`action.probabilities` is a closed object containing all seven Action keys exactly once; JSON key order is immaterial. All probabilities and confidence must be finite numbers in [0,1], never booleans. The sum must differ from 1 by no more than 0.000001, without renormalization. selected must be an exact maximum-probability key; for tied maxima preserve any returned maximal choice. Missing/extra/duplicate keys, unknown choices and non-maximal choices are invalid_response. confidence is the provider's separate statistic, not selected probability or an invented entropy formula. effective/source must exactly match the table and axis mapping; effective is not required to maximize the Choice distribution when source=axis_fallback.

A finite gate probability always has a floor classification, including p=0.5 (absent for gate-verify-v1); do not manufacture an uncertainty band or a second threshold. Detection.status retains indeterminate as a distinct successful non-change concept, but the current pinned Noul protocol and this registry do not emit it: missing/null/nonfinite answers are invalid_response, not normal indeterminacy. Consumers reject an indeterminate detection claimed under this active pair rather than inventing evidence. Verification has an explicit indeterminate interval. A future normal indeterminate detection representation would require a published protocol/registry revision; it may not be silently conflated with insufficient or not_run (AC-08-3).

Evaluated verification is pass only when all four checks pass; any fail makes the aggregate fail, otherwise it is indeterminate. meaning compares facts, numbers, conditions, negation and strength of commitments; scope bounds the edit to effective action; natural asks whether the candidate is more natural than the original for this audience, not whether it is acceptable in isolation; achieved asks whether that effective action was accomplished. not_run denotes non-execution, never missing probabilities.

Historical expression-v1 / conservative-v1 and state-action-v1 / state-action-conservative-v1 retain their original definitions and are not aliases or accepted registry pairs here. Do not reinterpret their records using these rules. The removed should_edit field and neutral axes are forbidden in the closed active shape.

The policy bundles fixed synthetic references, three natural and three unnatural, as a stable detection baseline. Client content or other requests never become reference examples. Normal detection shares all blocks of this request in one state; normal verification shares all final original/candidate/action pairs. Limits may require deterministic batches as defined below. Choice confidence cannot compensate for a vocabulary gap; edit_risk remains evaluation-only. Questions, references and the server's Japanese action instructions change only with a new policy ID and evaluation.

### Detection and editing outcomes

For policy reference-gate-action-v1, checks contains exactly the five ordered axes, with probability only; gate and action are non-null and reason is evaluated. Status and effective action follow the public table above. A preserve_as_is Choice with a present gate is valid and eligible, not indeterminate.

not_run has reason no_editable_prose, checks [], gate=null and action=null. It is allowed only when the existing HTML analysis finds no non-whitespace editable prose. Return the exact original without provider calls. No operational failure, missing answer or budget refusal may become not_run. Every other valid item receives complete detection, sharing its request's state unless deterministic packing requires batches.

For detection insufficient, indeterminate, or not_run: text is byte-for-byte original as a Unicode string, editing=not_run, diagnosis=null, flag=null, regenerated=false, and verification={status:not_run, reason:not_generated, checks:[]}. These are successful unchanged outcomes, not refusals or evidence of an issue-free document. They do not by themselves cause related-item rejection. Existing user prohibition, preservation comparison, and related-item consistency conditions still apply.

For rewrite detection eligible, validate the complete diagnostic subset before any candidate generation. A no_issue diagnosis produces original text with editing=diagnosed_no_issue, null flag, regenerated=false and verification not_run/not_generated/[]; no candidate is generated. An issue diagnosis is immutable across candidate attempts. For polish diagnosis is always null. `editing=generated` records an item sent for candidate generation, including an unchanged result, unfixable flag or rejection. Every generated rewrite item has an issue diagnosis. Missing/extra/duplicate diagnosis IDs are invalid_response; diagnosis-null exemptions apply only to the explicitly non-admitted items, not to the admitted diagnostic subset. Legacy v2 no_issue candidate generation and exact-equality checks are unchanged.

### Candidate verification

Verification checks appear exactly once in this order: `meaning`, `scope`, `natural`, `achieved`. They describe the final candidate that survived deterministic checks, before any verification-driven restoration of the original. `pass` requires all four pass; `fail` requires at least one fail; otherwise `indeterminate` requires at least one indeterminate. Evaluated verification has reason evaluated and all four checks. Do not show a discarded candidate body in the result or describe its scores as scores of the restored original.

A generated, unflagged final candidate always receives verification, even if identical to the original. A pass allows it to be returned, subject to existing whole-request final validation and client comparison. For fail or indeterminate return the exact original with `kind=verification_rejected`, fixed reason `Candidate verification failed.` or `Candidate verification was inconclusive.` respectively. Flag checks list every non-pass verification ID, in the fixed order; each corresponding check says fail or indeterminate. There is no new generation/retry. The item is a refusal for client reporting and related-item handling; other server items can succeed. `regenerated` still indicates only a previous preservation/HTML retry.

Existing unfixable/rejected flags retain their exact reason/check rules and take priority over verification: no verification call, status not_run, reason unfixable or preservation_rejected, checks []. They are original-text results, not verified changed candidates. HTML structure failure after the shared retry remains a whole-request error. Candidates discarded for first-attempt deterministic failures are never verified; only their replacement is. No previous verification result is reused for a different candidate. Any shape/ID/output-integrity failure still discards the whole request before flag normalization.

### V3 model_called invariant

For every JudgedError, `model_called == any(row.model_calls > 0 for row in providers)`: either a started editing generate or a started judgment evaluate makes it true, including a failed invocation. Estimation calls alone do not make it true. The legacy v1/v2 equation and meaning are unchanged. Validate this equality against the two required provider rows before returning or accepting the error.

A false value is not proof that no body left the process: an editing count preflight can transmit input, and this flag does not measure delivery or receipt. Skill reports editing generation, judgment evaluation and estimation counts separately using the provider rows; it must not translate false into "not sent", or true into successful generation, and it grants no retry permission.

| Error observation | editing model_calls | judgment model_calls | editing estimation_calls | model_called |
|---|---|---|---|---|
| Input validation failed before either adapter | 0 | 0 | 0 | false |
| First detection failed | 0 | 1 | 0 | true |
| Detection admitted one item; editing preflight started, generation not started | 0 | 1 | 1 | true |
| First editing generation started after detection and preflight | 1 | 1 | 1 | true |
| DTO accounting boundary: only estimation started | 0 | 0 | 1 | false |

The last row defines the boolean independently of orchestration; it is not a claim that current detect-first service reaches that state. In every row judgment estimation_calls=0. Consumer tests must include each row and its contradictory boolean as a negative case, including real service failure points for the first four rows. Larger counts obey the same equation.

### Judgment accounting and limits

Per-provider model_calls count started generate/evaluate HTTP invocations, including failures; one Jev detection request with 7*N questions counts as one invocation, not N or 7*N. Editing estimation_calls count started count preflights separately, judgment estimation_calls=0. Calls not started contribute neither count nor usage. Per-component usage is null if any started model call lacks that component, otherwise its sum; no started model calls yields zeros. Estimation RPCs add duration/count but not invented generation usage. Jev output tokens are actual reported tokens, not forced to zero because their price is zero; total_tokens is null if the provider does not report it. Missing or malformed optional usage components become null, never guessed. Valid answers do not become provider_error solely because usage is unavailable.

Provider cost uses its exact configured pricing entry and known input/output usage with the existing decimal formula/rounding. A zero-call row has cost null; it contributes known zero to total cost. Request cost is null when no model call started, any called row lacks cost, or called rows have different currencies. Otherwise sum unrounded computed amounts in the common currency and round once to six places. Provider latency includes its generation/evaluation and estimation waits, measured locally including failed awaits; zero calls of either kind yields 0. Request latency includes all stages and local validation; it is not an arithmetic sum of provider latencies.

The judgment-enabled request has a single tool-entry deadline: at most 120 seconds for polish, at most 240 seconds for rewrite. Each provider await is at most its configured operation timeout and the remaining deadline; editing retains its 60-second cap. No SDK retry, redirect following, failure-based resizing, background continuation, fallback, or call after terminal failure/cancel is allowed. In-flight remote computation/billing cannot be retracted.

For N original items (text: N=1), normal detection uses one call for all N blocks and normal verification one call for all M surviving candidates (zero if M=0). Only deterministic size packing can increase these to D<=N and V<=M calls, hard maximum D+V<=64 judgment calls. A no-editable-prose, non-admitted, diagnosed-no-issue, unfixable or preservation-rejected item only reduces this. Editing retains maximum 2 polish generations, or 1 diagnostic generation plus at most 2*ceil(N/4) rewrite candidate generations (maximum 17). Every edited item still has only one preservation/HTML retry. All-KEEP starts zero editing calls, including estimation.

Editing reservation rules for enabled rewrite retain 262,144 input / 139,264 output estimated tokens; enabled polish uses 262,144 input / 16,384 output estimated tokens, at most two generation slots. Both use the existing Vertex count preflight and margin formula. Disabled polish does not acquire a new preflight. Judgment reserves U=`UTF8(canonical JSON({model,state,questions})).length+4096` input units per batch, at most 262,144 across the tool request by default (CTR-04 may lower it). These are engineering units, not measured tokens or a cross-provider sum. The upstream context limits are 64k tokens for state plus all questions and 32k for state plus the longest question. Before sending, `request-pack-v1` uses two local admission bounds: U<=64000 and S=`UTF8(canonical JSON(state)).length+max(UTF8(canonical JSON(question)).length)+4096`<=32000. Canonical JSON is UTF-8, sorted keys, no optional whitespace, unescaped Unicode and no nonfinite numbers. The byte estimate is conservative engineering policy, not a proven tokenizer count.

Packing follows original block order, greedily closing a contiguous batch just before the next complete block would exceed either bound. Every detection batch repeats the same complete six references; verification packs indivisible original/candidate/action pairs in surviving original order. Never split within a block or HTML document, omit references, summarize context or resize after an API failure. A single block/pair that cannot fit produces request_budget before that phase sends any batch. Precompute the phase's full partition and reject prospective call/input-limit excess before its first call. Overall deadline and monetary checks also precede each send. An upstream token-limit rejection remains provider_error, with no adaptive retry.

Record the immutable plan (packing version, phase, original ordinals per batch, U and S) in request-local memory. Synthetic evaluation artifacts capture that plan and started batch counts to expose changes in sibling comparison context. Production logs/audit do not record it or any judgment contents; this amendment introduces no new public trace field. Changing the packing policy changes policy_version and requires comparison reevaluation.

Usage above reservation marks an overrun while preserving actual usage. Judgment HTTP response bodies are bounded at 65,536 bytes before JSON parsing, including streaming accumulation; oversized responses are invalid_response. Actual reported judgment output above 65,536 tokens is also invalid_response. Original public input limits remain unchanged; input acceptance does not guarantee sufficient processing reservation.

Existing editing budget caps and the whole-request deadlines are never enlarged. The new provider's finite ledger is separate; heterogenous tokens are not combined. Where both configured price entries exist and currencies match, additionally cap combined admitted monetary reservations at the previous editing reservation ceiling for that degree: `(262144*editing_input_price + output_cap*editing_output_price)/1e6`. Reserve judgment input units and 65,536 output tokens at its configured rates before evaluation, editing at existing reservation rates before generation; no refunds. With unknown or different-currency prices no cross-currency ceiling is invented: the two finite ledgers remain mandatory and total cost may be null. These are admission limits, not guarantees about invoices or unavailable usage. Exhaustion never silently changes thresholds, skips verification, or switches to the disabled path.

The combined monetary ceiling equals the editing-only ceiling, so judgment reservations reduce the remaining room for editing.
A near-limit request admitted with judgment disabled may therefore be refused with `request_budget` when judgment is enabled; this is intentional to keep the per-request estimated maximum cost unchanged.
Do not add a judgment allowance to that ceiling: doing so would change the total-cost promise. With missing prices or different currencies, only the two independent ledgers apply, as specified above.
Judgment unit prices are expected to be orders of magnitude lower than editing prices, limiting the practical impact under that pricing assumption without guaranteeing admission.
Report this refusal as budget exhaustion through the existing `request_budget` code and fixed message, retaining the required provider measurements so callers can understand that judgment consumed part of the shared budget; do not introduce a new error shape or silently bypass judgment.

### Judgment error precedence

For v3 apply, at every await completion and before starting another await: (1) reached overall deadline => provider_timeout; (2) provider/transport/auth failure => its fixed provider_timeout or provider_error; (3) editing finish mapping => existing truncated/blocked/other mapping; (4) invalid answer or generation shape/ID/Unicode/no_issue consistency => invalid_response; (5) valid-shape diagnosis/candidate/final-payload size excess => output_limit; (6) HTML structure failure after retry => html_structure; (7) measured/prospective reservation or call-cap refusal => request_budget. A response byte-cap violation in the judgment adapter is step 4, not a client-splittable generation truncation. An API 429, 401 or 403 is provider_error, with no retry. Normal middle-range values are neither API errors nor invalid_response. A terminal code is frozen; cleanup cannot replace it or start calls.

Record started-call slots and obtainable usage before choosing among competing outcomes; perform no new remote call merely to discover a higher-priority condition. Local integrity checks of an already received payload take precedence over its measured overrun. Semantic verification fail/indeterminate is processed only after these error checks and returns an item refusal, never overrides a whole-request error. Input validation still occurs before either provider call.

Errors return all started-call counts, locally measured durations and obtainable usage; all diagnoses/candidates/judgment scores are discarded. Client split permission remains degree-based: input_limit and generation_truncated can use the existing one-generation text/markdown split allowance; output_limit only for rewrite, including v3 rewrite. V3 polish output_limit does not acquire a new retry. request_budget, provider failure, invalid_response and all HTML failures have no automatic split. Cancellation stops further sends even when no response can be delivered.

## Initialization instructions

The exact `instructions` text below fits within the first 512 Unicode code points:

```text
copyeditor sends polish_text body, context and background to this server and Vertex AI. This server does not persist them or candidates. Compare meaning and preservation before applying local edits. Use either text or items [{id,text,context?}], never both; html uses text only. Set language explicitly when known; otherwise the server default applies. lint_text accepts text and language and calls no model. Keep originals on errors and flags. Provider retention follows its own policy.
```

Retain that exact string when judgment is disabled. When enabled, use this complete string (under 512 Unicode code points):

```text
copyeditor sends polish_text body, permitted context/background and candidates to Vertex AI and TypeSafe AI. This server does not persist them or judgment results. Providers govern their own retention and processing regions. Compare meaning before applying edits. Use text or items [{id,text,context?}], never both; html uses text only. Set language when known. lint_text calls no model. Keep originals on errors and flags. Judgment is not permission or proof of meaning preservation.
```

## Audit log

One compact JSON event on stdout per entered tool call, including validation errors. Whitelist exactly: `timestamp` (UTC RFC3339), `user` (24 lowercase hex HMAC characters for authenticated Google sub, null for none), `tool` (the known name), `language` (supported language or null), `rules_version`, `model` (identifier/null), `usage`, `cost`, `latency_ms`, `status` (`ok`, `flagged`, `error`), `error_code` (Code/null), `model_calls`, `regenerated` (boolean), `rejected_count` and `unfixable_count` (nonnegative integers). On whole-request errors both counts are zero because no item result is committed. `flagged` covers either flag kind. This list does not permit input lengths, item IDs, term lists, findings, arbitrary messages or exception text.

Do not log body, context, background, candidate, diagnosis (including its status, expression, reason or count), email, raw sub, credentials, bearer tokens, OAuth query strings, headers, tracebacks, or serialized requests/results. Authentication denial occurs outside tool execution and emits no tool audit event. Startup warnings/errors use CTR-04's fixed strings only. Disable framework, SDK, HTTP access and debug logging before initialization. Infrastructure log suppression is the deployer's responsibility and must be documented; server non-persistence is not a claim about a model provider's retention policy.

Preserve exactly the same whitelist, without judgment values, states, counts-by-outcome, provider rows, policy hashes, or new free-text fields. For v3 the existing model/usage/cost/model_calls fields project the **editing row only**. latency_ms is whole request latency. These legacy audit fields do not claim total provider billing; v3 response provider rows are the complete observation surface. `rejected_count` includes deterministic and verification_rejected items, unfixable_count is unchanged, status flagged covers any flag, and whole-request errors have both counts zero. Detection insufficient/indeterminate/not_run alone is not flagged. This maintains the preexisting aggregate rejection field, without logging judgment details. Disabled audit bytes/semantics are unchanged. Explicitly extend the log prohibition to judgment request/response bodies, numeric probabilities, classifications, keys, HTTP headers, parser snippets and exception chains, in all sinks and startup paths.

## Executable contract examples

Every `json contract-case` block is parsed by `tests/contracts/test_ctr01_tools.py`. `provider` is a queue of mocked raw model payloads; each has normal STOP completion and unavailable usage unless `finish` overrides it. `expect` is a recursive subset of the payload; the test also validates the full output schema and isError. Tests use built-in rules, empty config terms and default ratios. No live model is involved. Before schema validation or provider serialization, recursively expand fixture-only objects `{"$repeat":[string, nonnegative_integer]}` to that string repeated the given number of times, and `{"$concat":[string_or_expansion, ...]}` to the concatenation of its recursively expanded elements. Expand `input`, `provider`, and `expect`; these objects are test notation, never tool/model fields. Other objects and arrays are recursively traversed unchanged. Require exactly the indicated single key, reject invalid expansion operands, and cap each expanded string at 1,048,576 code points. Rewrite queues begin with a diagnostic payload `{diagnoses: [{id, status, expression, reason}]}` followed by the candidate payloads; token-estimation RPCs are mocked as zero-token estimates, never consume this generation queue, and do not access a network. Budget/deadline/usage overrides are additionally tested by the real service unit tests. Every queued provider response must be consumed exactly once; unexpected calls fail the fixture.

```json contract-case
{"name":"text_success","tool":"polish_text","input":{"text":"Hello.","language":"en"},"provider":[{"items":[{"id":"text","text":"Hello.","flag":null}]}],"expect":{"status":"ok","text":"Hello.","flag":null,"regenerated":false,"model_calls":1}}
```

```json contract-case
{"name":"exclusive_routes","tool":"polish_text","input":{"text":"Hello.","items":[{"id":"a","text":"Hello."}]},"provider":[],"expect":{"status":"error","error":{"code":"invalid_input"},"model_called":false}}
```

```json contract-case
{"name":"partial_rejection","tool":"polish_text","input":{"items":[{"id":"a","text":"Pay 10."},{"id":"b","text":"Hello."}],"language":"en"},"provider":[{"items":[{"id":"a","text":"Pay 11.","flag":null},{"id":"b","text":"Hello.","flag":null}]},{"items":[{"id":"a","text":"Pay 12.","flag":null}]}],"expect":{"status":"ok","items":[{"id":"a","text":"Pay 10.","flag":{"kind":"rejected","checks":["numbers"]},"regenerated":true},{"id":"b","text":"Hello.","flag":null,"regenerated":false}],"model_calls":2}}
```

```json contract-case
{"name":"retry_integrity_discards_batch","tool":"polish_text","input":{"items":[{"id":"a","text":"Pay 10."},{"id":"b","text":"Hello."}],"language":"en"},"provider":[{"items":[{"id":"a","text":"Pay 11.","flag":null},{"id":"b","text":"Hello.","flag":null}]},{"items":[]}],"expect":{"status":"error","error":{"code":"invalid_response"},"model_calls":2}}
```

```json contract-case
{"name":"shared_html_retry","tool":"polish_text","input":{"text":"<p>Pay 10.</p>","format":"html","language":"en"},"provider":[{"items":[{"id":"text","text":"<p>Pay 11.</p>","flag":null}]},{"items":[{"id":"text","text":"<div>Pay 10.</div>","flag":null}]}],"expect":{"status":"error","error":{"code":"html_structure"},"model_calls":2,"regeneration_attempted":true}}
```

```json contract-case
{"name":"lint_has_no_provider","tool":"lint_text","input":{"text":"Hello.","language":"en"},"provider":[],"expect":{"status":"ok","model":null,"model_calls":0,"usage":{"input_tokens":0,"output_tokens":0,"total_tokens":0},"cost":null}}
```

```json contract-case
{"name":"candidate_length_12000","tool":"polish_text","input":{"text":{"$repeat":["a",8000]},"language":"en"},"provider":[{"items":[{"id":"text","text":{"$repeat":["a",12000]},"flag":null}]}],"expect":{"status":"ok","text":{"$repeat":["a",12000]},"flag":null,"regenerated":false,"model_calls":1}}
```

```json contract-case
{"name":"candidate_length_12001","tool":"polish_text","input":{"text":{"$repeat":["a",8000]},"language":"en"},"provider":[{"items":[{"id":"text","text":{"$repeat":["a",12001]},"flag":null}]}],"expect":{"status":"ok","text":{"$repeat":["a",12001]},"flag":null,"regenerated":false,"model_calls":1}}
```

```json contract-case
{"name":"candidate_length_16000","tool":"polish_text","input":{"text":{"$repeat":["a",8000]},"language":"en"},"provider":[{"items":[{"id":"text","text":{"$repeat":["a",16000]},"flag":null}]}],"expect":{"status":"ok","text":{"$repeat":["a",16000]},"flag":null,"regenerated":false,"model_calls":1}}
```

```json contract-case
{"name":"candidate_length_16001","tool":"polish_text","input":{"text":{"$repeat":["a",8000]},"language":"en"},"provider":[{"items":[{"id":"text","text":{"$repeat":["a",16001]},"flag":null}]}],"expect":{"status":"error","error":{"code":"output_limit"},"model_calls":1,"regeneration_attempted":false}}
```

```json contract-case
{"name":"blank_candidate","tool":"polish_text","input":{"text":"Hi.","language":"en"},"provider":[{"items":[{"id":"text","text":" \t\n\r 　","flag":null}]}],"expect":{"status":"error","error":{"code":"invalid_response"},"model_calls":1,"regeneration_attempted":false}}
```

```json contract-case
{"name":"empty_candidate","tool":"polish_text","input":{"text":"Hi.","language":"en"},"provider":[{"items":[{"id":"text","text":"","flag":null}]}],"expect":{"status":"error","error":{"code":"invalid_response"},"model_calls":1,"regeneration_attempted":false}}
```

```json contract-case
{"name":"blank_unfixable","tool":"polish_text","input":{"text":"Hi.","language":"en"},"provider":[{"items":[{"id":"text","text":"   ","flag":{"kind":"unfixable","reason":"Cannot preserve meaning."}}]}],"expect":{"status":"error","error":{"code":"invalid_response"},"model_calls":1,"regeneration_attempted":false}}
```

```json contract-case
{"name":"candidate_aggregate_16000","tool":"polish_text","input":{"items":[{"id":"a","text":{"$repeat":["a",4000]}},{"id":"b","text":{"$repeat":["a",4000]}}],"language":"en"},"provider":[{"items":[{"id":"a","text":{"$repeat":["a",8000]},"flag":null},{"id":"b","text":{"$repeat":["a",8000]},"flag":null}]}],"expect":{"status":"ok","model_calls":1}}
```

```json contract-case
{"name":"candidate_aggregate_16001","tool":"polish_text","input":{"items":[{"id":"a","text":{"$repeat":["a",4000]}},{"id":"b","text":{"$repeat":["a",4000]}}],"language":"en"},"provider":[{"items":[{"id":"a","text":{"$repeat":["a",8001]},"flag":null},{"id":"b","text":{"$repeat":["a",8000]},"flag":null}]}],"expect":{"status":"error","error":{"code":"output_limit"},"model_calls":1}}
```

```json contract-case
{"name":"retry_merge_exceeds_16000","tool":"polish_text","input":{"items":[{"id":"a","text":{"$repeat":["a",4000]}},{"id":"b","text":{"$concat":["1",{"$repeat":["a",4999]}]}}],"language":"en"},"provider":[{"items":[{"id":"a","text":{"$repeat":["a",8000]},"flag":null},{"id":"b","text":{"$concat":["2",{"$repeat":["a",7999]}]},"flag":null}]},{"items":[{"id":"b","text":{"$concat":["1",{"$repeat":["a",8000]}]},"flag":null}]}],"expect":{"status":"error","error":{"code":"output_limit"},"model_calls":2,"regeneration_attempted":true}}
```

```json contract-case
{"name":"retry_blank_discards_batch","tool":"polish_text","input":{"items":[{"id":"a","text":"Pay 10."},{"id":"b","text":"Hello."}],"language":"en"},"provider":[{"items":[{"id":"a","text":"Pay 11.","flag":null},{"id":"b","text":"Hello.","flag":null}]},{"items":[{"id":"a","text":"       ","flag":null}]}],"expect":{"status":"error","error":{"code":"invalid_response"},"model_calls":2}}
```

```json contract-case
{"name":"html_incomplete_original","tool":"polish_text","input":{"text":"<p title=\"x","format":"html","language":"en"},"provider":[],"expect":{"status":"error","error":{"code":"invalid_input"},"model_calls":0}}
```

```json contract-case
{"name":"html_incomplete_candidate","tool":"polish_text","input":{"text":"<p>Hello.</p>","format":"html","language":"en"},"provider":[{"items":[{"id":"text","text":"<p title=\"x","flag":null}]},{"items":[{"id":"text","text":"<p title=\"x","flag":null}]}],"expect":{"status":"error","error":{"code":"html_structure"},"model_calls":2}}
```

The first implementation task introduces fixture loading/expansion, comparison helpers, a fake provider queue and positive/negative tests of those helpers. Before a real consumer exists, collection explicitly reports its contract tests as unconnected; those tests are not executed or represented as passing skips/xfails. Each consumer implementation task makes its corresponding contract cases and schema tests mandatory in CI, including boundaries (limit −1 / limit / limit +1), unknown language, duplicate IDs, missing usage, privacy assertions and both retry-crossing directions as their consumers are introduced. The final integration task executes every contract case against the real consumer with a fake model provider and requires nonempty coverage with no skips, xfails or unconnected cases. Changes to this contract and its tests are made together.

```json contract-case
{"name":"rewrite_unchanged_text","tool":"polish_text","input":{"text":"Hello.","language":"en","degree":"rewrite"},"provider":[{"diagnoses":[{"id":"text","status":"no_issue","expression":null,"reason":null}]},{"items":[{"id":"text","text":"Hello.","flag":null}]}],"expect":{"status":"ok","schema_version":2,"degree":"rewrite","text":"Hello.","diagnosis":{"status":"no_issue","expression":null,"reason":null},"model_calls":2,"regenerated":false}}
```

```json contract-case
{"name":"rewrite_no_issue_changed","tool":"polish_text","input":{"text":"Hello.","language":"en","degree":"rewrite"},"provider":[{"diagnoses":[{"id":"text","status":"no_issue","expression":null,"reason":null}]},{"items":[{"id":"text","text":"Hi.","flag":null}]}],"expect":{"status":"error","schema_version":2,"error":{"code":"invalid_response"},"model_calls":2,"regeneration_attempted":false}}
```

```json contract-case
{"name":"rewrite_missing_diagnosis","tool":"polish_text","input":{"text":"Hello.","degree":"rewrite"},"provider":[{"diagnoses":[]}],"expect":{"status":"error","error":{"code":"invalid_response"},"model_calls":1}}
```

```json contract-case
{"name":"rewrite_issue_text","tool":"polish_text","input":{"text":"Open it in order to see it.","language":"en","degree":"rewrite"},"provider":[{"diagnoses":[{"id":"text","status":"issue","expression":"in order to","reason":"Use a direct purpose phrase."}]},{"items":[{"id":"text","text":"Open it to see it.","flag":null}]}],"expect":{"status":"ok","schema_version":2,"text":"Open it to see it.","diagnosis":{"status":"issue","expression":"in order to","reason":"Use a direct purpose phrase."},"model_calls":2,"regenerated":false}}
```

```json contract-case
{"name":"polish_explicit_preserves_v1","tool":"polish_text","input":{"text":"Hello.","language":"en","degree":"polish"},"provider":[{"items":[{"id":"text","text":"Hello.","flag":null}]}],"expect":{"status":"ok","schema_version":1,"model_calls":1}}
```

```json contract-case
{"name":"degree_invalid","tool":"polish_text","input":{"text":"Hello.","degree":"other"},"provider":[],"expect":{"status":"error","schema_version":1,"error":{"code":"invalid_input"},"model_calls":0}}
```

```json contract-case
{"name":"rewrite_diagnosis_limit","tool":"polish_text","input":{"text":"Hello.","degree":"rewrite"},"provider":[{"diagnoses":[{"id":"text","status":"issue","expression":"Hello","reason":{"$repeat":["x",321]}}]}],"expect":{"status":"error","error":{"code":"output_limit"},"model_calls":1}}
```

```json contract-case
{"name":"rewrite_items_original_order","tool":"polish_text","input":{"items":[{"id":"a","text":"Hello."},{"id":"b","text":"Welcome."}],"degree":"rewrite","language":"en"},"provider":[{"diagnoses":[{"id":"b","status":"no_issue","expression":null,"reason":null},{"id":"a","status":"no_issue","expression":null,"reason":null}]},{"items":[{"id":"b","text":"Welcome.","flag":null},{"id":"a","text":"Hello.","flag":null}]}],"expect":{"status":"ok","schema_version":2,"model_calls":2,"items":[{"id":"a","diagnosis":{"status":"no_issue","expression":null,"reason":null}},{"id":"b","diagnosis":{"status":"no_issue","expression":null,"reason":null}}]}}
```

```json contract-case
{"name":"rewrite_duplicate_diagnosis","tool":"polish_text","input":{"text":"Hello.","degree":"rewrite"},"provider":[{"diagnoses":[{"id":"text","status":"no_issue","expression":null,"reason":null},{"id":"text","status":"no_issue","expression":null,"reason":null}]}],"expect":{"status":"error","error":{"code":"invalid_response"},"model_calls":1}}
```

```json contract-case
{"name":"rewrite_extra_diagnosis","tool":"polish_text","input":{"text":"Hello.","degree":"rewrite"},"provider":[{"diagnoses":[{"id":"text","status":"no_issue","expression":null,"reason":null},{"id":"extra","status":"no_issue","expression":null,"reason":null}]}],"expect":{"status":"error","error":{"code":"invalid_response"},"model_calls":1}}
```

```json contract-case
{"name":"rewrite_no_issue_flag_cannot_hide_change","tool":"polish_text","input":{"text":"Hello.","degree":"rewrite"},"provider":[{"diagnoses":[{"id":"text","status":"no_issue","expression":null,"reason":null}]},{"items":[{"id":"text","text":"Hi.","flag":{"kind":"unfixable","reason":"Cannot edit."}}]}],"expect":{"status":"error","error":{"code":"invalid_response"},"model_calls":2,"regeneration_attempted":false}}
```

```json contract-case
{"name":"rewrite_truncated_candidate_discards_diagnosis","tool":"polish_text","input":{"text":"Hello.","degree":"rewrite"},"provider":[{"diagnoses":[{"id":"text","status":"no_issue","expression":null,"reason":null}]},{"finish":"truncated","items":[]}],"expect":{"status":"error","schema_version":2,"error":{"code":"generation_truncated"},"model_calls":2}}
```

## Consumer case additions

Retain every legacy case with judgment disabled and require byte/shape/call-sequence equality to its baseline. Add v3 cases with fake judgment and editing queues, original-item IDs bound locally rather than supplied by Jev. Each case must exhaust exactly its expected calls and fail on unexpected calls, with no real network:

| Case | Required observation |
|---|---|
| Default/explicit disabled; poisoned judgment key accessor and client constructor | v1/v2 baseline passes, neither accessor nor constructor used, no judgment traffic |
| Gate below floor, with any axes/action/confidence | insufficient unchanged; no editing preflight/generation or fake no_issue |
| Gate at/above floor with preserve_as_is and with each non-preserve option at zero/low/high confidence | eligible in all cases; only preserve_as_is uses maximum-axis fallback; selected distribution retained |
| No editable HTML prose | not_run/no_editable_prose, exact original, no provider calls |
| Eligible rewrite diagnostic no_issue / issue | no candidate for no_issue; one immutable issue diagnosis through generation/retry |
| Every verification axis fail, every axis middle, all pass | exact original with classification / exact original inconclusive / candidate |
| Deterministic retry then verification | only final surviving candidate checked, no third generation |
| Generated unchanged, unfixable, preservation rejection | verify unchanged unflagged; no verification for the two flags |
| Mixed items and HTML/text/markdown, ja/en/zh | input order/IDs retained, same control, HTML whole document |
| Final-item API error or invalid answer after prior successes | whole-request content discard, retained usage, zero later calls |
| V3 model_called accounting table | every row and flipped-boolean negative case; false never reported as proof of no transmission |
| Public floor classification and Choice | 0.53 equality and neighbors; verification 0.30/0.70 equality and immediate neighbors; threshold-only boundary ownership; exact option set/sum/ties/non-max/unknown/NaN; fallback axis ties; confidence changes alone never change control; unknown pair rejected |
| Whole-request state and deterministic packing | 7*N / 4*M questions; one call per phase when within limits; stable internal IDs, six fixed references on every detection batch, two local limit boundaries, singleton refusal, no failure-based repartition |
| Boundaries on inputs, probabilities, hashes, payloads, caps, call counts, deadlines | at-bound accepted where applicable, above rejected, booleans/NaN rejected |
| Deadline + invalid answer; invalid answer + overrun; valid answer + overrun | provider_timeout; invalid_response; request_budget respectively |
| Unknown/missing usage, nonzero free output, mixed currencies, no calls | null distinct from zero, no invented totals or costs |
| Invalid arguments with enabled setting; raw protocol errors | v3 application error before providers; protocol error unchanged |
| Every stdout/stderr/framework/HTTP/SDK sink, success/error/cancel | no source/candidate/diagnosis/judgment/key sentinel |
| Discovery on/off/unknown and unsupported version | no body probe; permission expanded or submission withheld |
| Related item with detection insufficient vs verification indeterminate | first is unchanged success and not a skip trigger; second propagates related skip; fabricated indeterminate detection under active pair is rejected |

These are requirements for the same PRs that introduce the consumers, not assertions that not-yet-connected v3 cases pass today. Exact JSON examples and schema fixtures are added alongside their real consumer; no weakened always-pass comparator or removed v1/v2 inventory is allowed.
