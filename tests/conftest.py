from collections import Counter, defaultdict
from pathlib import Path
import runpy
import os
import subprocess
import sys

import pytest

PHASE2_MODULES = {"tests/integration/test_plugin_claude.py", "tests/integration/test_plugin_codex.py",
                  "tests/integration/test_plugin_distribution.py"}


REWRITE_MODULES = {
    'tests/contracts/test_rewrite_examples.py',
    'tests/integration/test_rewrite_acceptance.py',
    'tests/integration/test_rewrite_skill.py',
    'tests/integration/test_rewrite_transport.py',
    'tests/unit/test_diagnosis.py',
    'tests/unit/test_rewrite_budget.py',
    'tests/unit/test_rewrite_evaluation.py',
    'tests/unit/test_rewrite_failures.py',
    'tests/unit/test_rewrite_requests.py',
    'tests/unit/test_rewrite_response.py',
    'tests/unit/test_rewrite_service.py',
    'tests/unit/test_rewrite_vertex.py',
}
PHASE2_MODULES |= REWRITE_MODULES
REWRITE_TOOL_CASES = {
    'text_success',
    'exclusive_routes',
    'partial_rejection',
    'retry_integrity_discards_batch',
    'shared_html_retry',
    'lint_has_no_provider',
    'candidate_length_12000',
    'candidate_length_12001',
    'candidate_length_16000',
    'candidate_length_16001',
    'blank_candidate',
    'empty_candidate',
    'blank_unfixable',
    'candidate_aggregate_16000',
    'candidate_aggregate_16001',
    'retry_merge_exceeds_16000',
    'retry_blank_discards_batch',
    'html_incomplete_original',
    'html_incomplete_candidate',
    'rewrite_unchanged_text',
    'rewrite_no_issue_changed',
    'rewrite_missing_diagnosis',
    'rewrite_issue_text',
    'polish_explicit_preserves_v1',
    'degree_invalid',
    'rewrite_diagnosis_limit',
    'rewrite_items_original_order',
    'rewrite_duplicate_diagnosis',
    'rewrite_extra_diagnosis',
    'rewrite_no_issue_flag_cannot_hide_change',
    'rewrite_truncated_candidate_discards_diagnosis',
}


