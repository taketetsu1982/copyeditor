# Generation-four acceptance evidence

Mandatory CI checks machine-verifiable behavior and evidence formats. It does not accept native quality, actual client behavior or a production deployment. Merging an intermediate task is not acceptance of every requirement below.

Run `python -m pytest tests --require-phase1-contracts -n 2 --dist loadfile -q` without live-provider keys. Production judgment defaults to OFF; ON tests inject a synthetic registry only. Missing, replaced, skipped or unsuccessfully executed required consumers must fail. The existing authentication, packaging, provenance and release evidence gates remain required.

## Must and contract ownership

Task 166 owns the verification entry for all IDs in this table. Modules contain real consumers or explicit static/evidence-format checks, as described; a module name alone is not evidence of successful execution. All listed modules must be collected and the complete strict run must pass. Task 177 gathers outstanding records without transferring this ownership.

| Requirements | Consumers | Owner evidence | What the automated evidence establishes |
| --- | --- | --- | --- |
| AC-01-1, AC-01-3, AC-01-4, AC-01-5, AC-01-6, AC-01-7, AC-01-8, AC-01-9, AC-01-10, AC-01-11 | `tests/integration/test_rewrite_skill.py`; `tests/integration/test_judgment_skill.py`; `tests/integration/test_generation4_transport.py` | client | Static instructions and HTTP behavior; actual scoped edits, refusal/non-delivery and reports need both clients. |
| AC-02-1, AC-02-4, AC-02-5 | `tests/contracts/test_ctr01_requests.py`; `tests/contracts/test_ctr01_tools.py`; `tests/unit/test_edit_generation.py` | none | Real request/service consumers reject invalid routes, IDs, limits and partial responses. |
| AC-02-2, AC-02-10, AC-02-11, AC-07-3, AC-07-4 | `tests/unit/test_edit_pipeline.py`; `tests/unit/test_edit_rewrite_pipeline.py`; `tests/contracts/test_ctr02_preservation.py` | native | Candidate retention, excluded originals, preservation and shared retry; semantic quality still requires human judgment. |
| AC-02-3 | `tests/unit/test_judged_metrics.py`; `tests/integration/test_generation4_transport.py` | none | Provider accounting preserves known zero, unknown usage, costs and latency. |
| AC-02-6, AC-02-17 | `tests/unit/test_language_detection.py`; `tests/unit/test_edit_service.py` | none | Explicit language or one body-based inference, without default fallback. |
| AC-02-8, AC-07-11, AC-08-9 | `tests/integration/test_judgment_transport.py`; `tests/integration/test_rewrite_transport.py` | none | Success and failure audit records exclude private body, diagnosis and judgment data. |
| AC-02-14, AC-02-15, AC-02-18 | `tests/unit/test_default_style.py`; `tests/unit/test_edit_service.py`; `tests/unit/test_judgment_v2_batch.py` | native | Optional background and stable editing-only style; naturalness/register quality is not inferred from fixtures. |
| AC-02-16, AC-04-1 | `tests/unit/test_default_style.py`; `tests/contracts/test_ctr03_ja.py`; `tests/acceptance/test_native.py` | native | Built-in language assets and evidence validation; Japanese default style needs current native review. |
| AC-04-3, AC-04-4, AC-04-5 | `tests/contracts/test_ctr03_rules.py`; `tests/contracts/test_ctr05_examples.py`; `tests/contracts/test_ctr03_ja.py` | none | Installed rule headings and independent deterministic example regression. |
| AC-04-7 | `tests/acceptance/test_provenance.py`; `tests/acceptance/test_publication_gate.py` | release | Evidence validation cannot establish originality or authorize publication. |
| AC-05-1, AC-05-4, AC-05-10 | `tests/integration/test_images.py`; `tests/acceptance/test_release_evidence.py`; `tests/acceptance/test_publication_gate.py` | deployment, release | Local images and evidence validators do not replace fresh environment, derived image, Google login and Release records. |
| AC-05-2, AC-05-9 | `tests/contracts/test_ctr04_config.py`; `tests/integration/test_startup.py`; `tests/integration/test_judgment_startup.py` | none | Current configuration precedence, defaults, rejected retired keys and before-bind failures. |
| AC-05-3 | `tests/integration/test_auth.py`; `tests/unit/test_auth_boundary.py` | deployment | Offline OAuth/auth boundaries; actual Google setup needs owner evidence. |
| AC-05-5 | `tests/unit/test_vertex.py`; `tests/unit/test_rewrite_vertex.py` | none | One configured editor provider and simultaneous generation adapter. |
| AC-05-7 | `tests/integration/test_documentation.py`; `tests/integration/test_distribution.py` | deployment | Bilingual instructions and license checks; fresh setup usability remains unaccepted. |
| AC-05-8 | `tests/integration/test_startup.py`; `tests/integration/test_images.py` | none | Unauthenticated startup warning is distinct from Google mode. |
| AC-06-1, AC-06-3, AC-06-4 | `tests/integration/test_plugin_claude.py`; `tests/integration/test_plugin_codex.py` | client | Package and approval-policy checks; real installation and approval denial/non-delivery remain required. |
| AC-07-6, AC-08-8 | `tests/unit/test_edit_rewrite_pipeline.py`; `tests/unit/test_judgment_v2_batch.py`; `tests/unit/test_rewrite_vertex.py` | none | Untrusted input cannot alter fixed instructions; judgment excludes editing tone. |
| AC-07-12 | `tests/unit/test_edit_generation.py`; `tests/unit/test_edit_rewrite_pipeline.py`; `tests/integration/test_rewrite_skill.py` | client | Atomic body/diagnosis limits and static split boundaries; real-client retry behavior remains required. |
| AC-08-1, AC-08-7, AC-08-12 | `tests/integration/test_generation4_transport.py`; `tests/integration/test_judgment_startup.py`; `tests/unit/test_judged_budget.py` | none | Default OFF, synthetic ON, startup refusal, bounded calls and stopped follow-up sends. |
| AC-08-3, AC-08-5, AC-08-6 | `tests/unit/test_edit_pipeline.py`; `tests/unit/test_judgment_v2.py`; `tests/contracts/test_generation4_schema.py`; `tests/integration/test_judgment_skill.py` | client | Detection states, internal verification retry and flag-based application; client compliance needs owner evidence. |
| AC-08-11, AC-08-18 | `tests/integration/test_judgment_startup.py`; `tests/integration/test_judgment_transport.py`; `tests/integration/test_documentation.py` | none | Destination disclosure is not per-request consent for clients without the Skill. |
| CTR-01 | `tests/contracts/test_ctr01_tools.py`; `tests/contracts/test_generation4_schema.py`; `tests/integration/test_generation4_transport.py` | none | Complete v4 input/output and current public service behavior. |
| CTR-02 | `tests/contracts/test_ctr02_preservation.py`; `tests/unit/test_edit_pipeline.py`; `tests/integration/test_rewrite_skill.py` | client | Current deterministic preservation and Skill handling, without retired HTML terminal or local semantic checks. |
| CTR-04 | `tests/contracts/test_ctr04_config.py`; `tests/unit/test_config_v4.py`; `tests/integration/test_judgment_startup.py` | none | Current configuration, empty production registry and credential/startup boundaries. |

