---
contract-id: CTR-05
kind: schema
derives-from: [AC-03-4, AC-04-1, AC-04-4, AC-04-5, AC-04-6, AC-04-7, AC-07-8, AC-07-9, AC-07-13]
revision: 3
---

# Copyediting examples

## File format

Store one YAML 1.2 mapping in `examples/<lang>/<id>.yaml`, UTF-8, with no duplicate keys, aliases or custom tags. Reject unknown fields. Null is permitted only for `lint`. The directory language must have a built-in rule file. IDs match `[a-z][a-z0-9-]{0,63}` and equal the filename stem; the `(language,id)` pair is unique. English headings/reasons/metadata accompany target-language bad/good text. Examples must be synthetic or approved public material, not secrets, user documents or proprietary source copy.

| Field | Required / type | Meaning |
|---|---|---|
| `id` | required string | stable example identifier |
| `language` | required string | matches directory and CTR-03 identifier |
| `section` | required enum | `vocabulary`, `syntax`, `structure`, `translation`, `context` |
| `bad`, `good` | required nonblank strings | original and human-authored acceptable candidate; each within CTR-01 text limits |
| `reason` | required nonblank English string, 1–1,000 code points | why this change helps and what remains unchanged |
| `format` | optional enum | `text` (default), `markdown`, `html` |
| `background` | optional mapping | only audience/purpose/tone/message, CTR-01 limits; default `{}` |
| `protected_terms` | optional string array | additional exact terms, CTR-04 bounds; default `[]` |
| `lint` | required null or mapping | null for a semantic-only example; otherwise exactly `rule_ids`: nonempty distinct array of IDs from its language rules |
| `must_change` | optional boolean | default true; if true bad and good must differ |
| `degree` | optional enum | `polish` (default), `rewrite` |
| `rewrite_expectations` | required mapping exactly when degree is rewrite; forbidden otherwise | exactly `problems` and `invariants`, defined below; evaluation-only, never model input |
| `regression` | optional enum | `protected-terms-overreach`; omitted otherwise |

No per-example ratio override: use the image defaults in CTR-04, so fixtures cannot quietly loosen the preservation gate. `must_change: false` permits a naturally worded original; reason must explain why to retain it. The example's protected terms add to built-in rules for fixture evaluation, not a public tool argument. No arbitrary assertion code is allowed in examples.

This illustrates the schema only; actual rule IDs must exist before this example can be installed:

```yaml
id: direct-purpose
language: en
section: vocabulary
bad: "Open the page in order to view 10 results."
good: "Open the page to view 10 results."
reason: "The shorter phrase retains the purpose and the number of results."
format: text
protected_terms: []
lint:
  rule_ids: [en-vocabulary-001]
must_change: true
```

## Rewrite evaluation examples

`rewrite_expectations.problems` and `.invariants` are arrays of distinct, nonblank English strings, each 1–320 code points, at most eight entries each. Invariants has at least one entry and states source facts, relationships, negation/conditions/promise strength and register that must not change. Problems names undesirable source expressions and why they must not survive; it is nonempty when `must_change: true`, empty when false. For rewrite with `must_change: false`, require `bad == good` exactly. A naturally phrased source must remain byte-for-byte equivalent as decoded Unicode, not merely pass length/preservation checks. Nulls and extra keys are forbidden. `good` is one human-authored acceptable example, not the unique live answer.

Use newly composed Japanese examples for the observed LP failure types (fashionable wording, aphoristic endings, patterned repetition and literal translation), natural nonchange including polite/plain register, and tempting changes to facts, numbers, negation and promises. Do not copy the owner's LP. Keep all existing lint coverage and protected-terms-overreach examples. Adding semantic rewrite examples does not replace detector-backed examples or Japanese native review.

CI validates this schema, runs fixture candidates through the actual rewrite pipeline with fake diagnosis and generation, and checks preservation and nonchange. Human review of live synthetic examples decides whether the listed problems were resolved without new problems or meaning changes; regex matching or a model's own diagnosis is not proof. Criteria and the repetition protocol are frozen before running, and a report must identify their revision, fixture hashes, model, thinking, prompt and rules versions. No live evaluation is run by default CI.

## Deterministic assertions

For every example, CI loads the effective built-in rules with its extra terms, validates the schema, and evaluates bad→good with the **same preservation implementation** as the server. Every CTR-02 check must pass, including variables as well as terms, numbers, URLs and ratio. HTML additionally passes the same structure comparison. These are necessary conditions, not a proof of semantic quality. Each declared lint rule must occur in bad findings and must not occur in good findings. Check the complete uncapped detector matches, not just the response's first 100. Other rule IDs are permitted in either text, allowing a focused example to survive unrelated rule additions.