# Consumer pins are reviewed explicitly; never regenerate on collection.
JUDGMENT_MODULES = {
    'tests/unit/test_edit_service.py': (41, 'e6598872a26867f4fc28e63ed234d861d296e12c64ebba17d6b7f737abba37d3'),
    'tests/unit/test_lingua_dependency.py': (2, 'c5e57c5432750307aae49d0e9c0f9e9255517f4d6e58d4e6fa342d9db0ccfc68'),
    'tests/contracts/test_ctr01_tools.py': (90, '91ee763db5b044b2d19b8626be72c17d96086d9be93913e4cd2c05090485b83f'),
    'tests/contracts/test_generation4_schema.py': (60, '258c68dc0207788b09bc887f2ed6cd42070031ac668e6b0e63d30fd5a860e0ca'),
    'tests/unit/test_language_detection.py': (36, '05bee600a14c289d26e9edf81e1de50f1c7e13d2b3671862a45858fdf727dca7'),
    'tests/unit/test_judgment_v2.py': (46, '75192a2714ef155dda73d0c890f17604a38c3cfbf5a9a5924c8965273ed1281b'),
    'tests/unit/test_judgment_v2_batch.py': (28, '17eb5ae21ce0601bab8741966c5869c4eac20d4392c7cb0e06959fbd6560e6c6'),
    'tests/unit/test_edit_generation.py': (38, '75ae8ee49ba9bf43b3f98ef9f9d72f68d49cb7eef2d26e29c556754d1fcccdc9'),
    'tests/unit/test_config_v4.py': (13, '1f1c5bcee6689fb3d4ddfa4f2e8ea60875ca885f3e4a4ca34fa671e2500f150b'),
    'tests/unit/test_default_style.py': (10, 'adff79a91b9cd99ed34ea6b7aee59ccb7d16766de45a20cf089cf1060e8cdaf3'),
    'tests/unit/test_edit_pipeline.py': (19, '184463986af5190f8960699e452c2950994faedbfb69ffcaaf6fa209482785f7'),
    'tests/unit/test_edit_rewrite_pipeline.py': (12, '85df62b095943d72bcc52784970bc6341bf915cd6f6baa2a208462e04f916dea'),
    'tests/contracts/test_ctr04_config.py': (45, '1a9a09c49bb9c365c6c1ce7ff0809f5c1435f2d52c5452a6163944054b751749'),
    'tests/contracts/test_judged_final.py': (33, 'fe2b547e8acc28bdc7dac218401586028fe6dfdef03e37caf3422685a910fe5e'),
    'tests/contracts/test_judged_schema.py': (34, '2883974d560a6db31c50aa5afa72b705e4eacfe480c182cdf0b2c6a99165164f'),
    'tests/contracts/test_judged_tools.py': (71, 'aea73b1b37de58fde7c8be9e912505e9447bdfd809b9d80695d1b42b8e8a6d8b'),
    'tests/contracts/test_judgment_contract.py': (8, '2e3e96a41deb81e344207d151407ff2e5754d690e827cbfc8676073fab86f918'),
    'tests/integration/test_documentation.py': (6, '0270530cf9cb60e258b9348751ffe509cca70fed58fe1f16269fe0e746c14c72'),
    'tests/integration/test_images.py': (10, '5d1bdd5905278d652d12a665129f67886a21c23f4cd297cdf018c5bd1db1c633'),
    'tests/integration/test_judgment_skill.py': (11, 'bd458524f771071d2f4d95f404c784c79056e0083770bf396b8ec1cb119bb851'),
    'tests/integration/test_judgment_startup.py': (8, '47ee861e8905ce9f5155820d90dd318167fa12012e9464eacf4459b10caf0c9f'),
    'tests/integration/test_judgment_transport.py': (98, 'd99fd49796ba6b77cab066d3b9e7fae00a558d3c1b30a7e567ae065f0a4a80d0'),
    'tests/integration/test_rewrite_transport.py': (11, '478a6b36da4e00733b95b6435cb23313cd97a5404fa7e4c31528d37bfabbbc08'),
    'tests/unit/test_judged_budget.py': (29, 'c8389467771199cc4f82755ba0e81d252b811469e9adcd2a354de8bf3e28ed63'),
    'tests/unit/test_judged_metrics.py': (20, '93bfe6c13301952234c699e066654973e0229ea3eef5c04c4feec6352e25e2de'),
    'tests/unit/test_judged_rewrite.py': (15, '1ec5eafe2665b6dc5727d5f617402e13662694b6ade87df5d8d68f11ac2e01d2'),
    'tests/unit/test_judged_service.py': (10, '237b77b13d13809b9f423588c00a00370bd210f3ce9058385faf309c42240982'),
    'tests/unit/test_judgment.py': (181, '1b9470aafce99bfa5bbd80fcc84c415a8b0c6d35a341e3195310520db89829e8'),
    'tests/unit/test_judgment_batch.py': (14, '2faa53ba0b04f57d1532e48af62dc0ac5a025b9792e7c0efc590eecc621bb661'),
    'tests/unit/test_judgment_config.py': (45, '61ffc824970ea055d787481ba18d6c888885cb244ae5b9cea4829f4066982147'),
    'tests/unit/test_typesafe.py': (55, '902e9b58f919b6d39468dd56d0cbd158e6bebb4c5a894285b1330607204db3cf'),
}