## Outstanding owner evidence

No owner acceptance is asserted by this document. Until records for the current revision and assets are supplied, the following remain **NOT EVALUATED**:

- **native**: Japanese rules/examples and the built-in default style, naturalness, meaning, register and invariants. Retain the unreviewed status for English and Chinese.
- **client**: both Claude and Codex, explicit/implicit invocation, scoped transmission/application, permission refusal with non-delivery, failure reporting and retry boundaries. Static Skill text is not client execution.
- **deployment**: the documented fresh Vertex project/device path, stock and derived images, configuration/rule overlays and actual Google authentication, with a successful editing call.
- **release**: owner provenance/native records and existing publication/release evidence, including date, immutable image identity, source revision and client. A previous release or PR merge cannot certify changed assets.

Each record must name its requirement IDs, observer, date, source commit, rules/common versions, relevant model and policy/threshold versions and hashes, client/image identity, inputs or synthetic artifacts, observed results and unresolved failures. Link the existing evidence artifact in the relevant PR; never attach credentials or private originals. Existing evidence validators still apply; arbitrary status text cannot replace them.

Actual live API execution and real-client acceptance require explicit owner authorization for the concrete inputs, labels, models, planned trials and cost/time limits. Missing records stay pending; do not turn zero trials, all unchanged outputs, missing judgments or synthetic success into acceptance. The later held-out/calibration gate is separate. Do not publish or tag as part of this checklist.
