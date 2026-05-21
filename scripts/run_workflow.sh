#!/usr/bin/env zsh

set -Eeuo pipefail

script_dir="${0:A:h}"
project_root="${script_dir:h}"
home_dir="${HOME:-${project_root:h:h}}"

# cron 默认环境很薄，尤其不会继承交互式 zsh 里的 PATH。
export PATH="${home_dir}/.local/bin:${home_dir}/.cargo/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:${PATH:-}"

uv_bin="${UV_BIN:-${home_dir}/.local/bin/uv}"
env_file="${RUN_WORKFLOW_ENV_FILE:-${project_root}/.env}"
watchlist_file="${RUN_WORKFLOW_WATCHLIST_FILE:-${project_root}/config/watchlist.yaml}"
cli_log_dir="${RUN_WORKFLOW_CLI_LOG_DIR:-${project_root}/logs/runs}"
lock_file="${RUN_WORKFLOW_LOCK_FILE:-${project_root}/logs/run_workflow.lock}"
lock_fd=""

cd "$project_root" || exit 1

die() {
  echo "error: $*" >&2
  exit 1
}

resolve_uv_bin() {
  local resolved_uv=""

  if [[ -x "$uv_bin" ]]; then
    return 0
  fi

  if [[ -z "${UV_BIN:-}" ]]; then
    resolved_uv="$(command -v uv 2>/dev/null || true)"
    if [[ -n "$resolved_uv" && -x "$resolved_uv" ]]; then
      uv_bin="$resolved_uv"
      return 0
    fi
  fi

  return 1
}

acquire_lock() {
  if [[ "${RUN_WORKFLOW_DISABLE_LOCK:-0}" == "1" ]]; then
    return 0
  fi

  if ! command -v flock >/dev/null 2>&1; then
    return 0
  fi

  mkdir -p "$(dirname "$lock_file")"
  exec {lock_fd}>"$lock_file"
  if ! flock -n "$lock_fd"; then
    echo "skip: another run_workflow.sh process is still running" >&2
    exit 0
  fi
}

resolve_uv_bin || die "uv binary not found or not executable: $uv_bin. Set UV_BIN=/absolute/path/to/uv in crontab."
[[ -f "$project_root/pyproject.toml" ]] || die "pyproject.toml not found: $project_root"
[[ -f "$project_root/uv.lock" ]] || die "uv.lock not found. Run 'uv sync' before starting the workflow."
[[ -f "$env_file" ]] || die ".env not found: $env_file"
[[ -f "$watchlist_file" ]] || die "watchlist YAML not found: $watchlist_file"

acquire_lock

PYTHONUNBUFFERED=1 "$uv_bin" run --locked --project "$project_root" \
  niuniu-stock run \
  --env-file "$env_file" \
  --config-file "$watchlist_file" \
  --log-dir "$cli_log_dir" \
  "$@"
