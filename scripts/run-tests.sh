#!/usr/bin/env bash
set -euo pipefail

SCOPE=${1:-all}

TEST_ENCRYPTION_KEY=${VIVIAN_API_ENCRYPTION_KEY:-fEoEtwTZrNYkNLpLM2XXnV1l3e4dnKYGZHso5N86c10=}

run_api() {
  if [ -d "apps/api/.venv" ]; then
    # shellcheck disable=SC1091
    source apps/api/.venv/bin/activate
  elif [ -d ".venv" ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
  elif [ -d "venv" ]; then
    # shellcheck disable=SC1091
    source venv/bin/activate
  else
    echo "No API virtual environment found. Create one with:"
    echo "  cd apps/api && python3 -m venv .venv && source .venv/bin/activate && pip install -e \".[test]\""
    exit 1
  fi

  if command -v pytest >/dev/null 2>&1; then
    VIVIAN_API_ENCRYPTION_KEY=$TEST_ENCRYPTION_KEY pytest apps/api/tests
  else
    echo "pytest not found in the API venv. Install dependencies with:"
    echo "  cd apps/api && source .venv/bin/activate && pip install -e \".[test]\""
    exit 1
  fi
}

run_mcp() {
  if [ -d "apps/test-mcp-server/venv" ]; then
    # shellcheck disable=SC1091
    source apps/test-mcp-server/venv/bin/activate
  else
    echo "No MCP virtual environment found. Create one with:"
    echo "  cd apps/test-mcp-server && python3 -m venv venv && source venv/bin/activate && pip install -e \".[test]\""
    exit 1
  fi

  (cd apps/test-mcp-server && pytest)
}

case "$SCOPE" in
  api)
    run_api
    ;;
  mcp)
    run_mcp
    ;;
  all)
    run_api
    run_mcp
    ;;
  *)
    echo "Usage: $0 [api|mcp|all]"
    exit 1
    ;;
esac
