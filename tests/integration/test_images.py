import json
import io
import os
from pathlib import Path
import socket
import subprocess
import sys
import tarfile
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[2]
MARKER = "SYNTHETIC_IMAGE_SECRET_" + uuid.uuid4().hex
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
import os, time, httpx2
from copyeditor.auth import SCOPES
from copyeditor.server import PublicServer
from fastmcp.server.auth.oauth_proxy.models import UpstreamTokenSet, JTIMapping
original_run = PublicServer.run_http_async
async def run(self, **kwargs):
    if self.auth is None:
        return await original_run(self, **kwargs)
    marker = os.environ["GOOGLE_OAUTH_CLIENT_SECRET"]
    def respond(request):
        assert (request.url.host, request.url.path) in {
            ("oauth2.googleapis.com", "/tokeninfo"), ("www.googleapis.com", "/oauth2/v2/userinfo"),
            ("openidconnect.googleapis.com", "/v1/userinfo")}
        return httpx2.Response(200, json=dict(sub=marker, email="tester@example.com", email_verified=True,
            hd="example.com", aud="client", scope=" ".join(SCOPES), expires_in=3600))
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        self.auth._token_validator._http_client = client
        now = time.time()
        await self.auth._upstream_token_store.put(key="fixture", value=UpstreamTokenSet(upstream_token_id="fixture",
            access_token=marker, refresh_token=None, refresh_token_expires_at=None, expires_at=now+3600,
            token_type="Bearer", scope=" ".join(SCOPES), client_id="client", created_at=now), ttl=900)
        await self.auth._jti_mapping_store.put(key="fixture", value=JTIMapping(jti="fixture",
            upstream_token_id="fixture", created_at=now), ttl=900)
        return await original_run(self, **kwargs)
PublicServer.run_http_async = run
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
    from copyeditor.config import load_config
    from copyeditor.auth import make_auth, SCOPES
    token = None
    if load_config()["auth.mode"] == "google":
        try:
            urllib.request.urlopen(urllib.request.Request(origin + "/mcp", data=b"{}"), timeout=2)
            raise AssertionError("Anonymous request accepted")
        except urllib.error.HTTPError as error:
            assert error.code == 401
        proxy = make_auth(load_config())
        proxy.set_mcp_path("/mcp")
        token = proxy.jwt_issuer.issue_access_token(client_id="client", scopes=SCOPES, jti="fixture", expires_in=900)
    async with Client(origin + "/mcp", auth=token) as client:
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
def images(tmp_path, monkeypatch):
    image, label = os.environ.get("COPYEDITOR_TEST_IMAGE"), os.environ.get("COPYEDITOR_TEST_LABEL")
    if not image:
        yield None
        return
    launcher = tmp_path / "launcher.py"
    launcher.write_text(LAUNCHER)
    launcher.chmod(0o644)
    for key in ("GOOGLE_OAUTH_CLIENT_SECRET", "OAUTH_SIGNING_KEY"):
        monkeypatch.setenv(key, MARKER)
    def start(tag, mode):
        with socket.socket() as available:
            available.bind(("127.0.0.1", 0))
            port = available.getsockname()[1]
        name = label + "-" + mode
        secrets = ["-e", "GOOGLE_OAUTH_CLIENT_SECRET", "-e", "OAUTH_SIGNING_KEY"] if mode == "google" else []
        docker("run", "-d", "--name", name, "--label", "copyeditor.test=" + label, "--network", "none",
               "--mount", f"type=bind,src={launcher},dst=/launcher.py,readonly", "-e", "GOOGLE_CLOUD_PROJECT=fixture",
               "-e", "PORT=" + str(port), *secrets, "--entrypoint", "python", tag, "/launcher.py")
        return name
    yield image, label, start


