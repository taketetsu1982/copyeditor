#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
docker info >/dev/null
work="$(mktemp -d)"
name="copyeditor-images-$(basename "$work" | tr '[:upper:].' '[:lower:]-')"
cleanup() {
  status=$?
  set +e
  containers="$(docker ps -aq --filter "label=copyeditor.test=$name")" || status=1
  if [ -n "$containers" ]; then docker rm -f $containers >/dev/null || status=1; fi
  images="$(docker image ls -q --filter "label=copyeditor.test=$name" | sort -u)" || status=1
  if [ -n "$images" ]; then docker image rm -f $images >/dev/null || status=1; fi
  rm -rf "$work" || status=1
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
docker build --label "copyeditor.test=$name" --tag "$name:base" "$root"
COPYEDITOR_TEST_IMAGE="$name:base" COPYEDITOR_TEST_LABEL="$name" \
  "${COPYEDITOR_TEST_PYTHON:-python}" -m pytest "$root/tests/integration/test_images.py" -q
