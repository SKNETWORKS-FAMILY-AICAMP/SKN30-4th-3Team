"""LLM provider routing and structured response helpers."""

from app.services.llm.client import (
    ChatCompletionResult,
    DEFAULT_SELECTABLE_OPENAI_CHAT_MODEL,
    LLMUnavailableError,
    SELECTABLE_OPENAI_CHAT_MODELS,
    chat_completion,
    get_llm_runtime_status,
    is_fallback_eligible,
    primary_requests_allowed,
    record_primary_failure,
    record_primary_success,
    reset_primary_circuit,
)

__all__ = [
    "ChatCompletionResult",
    "DEFAULT_SELECTABLE_OPENAI_CHAT_MODEL",
    "LLMUnavailableError",
    "SELECTABLE_OPENAI_CHAT_MODELS",
    "chat_completion",
    "get_llm_runtime_status",
    "is_fallback_eligible",
    "primary_requests_allowed",
    "record_primary_failure",
    "record_primary_success",
    "reset_primary_circuit",
]