# Evaluation ownership is separate from runtime consumers.
EVALUATION_MODULES = {
    'tests/unit/test_generation4_evaluation.py': (23, '643126d9e344ed7028f0f180953cb9ac990e7d868a34eb69bc00ad5a0b6807ce'),
    'tests/unit/test_judgment_examples.py': (74, '00d3c90298c579b37acd0a6d44b0b7c9e293bbdf63e7a3fb36e0c1c55f0ab9a9'),
    'tests/unit/test_judgment_evaluation.py': (66, 'df05969bf72742a1ad2920c4253cd232b8d9abb05c1ad89f5e215134d5acea88'),
    'tests/unit/test_judgment_calibration.py': (55, '933c1229814671451f8d6a9120ba9e48d61f5df92f4dbdd66d066ba6b76736fb'),
    'tests/integration/test_judgment_acceptance.py': (65, '283d046de3f56a77f4f7bd73c60d9db85b95aab510cf88499bd1ea15351b310f'),
}
EVALUATION_SETS = {
    'judgment-calibration': (30, '1f5bd7410d0a73864a0576dfc5565ef6d7dc67ee80bb6d7d130e98288679ef53'),
    'judgment-acceptance': (40, '6ec1c9ce07d9812f03b7791d28935050be541d4c9d8f27615fbb73b61e19e312'),
    'rewrite': (24, '248f782d0ee57fc1b137350c4e2dff5a6cda53aad3689a4851d3ae5b99f94617'),
}
US08_OWNER_EVIDENCE = {
    'live': 'owner-fixed labels, complete gate and verify calibration, blinded off/on comparisons and unused held-out evidence',
    'native': 'Japanese naturalness, meaning, register and invariant review for the fixed inputs and candidates',
    'client': 'Claude and Codex explicit/implicit invocation, send permission, denial non-delivery and result reporting',
}


def evaluation_inventory(root):
    import hashlib
    import json
    for prefix, (count, digest) in EVALUATION_SETS.items():
        paths = sorted((root / 'examples/ja').glob(prefix + '-*.yaml'))
        rows = [(p.relative_to(root).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest()) for p in paths]
        if [p.stem for p in paths] != [f'{prefix}-{i:02}' for i in range(1, count + 1)] or hashlib.sha256(json.dumps(rows).encode()).hexdigest() != digest:
            raise ValueError('Missing or changed evaluation population: ' + prefix)
    return {module + '::*': {digest: {'count': count}} for module, (count, digest) in EVALUATION_MODULES.items()}


def judgment_fingerprint(root, module, items):
    import ast
    import hashlib
    import json
    import re
    source = ast.dump(ast.parse((root / module).read_text()), include_attributes=False)
    rows = []
    for item in items:
        if item.nodeid.split('::')[0] == module:
            params = repr(getattr(getattr(item, 'callspec', None), 'params', {}))
            params = re.sub(r' at 0x[0-9a-f]+', '', params).replace(str(root), '<root>')
            rows.append((item.nodeid, params))
    return len(rows), hashlib.sha256(json.dumps((source, sorted(rows))).encode()).hexdigest()


def judgment_inventory():
    return {module + '::*': {digest: {'count': count}}
            for module, (count, digest) in JUDGMENT_MODULES.items()}

def pytest_report_collectionfinish(items):
    connected = {mark.args[0] for item in items
                 for mark in item.iter_markers("consumer")}
    return [f"UNCONNECTED CTR-{number:02}: no real consumer tests collected"
            for number in range(1, 6) if f"CTR-{number:02}" not in connected]


