#!/bin/zsh
# SPDX-License-Identifier: Apache-2.0
# Generate or verify hashes for the distributable protocol payload.
set -eu

MODE="${1:---write}"
ROOT="${0:A:h:h}"
cd "$ROOT"
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT

# Community metadata and CI workflows are repository controls, not installed
# protocol payload. They are validated separately by CI and secret scans.
git ls-files --cached --others --exclude-standard -- \
  README.md LICENSE NOTICE install.sh scripts config skills tests docs tools \
  | LC_ALL=C sort \
  | while IFS= read -r file; do
      [[ "$file" == "manifest.sha256" ]] && continue
      shasum -a 256 "$file"
    done > "$TMP"

case "$MODE" in
  --write)
    mv "$TMP" manifest.sha256
    trap - EXIT
    ;;
  --check)
    cmp -s "$TMP" manifest.sha256 || {
      echo "manifest.sha256 is stale; run ./tools/generate-manifest.sh --write" >&2
      diff -u manifest.sha256 "$TMP" || true
      exit 1
    }
    shasum -a 256 -c manifest.sha256
    ;;
  -h|--help)
    echo "Usage: ./tools/generate-manifest.sh [--write|--check]"
    ;;
  *)
    echo "Unknown mode: $MODE" >&2
    exit 2
    ;;
esac
