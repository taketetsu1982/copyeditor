#!/usr/bin/env bash
set -euo pipefail
scope="$1"
shift
case "$scope" in images|fixtures) ;; *) exit 2 ;; esac
if [ -z "${COPYEDITOR_BUILD_CACHE_DIR:-}" ]; then
  exec docker build "$@"
fi
cache="$COPYEDITOR_BUILD_CACHE_DIR/$scope"
mkdir -p "$COPYEDITOR_BUILD_CACHE_DIR"
next="$(mktemp -d "$COPYEDITOR_BUILD_CACHE_DIR/.$scope.XXXXXX")"
trap 'rm -rf "$next"' EXIT
args=(--load --progress plain --cache-to "type=local,dest=$next,mode=max")
if [ -n "${COPYEDITOR_BUILDX_BUILDER:-}" ]; then
  args+=(--builder "$COPYEDITOR_BUILDX_BUILDER")
fi
if [ -f "$cache/index.json" ]; then
  args+=(--cache-from "type=local,src=$cache")
fi
docker buildx build "${args[@]}" "$@"
# Replace only after a successful build; rotating avoids accumulating stale blobs.
rm -rf "$cache"
mv "$next" "$cache"