def phase1_inventory(root):
    from copyeditor.config import SCHEMA
    from copyeditor.rules import load_rules
    from tests.contracts.harness import load_cases

    tools = load_cases(root / "contracts/tools.md", "contract-case")
    if len(tools) != 31 or {case["name"] for case in tools} != REWRITE_TOOL_CASES:
        raise ValueError("Missing or replaced tool contract cases")
    html = load_cases(root / "rules/common.md", "html-case")
    rules = load_rules(root / "rules", None)
    examples = runpy.run_path(str(root / "scripts/examples_to_promptfoo.py"))["load_examples"](root / "examples", rules)
    fixed = {f"rewrite-{i:02}" for i in range(1, 25)}
    if not fixed <= {case["id"] for case in examples if case["language"] == "ja" and case["degree"] == "rewrite"}:
        raise ValueError("Missing fixed rewrite examples")
    if not {"ja", "en", "zh"} <= set(rules.languages):
        raise ValueError("Missing language assets")
    groups = {}

    def cases(module, function, values, parameter=None):
        values = list(values)
        payloads = {key: {parameter: value} for key, value in values} if parameter else {v: {} for v in values}
        if not values or len(values) != len(payloads):
            raise ValueError("Empty or duplicate fixture inventory")
        groups[f"tests/{module}.py::{function}"] = payloads

    for module, function, selected in [
        ("requests", "test_ctr01_real_contract_inputs", tools),
        ("responses", "test_ctr01_real_contract_provider_batches", [c for c in tools if c["provider"]]),
        ("responses", "test_ac_02_2_ctr01_complete_contract_output_shapes", tools),
        ("responses", "test_ctr01_final_contract_fixtures", tools),
        ("tools", "test_ac_02_2_ac_02_3_ac_02_4_ac_02_10_ac_02_11_ac_02_12_ctr01_contract_service", tools),
    ]:
        cases(f"contracts/test_ctr01_{module}", function, ((c["name"], c) for c in selected), "case")
    cases("contracts/test_ctr02_html", "test_ctr02_html_contract", ((c["name"], c) for c in html), "case")
    cases("contracts/test_ctr04_config", "test_ac_05_2_leaf_validation_and_precedence", ((name, name) for name in SCHEMA), "name")
    cases("contracts/test_ctr05_examples", "test_ac04_3_ac04_4_ac04_5_ctr03_ctr05_real_examples_accepted",
          ((c["language"] + "/" + c["id"], c) for c in examples), "case")
    cases("unit/test_auth_routes", "test_ac_05_3_sdk_error_routes_remove_details", ((v, v) for v in ("callback", "registration", "token", "callback-exception")), "kind")
    cases("acceptance/test_publication_gate", "test_ac_05_1_ac_05_7_ctr04_distinct_commits_and_language_scopes", ((str(v), v) for v in (False, True)), "updated")
    for module, function in [
        ("acceptance/test_native", "test_ac_04_1_login_case_and_crlf_are_accepted"),
        ("acceptance/test_provenance", "test_ac_04_7_owner_evidence_passes_without_published_run"),
        ("acceptance/test_release_evidence", "test_ac_05_1_ac_05_3_ac_05_4_ctr03_ctr05_missing_and_null_fields"),
        ("contracts/test_ctr02_preservation", "test_ctr02_ratio_has_no_rounding_or_whitespace_normalization"),
        ("unit/test_auth", "test_ctr04_none_does_not_initialize_google"),
        ("unit/test_auth_boundary", "test_ctr04_non_http_scope_is_delegated"),
        ("unit/test_html_lexical", "test_ctr02_all_fixed_tokenizer_state_targets_are_instrumented"),
        ("unit/test_html_source", "test_ctr02_trace_records_are_immutable"),
        ("unit/test_html_trace", "test_ctr02_trace_context_identity_and_lexical_events"),
        ("unit/test_lint", "test_ctr03_runtime_failure_is_not_empty"),
        ("unit/test_metrics", "test_ctr01_no_call_and_pending_failure"),
        ("unit/test_service", "test_ctr01_final_validator_failure_returns_fixed_error"),
        ("unit/test_vertex", "test_ac_05_5_startup_and_import_boundary"),
        ("integration/test_transport", "test_ac_02_1_ac_02_5_ac_02_6_ac_02_8_ctr01_discovery"),
        ("integration/test_startup", "test_ac_05_8_ac_05_9_ctr01_ctr04_writers_reject_unstructured_data"),
        ("integration/test_documentation", "test_ac_05_7_ctr04_bilingual_structure_and_commands"),
        ("contracts/test_ctr03_rules", "test_ctr03_installed_assets"),
        ("contracts/test_ctr03_ja", "test_ac_04_1_ctr03_ctr05_ja_assets_preserve_content"),
        ("contracts/test_ctr03_en_zh", "test_ctr03_ctr05_en_zh_assets_preserve_content"),
        ("contracts/test_ctr05_examples", "test_ctr05_installed_assets"),
        ("integration/test_images", "test_ac_05_1_ac_05_4_ctr04_all_images_and_secrets"),
        ("integration/test_distribution", "test_ac_05_7_ctr04_distribution_permissions_and_order"),
        ("integration/test_benchmark", "test_ac_04_3_ac_04_4_ctr03_ctr05_offline_runner"),
    ]:
        cases(module, function, [None])
    for suffix in ("real_oauth_round_trip", "oauth_rejections_and_expiry", "refresh_identity_and_empty_memory",
                   "real_registration_capacity", "idp_errors_and_exception_privacy"):
        cases("integration/test_auth", "test_ac_05_3_ctr04_" + suffix, [None])
    groups["tests/integration/test_judgment_inventory.py::test_ac_08_1_2_3_4_5_6_7_8_9_10_11_12_15_16_17_18_ctr01_ctr04_fixed_consumers"] = {None: {}}
    groups.update(judgment_inventory())
    groups.update(evaluation_inventory(root))
    return groups


