#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
docker info >/dev/null
work="$(mktemp -d)"
name="copyeditor-fixtures-$(basename "$work" | tr '[:upper:].' '[:lower:]-')"
cleanup() {
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker image rm "$name" >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
mkdir -p "$work/scripts" "$work/tests/integration"
cp "$root/package.json" "$root/package-lock.json" "$root/requirements.generated.txt" "$work/"
cp "$root/tests/fixtures/Dockerfile.benchmark" "$work/Dockerfile"
cp -R "$root/src" "$root/rules" "$root/examples" "$work/"
cp "$root/scripts/benchmark_provider.py" "$root/scripts/benchmark_assert.py" "$root/scripts/examples_to_promptfoo.py" "$root/scripts/test_fixtures.sh" "$work/scripts/"
cp "$root/tests/integration/test_benchmark.py" "$work/tests/integration/"
docker build --tag "$name" "$work"
docker run --name "$name" --network none --cap-drop ALL --security-opt no-new-privileges "$name"
