#!/usr/bin/env bash
set -euo pipefail

SCOPE=${1:-all}

TEST_ENCRYPTION_KEY=${VIVIAN_API_ENCRYPTION_KEY:-fEoEtwTZrNYkNLpLM2XXnV1l3e4dnKYGZHso5N86c10=}

require_uv() {
  if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required but was not found."
    echo "Install it from: https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
  fi
}

run_api() {
  uv sync --project apps/api --extra test --locked
  VIVIAN_API_ENCRYPTION_KEY=$TEST_ENCRYPTION_KEY \
    uv run --project apps/api --extra test pytest apps/api/tests
}

run_mcp() {
  uv sync --project apps/test-mcp-server --extra test --locked
  uv run --project apps/test-mcp-server --extra test pytest apps/test-mcp-server/tests
}

require_uv

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
