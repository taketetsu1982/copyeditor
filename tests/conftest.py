from collections import Counter, defaultdict
from pathlib import Path
import runpy
import os
import subprocess
import sys

import pytest

PHASE2_MODULES = {"tests/integration/test_plugin_claude.py", "tests/integration/test_plugin_codex.py"}


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
    html = load_cases(root / "rules/common.md", "html-case")
    rules = load_rules(root / "rules", None)
    examples = runpy.run_path(str(root / "scripts/examples_to_promptfoo.py"))["load_examples"](root / "examples", rules)
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

    def pytest_runtest_logreport(self, report):
        self.reports[report.nodeid].append(report)

    def pytest_sessionfinish(self, session, exitstatus):
        complete = set()
        for item in self.items:
            reports = self.reports[item.nodeid]
            if (Counter(r.when for r in reports) == Counter(("setup", "call", "teardown"))
                    and all(r.passed and not hasattr(r, "wasxfail") for r in reports)):
                complete.add(item.nodeid)
        if not self.items or len(complete) != len(self.items) or exitstatus:
            self.errors.append("Incomplete or unsuccessful execution")
        if self.errors:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
        terminal = self.config.pluginmanager.getplugin("terminalreporter")
        if terminal:
            terminal.write_sep("=", "CONTRACTS FAIL" if self.errors else "CONTRACTS PASS")
            for error in self.errors:
                terminal.write_line(error)
            for entry, expected in self.groups.items():
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
