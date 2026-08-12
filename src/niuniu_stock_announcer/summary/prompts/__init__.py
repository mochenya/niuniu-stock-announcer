"""China 公告摘要 prompt 构造。"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from niuniu_stock_announcer.summary.schema import SummaryAgentInput

PROMPT_PATH = Path(__file__).resolve().parent / "announcement_summary.md"
EMPTY_OUTPUT_MARKER = "<empty>"


def load_system_prompt() -> str:
    """读取版本化 China 摘要系统 prompt。"""
    return PROMPT_PATH.read_text(encoding="utf-8").strip()


def build_markdown_summary_messages(
    system_prompt: str, request: SummaryAgentInput
) -> list[dict[str, str]]:
    """根据 typed Agent 输入构造 Chat Completions 消息。"""
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": build_summary_user_content(request)},
    ]


def build_summary_user_content(request: SummaryAgentInput) -> str:
    """构造保留旧版事实边界的用户 prompt。"""
    return (
        f"请帮我总结公司 {request.company_name} 名为 "
        f"{request.announcement_title} 的公告，并以纯json输出。结果必须是可被 "
        "json.loads 直接解析的单个 JSON 对象，不要输出 ```json 或任何代码块包裹，也不要解释或任何对象外文本。"
        f"公告原文是：\n{request.markdown}"
    )


def build_repair_messages(
    previous_messages: Sequence[dict[str, str]],
    *,
    bad_output: str,
    error_summary: str,
) -> list[dict[str, str]]:
    """构造只修复 JSON 格式、不重新改写事实的第二轮消息。"""
    repair_messages = clone_messages(previous_messages)
    repair_messages.append(
        {
            "role": "assistant",
            "content": bad_output if bad_output.strip() else EMPTY_OUTPUT_MARKER,
        }
    )
    repair_messages.append(
        {"role": "user", "content": build_repair_user_content(error_summary)}
    )
    return repair_messages


def build_repair_user_content(error_summary: str) -> str:
    """生成一次性格式修复指令。"""
    return (
        "上一条 assistant 消息是上一次错误输出，请把它修复为最终 JSON。"
        "优先保留上一条输出中已经存在且可由原始公告支撑的事实，不要重新改写摘要。"
        "如果上一条输出为空、无法提取必要字段，或缺少 summary/tags，只能基于本轮对话中的原始公告上下文补齐缺失字段，不得引入外部信息。"
        "不要输出任何解释或代码块。"
        "最终结果必须是可被 json.loads 直接解析的单个 JSON 对象，且只能包含 summary 和 tags 两个字段。"
        "summary 必须是 JSON 字符串，tags 必须是包含 3 到 6 个字符串的 JSON 数组。"
        f"\n\n解析错误：\n{error_summary}"
    )


def clone_messages(messages: Sequence[dict[str, str]]) -> list[dict[str, str]]:
    """复制消息，防止 repair 流程修改首轮请求。"""
    return [dict(message) for message in messages]