@pytest.mark.consumer("CTR-04")
def test_ac_05_1_ac_05_4_ctr04_all_images_and_secrets(images, tmp_path):
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
    (tmp_path / "config.yaml").write_text('default_language: en\nauth:\n  mode: google\n  client_id: client\n'
        '  base_url: https://service.example\n  allowed_domains: [example.com]\n')
    google = label + ":google"
    docker("build", "--network", "none", "--label", "copyeditor.test=" + label, "-t", google, str(tmp_path))
    for mode, tag in (("base", image), ("none", derived), ("google", google)):
        name = start(tag, mode)
        state = json.loads(docker("inspect", name).stdout)[0]
        assert not state["Mounts"][0]["RW"] and state["HostConfig"]["NetworkMode"] == "none"
        report = json.loads(docker("exec", name, "python", "-c", CLIENT).stdout)
        assert len(docker("top", name).stdout.splitlines()) == 2
        assert report["config"] == (mode != "base")
        assert report["detectors"] == report["base_detectors"] + (mode != "base")
        assert (report["version"] != report["base_version"]) == (mode != "base")
        if mode != "base": assert "Overlay" in report["value"]["protected_terms"]
        logs = docker("logs", name)
        assert logs.stderr == ("" if mode == "google" else "WARNING: copyeditor is listening without authentication.\n")
        assert len(logs.stdout.splitlines()) == 1 and json.loads(logs.stdout)["tool"] == "polish_text"
        assert MARKER not in logs.stdout + logs.stderr
        assert any(MARKER in item for item in state["Config"]["Env"]) == (mode == "google")
        docker("stop", name)
        check_image_secrets(tag, tmp_path)
    excluded = ROOT / "tmp" / (label + "-secret")
    created = False
    try:
        excluded.parent.mkdir()
        created = True
    except FileExistsError:
        pass
    try:
        excluded.write_text(MARKER)
        context = label + ":context"
        subprocess.run(["docker", "build", "--network", "none", "--label", "copyeditor.test=" + label,
            "-t", context, "-f", "-", str(ROOT)], input="FROM scratch\nCOPY . /context\n",
            text=True, capture_output=True, check=True, timeout=300)
        check_image_secrets(context, tmp_path)
        (tmp_path / "leak").write_text(MARKER)
        (tmp_path / "Dockerfile").write_text(f"FROM {image}\nCOPY --chown=65532:65532 leak /tmp/removed-secret\nRUN rm /tmp/removed-secret\n")
        poisoned = label + ":leak-probe"
        docker("build", "--network", "none", "--label", "copyeditor.test=" + label, "-t", poisoned, str(tmp_path))
        with pytest.raises(AssertionError):
            check_image_secrets(poisoned, tmp_path)
    finally:
        excluded.unlink(missing_ok=True)
        if created:
            excluded.parent.rmdir()


def assert_secret_absent(stream):
    tail = b""
    while chunk := stream.read(1024 * 1024):
        combined = tail + chunk
        assert MARKER.encode() not in combined
        tail = combined[-len(MARKER):]


def check_image_secrets(tag, tmp_path):
    assert MARKER not in docker("history", "--no-trunc", "--format", "{{json .}}", tag).stdout
    archive = tmp_path / "image.tar"
    docker("save", "-o", str(archive), tag)
    with tarfile.open(archive) as outer:
        manifest = json.load(outer.extractfile("manifest.json"))
        assert manifest and all(entry["Layers"] for entry in manifest)
        for member in outer:
            if member.isfile(): assert_secret_absent(outer.extractfile(member))
        for layer in {layer for entry in manifest for layer in entry["Layers"]}:
            with tarfile.open(fileobj=outer.extractfile(layer), mode="r:*") as contents:
                for member in contents:
                    assert MARKER not in member.name + member.linkname
                    if member.isfile(): assert_secret_absent(contents.extractfile(member))
    archive.unlink()


def test_ac_05_1_ac_05_4_ctr04_secret_chunk_boundary():
    assert_secret_absent(io.BytesIO(b"ordinary image data"))
    with pytest.raises(AssertionError):
        assert_secret_absent(io.BytesIO(b"x" * (1024 * 1024 - 3) + MARKER.encode()))


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
