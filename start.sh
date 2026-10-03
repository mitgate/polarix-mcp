#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$BASE_DIR/.env"

# Kill any existing Polarix process before starting a fresh one
EXISTING=$(pgrep -f "browser_python_mcp.py" 2>/dev/null || true)
if [[ -n "$EXISTING" ]]; then
    echo "[polarix] Encerrando sessão anterior (PID: $EXISTING)..."
    kill $EXISTING 2>/dev/null || true
    sleep 1
fi

# Load env file if present
if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

export MCP_TRANSPORT="${MCP_TRANSPORT:-streamable-http}"
export MCP_HOST="${MCP_HOST:-127.0.0.1}"
export MCP_PORT="${MCP_PORT:-8016}"
export BROWSER_HEADLESS="${BROWSER_HEADLESS:-true}"
export BROWSER_USE_MODEL="${BROWSER_USE_MODEL:-gpt-4o-mini}"
export POLARIX_SESSIONS_DIR="${POLARIX_SESSIONS_DIR:-${POLARIS_SESSIONS_DIR:-/tmp/polarix_sessions}}"

# Desktop / VM targets (see README "Desktop automation")
export POLARIX_DESKTOP_DRIVER="${POLARIX_DESKTOP_DRIVER:-${POLARIS_DESKTOP_DRIVER:-auto}}"
export POLARIX_DESKTOP_AGENT_URL="${POLARIX_DESKTOP_AGENT_URL:-}"
export POLARIX_AGENT_TOKEN="${POLARIX_AGENT_TOKEN:-}"
export POLARIX_VM_BACKEND="${POLARIX_VM_BACKEND:-auto}"
export POLARIX_MACROS_DIR="${POLARIX_MACROS_DIR:-/tmp/polarix_macros}"

# Never inherit a venv from the calling environment
unset VIRTUAL_ENV
unset PYTHONPATH

# Prefer the virtualenv that install.sh created; fall back to the system interpreter
if [[ -x "$BASE_DIR/.venv/bin/python" ]] && "$BASE_DIR/.venv/bin/python" -c "import mcp, playwright" 2>/dev/null; then
    PYTHON="$BASE_DIR/.venv/bin/python"
else
    PYTHON="${POLARIX_PYTHON:-/usr/bin/python3.11}"
fi
exec "$PYTHON" "$BASE_DIR/browser_python_mcp.py"
