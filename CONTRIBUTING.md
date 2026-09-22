# Contributing

When adding or changing a rule, include an example in the same pull request. Use synthetic text or approved public material; do not copy user documents, secrets or proprietary source text.

## Rules and examples

Follow the language asset and lint definitions in [CTR-03](rules/README.md) and the YAML format in [CTR-05](examples/README.md). Store examples in `examples/<lang>/<id>.yaml`, with matching `id` and `language`, a valid `section`, target-language `bad` / `good` text and an English `reason` explaining both the problem and what must remain unchanged. Use `lint: null` for semantic-only cases, or the defined `rule_ids` for deterministic lint cases.

Include natural controls where appropriate. `bad` and `good` are examples, not a unique correct answer. Rewrite cases use `degree: rewrite` and the required `rewrite_expectations.problems` and `rewrite_expectations.invariants`. See CTR-05 for limits and optional fields rather than adding new fields.

## Validation

Run `python -m pytest tests --require-phase1-contracts -n 2 --dist=loadfile -q` and `git diff --check`. Docker must be available for image integration tests. CI selects documentation checks for documentation-only changes and the full suite for code, rule and example changes.

CI checks schemas, rule IDs, deterministic bad-detected/good-not-detected lint examples, preservation behavior, packaged assets and required consumer execution. Missing, replaced, skipped or unsuccessful required checks fail the strict suite. Updating a fixed evaluation population or consumer requires an explicit reviewed inventory update, not automatic regeneration during collection.

Passing fixtures does not prove naturalness, semantic quality or actual client behavior. Native review, approved live measurements and owner acceptance remain separate; English and Chinese assets are native-unverified. Publication and release need their existing owner evidence and authorization.