def pytest_addoption(parser):
    parser.addoption("--require-phase1-acceptance", action="store_true", help="Require real release evidence")
    parser.addoption("--require-phase1-contracts", action="store_true", help="Require complete contract execution")


def pytest_configure(config):
    if (config.getoption("--require-phase1-contracts") or config.getoption("--require-phase1-acceptance")
            or "COPYEDITOR_ACCEPTANCE_EVIDENCE" in os.environ):
        config.pluginmanager.register(Phase1Contracts(config), "phase1-contracts")


class Phase1Contracts:
    def __init__(self, config):
        self.config, self.errors, self.items = config, [], []
        self.reports = defaultdict(list)
        self.nodeids = []
        self.groups = {}
        self.acceptance = config.getoption("--require-phase1-acceptance") or "COPYEDITOR_ACCEPTANCE_EVIDENCE" in os.environ
        self.evidence = os.environ.get("COPYEDITOR_ACCEPTANCE_EVIDENCE", "")
        self.owner = os.environ.get("COPYEDITOR_OWNER", "")
        if self.acceptance:
            # Nested image and self-test suites must not inherit final acceptance.
            captured = {key: os.environ.pop(key) for key in ("COPYEDITOR_ACCEPTANCE_EVIDENCE", "COPYEDITOR_OWNER") if key in os.environ}
            config.add_cleanup(lambda: os.environ.update(captured))

    def pytest_deselected(self, items):
        if items:
            self.errors.append("Partial collection")

    def pytest_collection_finish(self, session):
        self.items = session.items
        self.nodeids = [item.nodeid for item in self.items]
        root = self.config.rootpath
        if self.config.option.collectonly or [Path(a).resolve() for a in self.config.args] != [root / "tests"]:
            self.errors.append("Full execution of tests is required")
        if self.config.option.ignore or self.config.option.ignore_glob or self.config.option.deselect:
            self.errors.append("Collection exclusions are not allowed")
        self.errors.extend(pytest_report_collectionfinish(self.items))
        collected_modules = {item.nodeid.split("::")[0] for item in self.items}
        for module in sorted(PHASE2_MODULES - collected_modules):
            self.errors.append(f"Missing Phase 2 module: {module}")
        try:
            self.groups = phase1_inventory(root)
            for entry, expected in self.groups.items():
                if entry.endswith('::*'):
                    module = entry.removesuffix('::*')
                    try:
                        count, digest = judgment_fingerprint(root, module, self.items)
                        matches = expected == {digest: {'count': count}}
                    except (OSError, SyntaxError):
                        matches = False
                    if not matches: self.errors.append(f"Inventory mismatch: {entry}")
                    continue
                actual = [getattr(item, "callspec", None).id if hasattr(item, "callspec") else None
                          for item in self.items if item.nodeid.split("[")[0] == entry]
                if Counter(actual) != Counter(expected.keys()):
                    self.errors.append(f"Inventory mismatch: {entry}")
                for item in self.items:
                    if item.nodeid.split("[")[0] == entry and hasattr(item, "callspec"):
                        for key, value in expected.get(item.callspec.id, {}).items():
                            if item.callspec.params.get(key) != value:
                                self.errors.append(f"Parameter mismatch: {item.nodeid}")
        except Exception:
            self.errors.append("Invalid contract inventory")

    @pytest.hookimpl(optionalhook=True)
    def pytest_xdist_node_collection_finished(self, node, ids):
        if self.nodeids and self.nodeids != ids:
            self.errors.append("Worker collections differ")
        self.nodeids = list(ids)

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node, error):
        evidence = getattr(node, "workeroutput", {}).get("contracts")
        if error or not evidence or evidence["nodeids"] != self.nodeids:
            self.errors.append("Missing or inconsistent worker inventory")
        else:
            self.errors.extend(evidence["errors"])
            self.groups = evidence["groups"]

    def pytest_runtest_logreport(self, report):
        self.reports[report.nodeid].append(report)

    def pytest_sessionfinish(self, session, exitstatus):
        if hasattr(self.config, "workerinput"):
            # Each worker validates the full inventory, but executes only its assigned files.
            self.config.workeroutput["contracts"] = dict(
                nodeids=self.nodeids, errors=self.errors, groups=self.groups)
            return
        nodeids = self.nodeids or [item.nodeid for item in self.items]
        complete = set()
        for nodeid in nodeids:
            reports = self.reports[nodeid]
            if (Counter(r.when for r in reports) == Counter(("setup", "call", "teardown"))
                    and all(r.passed and not hasattr(r, "wasxfail") for r in reports)):
                complete.add(nodeid)
        if (not nodeids or len(complete) != len(nodeids) or exitstatus
                or set(self.reports) != set(nodeids)):
            self.errors.append("Incomplete or unsuccessful execution")
        if self.errors:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
        terminal = self.config.pluginmanager.getplugin("terminalreporter")
        if terminal:
            terminal.write_sep("=", "CONTRACTS FAIL" if self.errors else "CONTRACTS PASS")
            terminal.write_line("US-07 quality not evaluated: owner native/live/client evidence remains required.")
            for error in self.errors:
                terminal.write_line(error)
            terminal.write_line("US-08 live/native/client acceptance not evaluated; owner evidence remains required.")
            for kind, requirement in US08_OWNER_EVIDENCE.items():
                terminal.write_line(f"US-08 {kind}: NOT EVALUATED; required: {requirement}.")
            for entry, expected in self.groups.items():
                if entry.endswith('::*'):
                    executed = sum(n.split('::')[0] == entry.removesuffix('::*') for n in complete)
                    terminal.write_line(f"V3 {entry}: expected={next(iter(expected.values()))['count']} executed={executed}")
                    continue
                executed = sum(n.split("[")[0] == entry for n in complete)
                terminal.write_line(f"{entry}: expected={len(expected)} executed={executed}")

        if self.acceptance:
            accepted = False
            if not self.errors and self.evidence.strip() and self.owner.strip():
                try:
                    result = subprocess.run([sys.executable, str(self.config.rootpath / "scripts/check_evidence.py"),
                                             "release", "--evidence", self.evidence, "--owner", self.owner],
                                            capture_output=True, timeout=120)
                    accepted = result.returncode == 0
                except Exception:
                    pass
            if not accepted:
                session.exitstatus = pytest.ExitCode.TESTS_FAILED
            if terminal:
                terminal.write_sep("=", "ACCEPTANCE PASS" if accepted else "ACCEPTANCE FAIL")
