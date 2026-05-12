#!/bin/bash
# SessionStart-hook: installerar Python-deps + Railway CLI så att tester
# kan köras och Railway MCP-servern (som shell:ar ut till `railway`-CLI:n)
# fungerar i Claude Code on the web-sessions.
#
# Idempotent — säker att köra flera gånger. Endast aktiv på remote.

set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(pwd)}"

echo "[session-start] installerar Python-deps från requirements.txt..."
pip install --quiet -r requirements.txt

echo "[session-start] installerar pytest (krävs för backend-tester)..."
pip install --quiet pytest

if ! command -v railway >/dev/null 2>&1; then
  echo "[session-start] installerar Railway CLI..."
  npm install -g @railway/cli >/dev/null
else
  echo "[session-start] Railway CLI redan installerad ($(railway --version))"
fi

echo "[session-start] klart."
