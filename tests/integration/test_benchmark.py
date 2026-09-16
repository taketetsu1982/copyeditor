import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.consumer("CTR-05")
def test_ac_04_3_ac_04_4_ctr03_ctr05_offline_runner(tmp_path):
    if os.environ.get("COPYEDITOR_BENCHMARK_CONTAINER") != "1":
        subprocess.run(["bash", str(ROOT / "scripts/test_fixtures.sh")], cwd=ROOT, check=True, timeout=900)
        return
    sys.path.insert(0, str(ROOT / "scripts"))
    from examples_to_promptfoo import convert, load_examples
    from copyeditor.rules import load_rules
    assert os.getuid() != 0
    assert all(not int((interface / "flags").read_text(), 16) & 1
               for interface in Path("/sys/class/net").iterdir() if interface.is_dir() and interface.name != "lo")
    assert os.environ["PROMPTFOO_DISABLE_TELEMETRY"] == os.environ["PROMPTFOO_DISABLE_UPDATE"] == "1"
    cases = load_examples(ROOT / "examples", load_rules(ROOT / "rules", None))
    assert len(cases) == len(list((ROOT / "examples").glob("*/*.yaml"))) > 0
    config = tmp_path / "cases.json"
    convert(config, cases)
    original = json.loads(config.read_text())
    command = ["node", "/opt/node_modules/promptfoo/dist/src/main.js", "eval", "-c", str(config),
               "--no-cache", "--no-progress-bar", "-o", str(tmp_path / "result.json")]
    for variant in ("success", "mutation", "adapter", "empty"):
        data = json.loads(json.dumps(original))
        if variant == "mutation":
            data["tests"][0]["vars"]["good"] = "123456789 changed facts"
        if variant == "adapter":
            broken = tmp_path / "broken.py"
            broken.write_text('def call_api(prompt, options, context):\n    raise RuntimeError("Adapter failed")\n')
            data["providers"][0]["id"] = "file://" + str(broken)
        if variant == "empty":
            empty = tmp_path / "empty"
            empty.mkdir()
            failed = subprocess.run([sys.executable, "-c",
                "from examples_to_promptfoo import *; convert(sys.argv[1], load_examples(sys.argv[2], load_rules(ROOT / 'rules', None)))",
                str(config), str(empty)], env=dict(os.environ, PYTHONPATH=str(ROOT / "scripts") + ":" + str(ROOT / "src")),
                capture_output=True, text=True)
            assert failed.returncode != 0
            with pytest.raises(ValueError):
                convert(config, [])
            continue
        config.write_text(json.dumps(data))
        (tmp_path / "result.json").unlink(missing_ok=True)
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
        assert (result.returncode == 0) == (variant == "success"), result.stdout + result.stderr
        report = json.loads((tmp_path / "result.json").read_text())
        stats = report["results"]["stats"]
        assert stats["successes"] + stats["failures"] + stats["errors"] == len(cases)
        if variant == "success":
            assert stats["successes"] == len(cases) and stats["failures"] == stats["errors"] == 0
            assert stats["tokenUsage"]["total"] == 0
        else:
            assert stats["failures"] + stats["errors"] > 0


@pytest.mark.parametrize("failure", ["build", "run"])
def test_ac_04_3_ac_04_4_ctr03_ctr05_failure_cleanup(tmp_path, failure):
    driver = ROOT / "scripts/test_fixtures.sh"
    assert driver.is_file()
    commands, temporary, binaries = tmp_path / "commands", tmp_path / "temporary", tmp_path / "bin"
    temporary.mkdir()
    binaries.mkdir()
    docker = binaries / "docker"
    docker.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$COMMANDS"\n'
                      'if [ "$1" = "$FAILURE" ]; then exit 17; fi\n')
    docker.chmod(0o755)
    result = subprocess.run(["bash", str(driver)], cwd=ROOT, capture_output=True, text=True,
        env=dict(os.environ, PATH=str(binaries) + ":" + os.environ["PATH"], TMPDIR=str(temporary),
                 COMMANDS=str(commands), FAILURE=failure), timeout=30)
    assert result.returncode == 17
    calls = commands.read_text().splitlines()
    build = next(call.split() for call in calls if call.startswith("build "))
    name = build[2]
    assert name.startswith("copyeditor-fixtures-")
    assert calls[-2:] == ["rm -f " + name, "image rm " + name]
    assert list(temporary.iterdir()) == []
    if failure == "run":
        assert any("--network none" in call for call in calls)
