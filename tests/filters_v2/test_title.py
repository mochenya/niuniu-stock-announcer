"""标题过滤 typed evidence 的确定性测试。"""

from niuniu_stock_announcer.filters.title import evaluate_title_filter


def test_filter_normalizes_keywords_and_records_all_matches_in_config_order() -> None:
    decision = evaluate_title_filter(
        "关于更正月报表的公告", [" 月报表 ", "更正", "月报表", ""]
    )

    assert decision.outcome == "filtered"
    assert decision.reason_code == "excluded_keyword"
    assert decision.evidence.configured_keywords == ("月报表", "更正")
    assert decision.evidence.matched_keywords == ("月报表", "更正")


def test_filter_records_pass_evidence_even_without_configured_keywords() -> None:
    decision = evaluate_title_filter("关于股份回购的公告", [])

    assert decision.outcome == "selected"
    assert decision.reason_code == "passed"
    assert decision.evidence.evaluated_title == "关于股份回购的公告"
    assert decision.evidence.configured_keywords == ()
    assert decision.evidence.matched_keywords == ()