`lint: null` is for prose criteria that the string-based detectors cannot decide; it is explicitly not evidence for AC-03-4. CI requires at least one detector-backed bad/good pair for **every** mechanical rule ID, and at least one example for each of the five prose sections of every built-in language. Thus a language containing only semantic examples cannot satisfy lint coverage. The human review for Japanese criteria and representative examples belongs in the PR, not a boolean generated by CI.

At least one Japanese example must have `regression: protected-terms-overreach`, `must_change: true`, and nonempty protected terms containing an actual product/official name. Its bad/good pair changes surrounding generic prose while preserving that term. The assertion also checks good differs from bad and is accepted without rejection using the fake provider. Do not solve this regression by protecting the entire source sentence or banning all copy changes. This models the previous LP failure mechanism using newly authored text, not copied proprietary wording.

The assertion suite additionally mutates a fixture candidate to remove a protected term, change a number/URL/variable, and exceed ratio bounds using synthetic fixtures where each token exists. Each mutation must fail its named check. This demonstrates that positive fixtures are not passing through an assertion that always returns true.

## Promptfoo conversion

`scripts/examples_to_promptfoo.py --output <path>` sorts files by `(language,id)`, validates them, and emits one test case per example. Conversion fails on an empty set or invalid case. Relative `file://` paths in generated config are calculated from its output directory to the repository adapters. The generated config uses these interfaces:

```yaml
prompts:
  - "{{bad}}"
providers:
  - id: file://../scripts/benchmark_provider.py
    config:
      mode: fixture
tests:
  - description: en/direct-purpose
    vars:
      id: direct-purpose
      language: en
      section: vocabulary
      bad: "Open the page in order to view 10 results."
      good: "Open the page to view 10 results."
      reason: "The shorter phrase retains the purpose and the number of results."
      format: text
      degree: polish
      background: {}
      protected_terms: []
      lint: {rule_ids: [en-vocabulary-001]}
      must_change: true
    assert:
      - type: python
        value: file://../scripts/benchmark_assert.py
```

This path example assumes output in a direct child of the repository. Omitted optional source fields become their explicit defaults in vars; omitted degree becomes `polish`; omitted regression and rewrite_expectations remain omitted vars keys. No other source field is dropped or inferred. This uses Promptfoo's [test vars and assertions](https://www.promptfoo.dev/docs/configuration/test-cases/) and [Python provider interface](https://www.promptfoo.dev/docs/providers/python/).

`benchmark_provider.py` implements `call_api(prompt, options, context)` with `context.vars` as validated example data. In `fixture` mode it invokes the server's in-process polish pipeline with a fake provider returning `good`; for rewrite the fake first returns one valid diagnosis (no_issue for nonchange, otherwise an issue expression from bad with a short reason), then good through the real candidate path; returns `{"output": <compact JSON string of the full CTR-01 text result>}`. No auth bypass is exposed by the production server; this is a test-only dependency injection. It must raise on any attempted network access. The assertion adapter `get_assert(output, context)` returns a boolean: schema-valid successful result, null flag, deterministic bad→returned-text checks, must-change if required, and the lint expectations evaluated on the fixture pair. In fixture mode it also requires returned text exactly equal to good. Failures are test failures, never converted to passing empty outputs.

`--live` emits `mode: live` instead; it is an explicit benchmark opt-in with synthetic public examples and runtime ADC/config. The adapter uses the same in-process service and real configured provider. It does not pass good/reason/lint assertions to the model, only bad, language, format, degree and permitted background. Neither rewrite_expectations, good, reason nor the evaluator’s diagnosis is sent to the live provider. The production server creates its own diagnosis. For rewrite nonchange examples the live assertion additionally requires returned text exactly equal to bad. Other live assertions do not require equality with good, but still require the returned candidate's preservation, null flag, and must-change where declared. Lint expectations for bad/good remain fixture tests; semantic quality of live candidates requires human review. Live mode never runs on default PR CI and never silently falls back to fixture mode.

Pin the Promptfoo CLI and Python dependencies in the implementation's lockfiles, turn off Promptfoo telemetry, and run fixture mode with networking denied. Generated reports may contain example text; never feed private production requests into this benchmark. The server audit restrictions remain unchanged.

## Verification

`tests/contracts/test_ctr05_examples.py` validates schema, coverage, conversion ordering and all mapped vars. CI runs the converter and the generated Promptfoo fixture evaluation, with zero failed assertions and zero external model calls. The first implementation task introduces fixture helpers and their positive/negative self-tests. Before real consumers exist, collection explicitly reports their tests as unconnected, without executing them or treating skips/xfails as passes. The tasks implementing language examples and the server-backed conversion/evaluation adapters make their corresponding contract checks mandatory in CI as each consumer is introduced. The final integration task evaluates all examples and fixtures, rejects an empty set, and permits no skips, xfails or unconnected checks. Later language edits must add/update their examples in the same PR. Native review and the attribution/originality check remain release/PR evidence, not fabricated automated results.
