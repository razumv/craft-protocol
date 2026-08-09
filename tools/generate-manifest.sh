#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Generate or verify hashes for the distributable protocol payload.
set -eu

MODE="${1:---write}"
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT HUP INT TERM

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1"
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1"
  else
    echo "sha256sum or shasum is required" >&2
    return 127
  fi
}

# Community metadata and CI workflows are repository controls, not installed
# protocol payload. They are validated separately by CI and secret scans.
git ls-files --cached --others --exclude-standard -- \
  README.md LICENSE NOTICE install.sh scripts config skills tests docs tools \
  | LC_ALL=C sort \
  | while IFS= read -r file; do
      [ "$file" = "manifest.sha256" ] && continue
      sha256 "$file"
    done > "$TMP"

case "$MODE" in
  --write)
    mv "$TMP" manifest.sha256
    trap - EXIT HUP INT TERM
    ;;
  --check)
    cmp -s "$TMP" manifest.sha256 || {
      echo "manifest.sha256 is stale; run ./tools/generate-manifest.sh --write" >&2
      diff -u manifest.sha256 "$TMP" || true
      exit 1
    }
    while IFS='  ' read -r hash file; do
      actual=$(sha256 "$file" | awk '{print $1}')
      [ "$hash" = "$actual" ] || { echo "$file: FAILED" >&2; exit 1; }
      echo "$file: OK"
    done < manifest.sha256
    ;;
  -h|--help)
    echo "Usage: ./tools/generate-manifest.sh [--write|--check]"
    ;;
  *)
    echo "Unknown mode: $MODE" >&2
    exit 2
    ;;
esac
