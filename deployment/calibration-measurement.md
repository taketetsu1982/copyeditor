# Preparing raw calibration measurements

The measurement workflow collects original gates and owner-labelled candidate pairs without a registered production threshold. It never changes a registry, starts the editing service in judgment mode, calls Vertex, or declares quality acceptance. Any editing candidates needed beforehand must be obtained through the existing judgment-disabled editing contract or written by the owner. Meaning-breaking controls require owner preparation, not a relaxed editing prompt.

A real run requires separate owner authorization. Repository changes and a successful fixture run are not permission to send text or spend money. Only the fixed synthetic calibration population is supported; held-out execution remains unavailable here.

## Prepare and freeze

Use `scripts/judgment_evaluation.py plan --set calibration --pairs INPUT.json --plan PLAN.json --mode live`. Planning is offline and does not read a judgment secret. The JSON input has exactly:

- `owner`: a nonblank owner identity.
- `editing_model`: `gemini-3.1-flash-lite`, pinned for subsequent editing work; not called by this runner.
- `tone`: the owner's nonblank explicit tone for the second condition.
- `labels`: every calibration source ID mapped to `{kind, reason}`, with owner-confirmed `problem` or `natural` labels.
- `pairs`: exactly eight labelled pairs per source. Each has `id` (`source_id/kind`), `source_id`, `kind`, `candidate`, `accepted` and `reason`. Kinds are `improved`, `same`, `unimproved`, `unrelated`, `meaning`, `negation`, `condition`, `promise`. Only a genuinely owner-approved `improved` pair has `accepted: true`; all controls have false. Only `same` may equal the original. Do not manufacture an improvement label for a natural source that has no valid improvement.
- `search_limit`: a positive integer bounding the finite local threshold search.
- `budget`: positive integer `max_calls`, `max_input_units`, `max_output_tokens`, `max_seconds`; positive `max_cost`; a three-letter `currency`; and known nonnegative `input_per_million` / `output_per_million` prices. Decimal monetary values can be strings. Missing or unknown prices stop planning; they never mean zero.

The manifest pins source commit, population paths/hashes/origins, questions, references, rules, model IDs, packing, input labels, every request and its limits. Freeze it again after changing code or inputs. The manifest hash is SHA-256 of `judgment_evaluation.encoded(plan).encode()`; the owner reviews this exact manifest, including its reservations.

There are two separately identified conditions: no background and explicit tone. The fixed judgment policy intentionally excludes tone from provider state; the conditions do not silently change that policy. Original gates use both degree labels and five repeats: 600 trials. The 240 candidate pairs use five repeats per condition: 2,400 trials. A source trial makes one call; a pair trial makes a fresh source-gate call and a candidate-gate/meaning call, for 5,400 singleton calls. This avoids sharing or guessing source observations. Packing regression and held-out examples are not added to this denominator.

All JSON bodies are prepared before authorization or communication, so an oversized pair prevents the entire run from starting. The runner uses existing fixed questions, references, byte packing and response validation. Provider state contains only the contract fields, not source IDs, owner labels, reasons or reference answers from examples.

## Authorize and run

The owner supplies a separate approval JSON with exactly these fields:

```json
{
  "owner": "OWNER",
  "manifest_hash": "REVIEWED_MANIFEST_SHA256",
  "scope": "synthetic-calibration-only; TypeSafe raw judgments; no Vertex; no registration",
  "mode": "live",
  "approved": false,
  "approved_at": "OWNER_APPROVAL_TIMESTAMP_WITH_TIMEZONE",
  "expires_at": "OWNER_EXPIRY_TIMESTAMP_WITH_TIMEZONE",
  "artifact_path": "/absolute/path/to/new/private/observations.json"
}
```

This template is deliberately not approved. The owner must set the decision and timestamps for the reviewed manifest. Keep inputs, labels and evidence in private storage such as the ignored repository `tmp/`, not in a public PR.

After authorization, use:

```sh
python scripts/judgment_evaluation.py run --mode live \
  --plan PLAN.json --approval APPROVAL.json --artifact OBSERVATIONS.json
```

Supply `TYPESAFE_API_KEY` through runtime secret injection. Do not put its value in arguments, files, logs or an approval. The runner checks approval identity, hash, scope, mode, expiry, artifact path and the entire plan before constructing a provider or loading this secret.

The artifact path is reserved exclusively. An existing artifact cannot be resumed or overwritten by `run`: failed or interrupted calls must not be replayed under the same approval. Each started slot is saved before sending, including interrupted slots. An upstream failure, timeout, cancellation, missing usage, reservation overrun, expiry or deadline stops the run before another call. Costs are reserved for all calls before starting, with no refunds for started slots. Reservation budgets are admission controls, not a provider billing guarantee; usage exceeding a reservation is recorded and stops further calls.

Use `--mode fixture` in both planning and approval for an offline rehearsal. Fixture mode blocks sockets and DNS and uses synthetic scores; it cannot establish real separation or authorize numerical registration.

## Select and inspect unregistered candidates

```sh
python scripts/judgment_evaluation.py calibrate --mode live \
  --plan PLAN.json --artifact OBSERVATIONS.json
python scripts/judgment_evaluation.py verify --mode live \
  --plan PLAN.json --artifact OBSERVATIONS.json --thresholds CANDIDATES.json
python scripts/judgment_evaluation.py verify-report --mode live \
  --plan PLAN.json --artifact OBSERVATIONS.json
```

These commands are offline, even for a live artifact. `CANDIDATES.json` has exactly `floor`, `gap`, `meaning_floor`; decimal strings retain exact midpoint precision. No synthetic registry is installed. The shared finite search records N/U/G, enforces its search limit, rejects nonpositive separation and infeasible controls, and keeps the existing tie-break. Invalid or incomplete observations cannot produce candidates.

`verify` evaluates the supplied numbers against the complete raw evidence and reports detection errors, accepted improvements and unsafe controls. `verify-report` recomputes that evaluation, rather than trusting a stored success flag. Reports always retain `quality_accepted: false` and `production_registered: false`.

Raw pair feasibility is not an end-to-end OFF/ON comparison, a claim of naturalness, or registration evidence by itself. A separately reviewed registration and calibration remeasurement on the registered revision remain necessary, followed by the unused held-out and client acceptance work. Do not enable production or publish a tag from this workflow.
