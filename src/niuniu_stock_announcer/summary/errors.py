"""摘要失败分类与受控诊断文本。"""

from __future__ import annotations


class SummaryError(RuntimeError):
    """表示摘要流程中可稳定分类的失败。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def serialize_summary_error(exc: Exception) -> str:
    """把摘要异常转换为不包含完整模型响应的稳定诊断文本。

    Args:
        exc: 摘要流程捕获的异常。

    Returns:
        可保存到 failure 字段的受控单行文本。
    """
    if isinstance(exc, SummaryError):
        return f"SUMMARY_{exc.code}: {_short(exc.message)}"
    return f"SUMMARY_INTERNAL: {exc.__class__.__name__}"


def _short(value: str, *, limit: int = 1000) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."
