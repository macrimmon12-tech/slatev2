#!/usr/bin/env bash
# CI-runnable lint/test entrypoint for SLATE v2. See root CLAUDE.md.
#
# Named tools/check.sh rather than scripts/check.sh specifically to avoid
# colliding with scripts/ (the base Lua library directory owned by
# 10-lua-scripting-layer.md, per CONTRACTS.md §1) — see
# 00-foundation-core.md §4.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo "==> pytest"
python3 -m pytest "$@"
