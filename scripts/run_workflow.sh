#!/usr/bin/env zsh

set -Eeuo pipefail

readonly script_dir="${0:A:h}"
readonly project_root="${script_dir:h}"
readonly home_dir="${HOME:-${project_root:h:h}}"
export PATH="${home_dir}/.local/bin:${home_dir}/.cargo/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:${PATH:-}"

uv_bin="${UV_BIN:-${home_dir}/.local/bin/uv}"
readonly env_file="${RUN_WORKFLOW_ENV_FILE:-${project_root}/.env}"
readonly selected_plan="${RUN_WORKFLOW_SELECTED_PLAN_FILE:-${project_root}/config/plan.selected.yaml}"
readonly keyword_plan="${RUN_WORKFLOW_KEYWORD_PLAN_FILE:-${project_root}/config/plan.keywords.yaml}"
readonly lock_file="${RUN_WORKFLOW_LOCK_FILE:-${project_root}/logs/run_workflow.lock}"
readonly process_pending="${RUN_WORKFLOW_PROCESS_PENDING:-1}"
lock_fd=""

cd "$project_root" || exit 1

die() {
  echo "error: $*" >&2
  exit 1
}

if [[ ! -x "$uv_bin" ]]; then
  [[ -z "${UV_BIN:-}" ]] || die "uv binary not executable: $uv_bin"
  uv_bin="$(command -v uv 2>/dev/null || true)"
fi
[[ -x "$uv_bin" ]] || die "uv binary not found; set UV_BIN to an absolute path"
[[ -f "$project_root/pyproject.toml" ]] || die "pyproject.toml not found: $project_root"
[[ -f "$project_root/uv.lock" ]] || die "uv.lock not found; run uv sync first"
[[ -f "$env_file" ]] || die ".env not found: $env_file"
[[ -f "$selected_plan" ]] || die "selected Plan not found: $selected_plan"
[[ -f "$keyword_plan" ]] || die "keyword Plan not found: $keyword_plan"

mkdir -p "${lock_file:h}"
if [[ "${RUN_WORKFLOW_DISABLE_LOCK:-0}" != "1" ]] && command -v flock >/dev/null 2>&1; then
  exec {lock_fd}>"$lock_file"
  flock -n "$lock_fd" || {
    echo "skip: another run_workflow.sh process is still running" >&2
    exit 0
  }
fi

for plan_file in "$selected_plan" "$keyword_plan"; do
  PYTHONUNBUFFERED=1 "$uv_bin" run --locked --project "$project_root" \
    niuniu-stock run --env-file "$env_file" --plan "$plan_file" "$@"
done

if [[ "$process_pending" == "1" ]]; then
  PYTHONUNBUFFERED=1 "$uv_bin" run --locked --project "$project_root" \
    niuniu-stock process-pending --env-file "$env_file"
fi
