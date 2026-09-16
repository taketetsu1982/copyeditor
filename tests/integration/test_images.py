import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = '''
import asyncio, json
from copyeditor import __main__ as entry
from copyeditor import providers
from copyeditor.providers.base import GenerationResult, Usage
class Provider:
    async def generate(self, request):
        return GenerationResult(json.dumps({"items": [dict(id=i.id, text=i.text, flag=None) for i in request.items]}), "stop", Usage(0, 0, 0))
    async def aclose(self): pass
providers.create_provider = lambda config: Provider()
raise SystemExit(asyncio.run(entry.run()))
'''
CLIENT = '''
import asyncio, json, os, time, urllib.request
from pathlib import Path
from fastmcp import Client
from copyeditor.rules import load_rules
origin = "http://127.0.0.1:" + os.environ["PORT"]
deadline = time.monotonic() + 20
while True:
    try:
        with urllib.request.urlopen(origin + "/health", timeout=1) as response:
            assert json.load(response) == {"status": "ok"}
        break
    except OSError:
        if time.monotonic() > deadline: raise
        time.sleep(0.2)
assert os.getuid() != 0 and Path("/app/examples/en").is_dir()
assert Path("/etc/copyeditor/rules.d").is_dir()
assert not any(Path("/app", p).exists() for p in ("docs", "tmp", ".git", ".claude", ".codex"))
snapshot = load_rules()
base = load_rules(overlay=None)
async def call():
    async with Client(origin + "/mcp") as client:
        arguments = {"text": "Overlay in order to act."}
        if not Path("/etc/copyeditor/config.yaml").exists(): arguments["language"] = "en"
        result = await client.call_tool("polish_text", arguments)
        assert not result.is_error
        value = result.structured_content
        assert value["text"] == "Overlay in order to act." and value["language"] == "en"
        assert value["rules_version"] == snapshot.rules_version
        assert value["common_version"] == base.common_version
        return value
value = asyncio.run(call())
print(json.dumps(dict(value=value, config=Path("/etc/copyeditor/config.yaml").exists(),
    detectors=len(snapshot.languages["en"].detectors), base_detectors=len(base.languages["en"].detectors),
    version=snapshot.rules_version, base_version=base.rules_version)))
'''


def docker(*args):
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=True, timeout=300)


@pytest.fixture
def images(tmp_path):
    image, label = os.environ.get("COPYEDITOR_TEST_IMAGE"), os.environ.get("COPYEDITOR_TEST_LABEL")
    if not image:
        yield None
        return
    launcher = tmp_path / "launcher.py"
    launcher.write_text(LAUNCHER)
    launcher.chmod(0o644)
    def start(tag, mode):
        with socket.socket() as available:
            available.bind(("127.0.0.1", 0))
            port = available.getsockname()[1]
        name = label + "-" + mode
        docker("run", "-d", "--name", name, "--label", "copyeditor.test=" + label, "--network", "none",
               "--mount", f"type=bind,src={launcher},dst=/launcher.py,readonly", "-e", "GOOGLE_CLOUD_PROJECT=fixture",
               "-e", "PORT=" + str(port), "--entrypoint", "python", tag, "/launcher.py")
        return name
    yield image, label, start


