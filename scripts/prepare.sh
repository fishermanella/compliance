#!/bin/bash
set -Eeuo pipefail

# Resolve the source project from this script, then let the Web runner choose its local mirror.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd -P)"
if [[ "${COZE_WEB_LOCAL_ACTIVE:-}" != "${PROJECT_ROOT}" ]]; then
  exec node "${SCRIPT_DIR}/local-workspace.cjs" prepare "$@"
fi
COZE_WORKSPACE_PATH="${PROJECT_ROOT}"

cd "${COZE_WORKSPACE_PATH}"

echo "Installing dependencies..."
if [[ "${COZE_WEB_LOCAL_MIRROR:-}" != "1" ]]; then
  pnpm install --prefer-frozen-lockfile --prefer-offline --loglevel debug --reporter=append-only
fi
