#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_ROOT}"

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv is not installed or not available in PATH." >&2
  echo "install uv first, then retry: https://docs.astral.sh/uv/" >&2
  exit 127
fi

if [[ ! -f "${PROJECT_ROOT}/pyproject.toml" ]]; then
  echo "error: pyproject.toml not found. Run this script from the project checkout." >&2
  exit 1
fi

if [[ ! -f "${PROJECT_ROOT}/uv.lock" ]]; then
  echo "error: uv.lock not found. Run 'uv sync' before starting the workflow." >&2
  exit 1
fi

if [[ ! -f "${PROJECT_ROOT}/.env" ]]; then
  echo "error: .env not found. Create it from .env.example and fill runtime settings." >&2
  exit 1
fi

if [[ ! -f "${PROJECT_ROOT}/config/watchlist.yaml" ]]; then
  echo "error: config/watchlist.yaml not found. Create it from config/watchlist.example.yaml." >&2
  exit 1
fi

exec uv run --locked --project "${PROJECT_ROOT}" niuniu-stock run "$@"