@pytest.mark.consumer("CTR-04")
def test_ac_05_1_ac_05_4_ctr04_base_and_none_images(images, tmp_path):
    if images is None:
        subprocess.run(["bash", str(ROOT / "scripts/test_images.sh")], check=True, timeout=900,
                       env=dict(os.environ, COPYEDITOR_TEST_PYTHON=sys.executable))
        return
    image, label, start = images
    config = json.loads(docker("image", "inspect", image).stdout)[0]["Config"]
    assert config["User"] == "65532:65532" and config["Entrypoint"] == ["python", "-m", "copyeditor"]
    failure = subprocess.run(["docker", "run", "--rm", "--network", "none", "--label", "copyeditor.test=" + label, image],
                             capture_output=True, text=True, timeout=30)
    assert failure.returncode != 0 and failure.stdout == ""
    assert failure.stderr == "ERROR: missing_required at vertex.project.\n"
    original = (ROOT / "rules/en.md").read_text()
    overlay = original.replace("en-vocabulary-001", "en-vocabulary-901").replace('"protected_terms": []', '"protected_terms": ["Overlay"]')
    (tmp_path / "en.md").write_text(overlay)
    (tmp_path / "config.yaml").write_text("default_language: en\n")
    (tmp_path / "Dockerfile").write_text(f"FROM {image}\nCOPY config.yaml /etc/copyeditor/config.yaml\nCOPY en.md /etc/copyeditor/rules.d/en.md\n")
    derived = label + ":none"
    docker("build", "--network", "none", "--label", "copyeditor.test=" + label, "-t", derived, str(tmp_path))
    for mode, tag in (("base", image), ("none", derived)):
        name = start(tag, mode)
        state = json.loads(docker("inspect", name).stdout)[0]
        assert not state["Mounts"][0]["RW"] and state["HostConfig"]["NetworkMode"] == "none"
        report = json.loads(docker("exec", name, "python", "-c", CLIENT).stdout)
        assert len(docker("top", name).stdout.splitlines()) == 2
        assert report["config"] == (mode == "none")
        assert report["detectors"] == report["base_detectors"] + (mode == "none")
        assert (report["version"] != report["base_version"]) == (mode == "none")
        if mode == "none": assert "Overlay" in report["value"]["protected_terms"]
        logs = docker("logs", name)
        assert logs.stderr == "WARNING: copyeditor is listening without authentication.\n"
        assert len(logs.stdout.splitlines()) == 1 and json.loads(logs.stdout)["tool"] == "polish_text"


@pytest.mark.parametrize("stage", ["build", "test", "cleanup", "path"])
def test_ac_05_1_ac_05_4_ctr04_failed_run_cleanup(tmp_path, stage):
    binaries, temporary, calls = tmp_path / "bin", tmp_path / "temporary", tmp_path / "calls"
    binaries.mkdir()
    temporary.mkdir()
    fake = binaries / "docker"
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\n'
        'case "$1 $2" in "ps -aq") echo owned-container;; "image ls") echo owned-image;; esac\n'
        'if [ "$1" = build ] && [ "$STAGE" = build ]; then exit 23; fi\n'
        'if [ "$1" = rm ] && [ "$STAGE" = cleanup ]; then exit 17; fi\n')
    fake.chmod(0o755)
    python = binaries / "python"
    python.write_text('#!/bin/sh\nif [ "$STAGE" = path ]; then echo path-python >> "$CALLS"; exit 0; fi\n'
                      'if [ "$STAGE" = cleanup ]; then exit 0; fi\nexit 23\n')
    python.chmod(0o755)
    driver = binaries / "test_images.sh"
    driver.write_text((ROOT / "scripts/test_images.sh").read_text())
    assert not (tmp_path / ".venv").exists()
    result = subprocess.run(["bash", str(driver)], capture_output=True, text=True,
        env=dict({k: v for k, v in os.environ.items() if k != "COPYEDITOR_TEST_PYTHON"}, PATH=str(binaries) + ":" + os.environ["PATH"], TMPDIR=str(temporary),
                 CALLS=str(calls), STAGE=stage, **({"COPYEDITOR_TEST_PYTHON": str(python)} if stage != "path" else {})), timeout=30)
    assert result.returncode == (0 if stage == "path" else 1 if stage == "cleanup" else 23)
    commands = calls.read_text().splitlines()
    if stage == "path": assert "path-python" in commands
    assert "rm -f owned-container" in commands and "image rm -f owned-image" in commands
    selectors = [line for line in commands if line.startswith(("ps ", "image ls "))]
    assert len(selectors) == 2 and all("--filter label=copyeditor.test=copyeditor-images-" in line for line in selectors)
    assert list(temporary.iterdir()) == []
