"""scheduler 的 Plan 顺序、互斥锁和全局 recovery 契约测试。"""

from __future__ import annotations

from pathlib import Path


def test_scheduler_runs_two_explicit_plans_then_one_global_pending() -> None:
    source = Path("scripts/run_workflow.sh").read_text(encoding="utf-8")

    assert "readonly selected_plan=" in source
    assert "readonly keyword_plan=" in source
    assert "readonly lock_file=" in source
    assert 'for plan_file in "$selected_plan" "$keyword_plan"' in source
    assert 'niuniu-stock run --env-file "$env_file" --plan "$plan_file"' in source
    assert 'niuniu-stock process-pending --env-file "$env_file"' in source
    assert "retry-failed all" not in source
    assert "watchlist" not in source
