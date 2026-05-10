from __future__ import annotations


class SummaryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def serialize_summary_error(exc: Exception) -> str:
    if isinstance(exc, SummaryError):
        return f"SUMMARY_{exc.code}: {exc.message}"
    message = str(exc).strip() or "unexpected summary failure"
    return f"SUMMARY_INTERNAL: {message}"
