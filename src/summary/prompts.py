from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from domain.summary_models import MarkdownSummaryRequest

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "announcement_summary.md"
EMPTY_OUTPUT_MARKER = "<empty>"


def load_system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8").strip()


def build_summary_messages(
    system_prompt: str,
    user_content: str,
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


def build_markdown_summary_messages(
    system_prompt: str,
    request: MarkdownSummaryRequest,
) -> list[dict[str, str]]:
    return build_summary_messages(
        system_prompt,
        build_summary_user_content(request),
    )


def build_summary_user_content(request: MarkdownSummaryRequest) -> str:
    return (
        f"请帮我总结公司 {request.company_name} 名为 "
        f"{request.announcement_title} 的公告，并以纯json输出。结果必须是可被 json.loads 直接解析的单个 JSON 对象，不要输出 ```json 或任何代码块包裹，也不要解释或任何对象外文本。公告原文是：\n"
        f"{request.markdown}"
    )


def build_repair_messages(
    previous_messages: Sequence[dict[str, str]],
    *,
    bad_output: str,
    error_summary: str,
) -> list[dict[str, str]]:
    repair_messages = clone_messages(previous_messages)
    previous_output = bad_output if bad_output.strip() else EMPTY_OUTPUT_MARKER
    repair_messages.append({"role": "assistant", "content": previous_output})
    repair_messages.append(
        {"role": "user", "content": build_repair_user_content(error_summary)}
    )
    return repair_messages


def build_repair_user_content(error_summary: str) -> str:
    return (
        "上一条 assistant 消息是上一次错误输出，请把它修复为最终 JSON。"
        "优先保留上一条输出中已经存在且可由原始公告支撑的事实，不要重新改写摘要。"
        "如果上一条输出为空、无法提取必要字段，或缺少 summary/tags，"
        "只能基于本轮对话中的原始公告上下文补齐缺失字段，不得引入外部信息。"
        "不要输出任何解释或代码块。"
        "最终结果必须是可被 json.loads 直接解析的单个 JSON 对象，且只能包含 summary 和 tags 两个字段。"
        "summary 必须是 JSON 字符串，tags 必须是包含 3 到 6 个字符串的 JSON 数组。"
        "\n\n解析错误：\n"
        f"{error_summary}"
    )


def clone_messages(messages: Sequence[dict[str, str]]) -> list[dict[str, str]]:
    return [dict(message) for message in messages]
