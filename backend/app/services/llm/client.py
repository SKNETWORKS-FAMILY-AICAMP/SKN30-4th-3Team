"""OpenAI-primary, local-model-fallback chat completion routing."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Literal

from app.core.config import settings

logger = logging.getLogger(__name__)

ProviderName = Literal["openai", "local"]
PreferredProvider = Literal["openai", "local"]

SELECTABLE_OPENAI_CHAT_MODELS = (
    "gpt-5.4",
    "gpt-5.5",
    "gpt-5.6-sol",
)
DEFAULT_SELECTABLE_OPENAI_CHAT_MODEL = SELECTABLE_OPENAI_CHAT_MODELS[0]


@dataclass(frozen=True)
class ChatCompletionResult:
    response: Any
    provider: ProviderName
    model: str


class LLMUnavailableError(RuntimeError):
    """Raised when neither the primary nor local LLM can serve a request."""


class _PrimaryCircuitBreaker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failure_count = 0
        self._opened_until = 0.0

    def allow_request(self) -> bool:
        now = time.monotonic()
        with self._lock:
            if self._opened_until and now >= self._opened_until:
                self._opened_until = 0.0
                self._failure_count = 0
            return self._opened_until == 0.0

    def record_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            self._opened_until = 0.0

    def record_failure(self, threshold: int, open_seconds: float) -> None:
        with self._lock:
            self._failure_count += 1
            if self._failure_count >= max(1, threshold):
                self._opened_until = time.monotonic() + max(1.0, open_seconds)

    def reset(self) -> None:
        self.record_success()

    def snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            remaining = max(0.0, self._opened_until - now)
            return {
                "state": "open" if remaining > 0 else "closed",
                "failureCount": self._failure_count,
                "retryAfterSeconds": round(remaining, 1),
            }


_primary_circuit = _PrimaryCircuitBreaker()


def _primary_api_key() -> str:
    return os.getenv("OPENAI_API_KEY") or settings.OPENAI_API_KEY


def _fallback_enabled() -> bool:
    return bool(settings.LLM_FALLBACK_ENABLED and settings.LOCAL_LLM_BASE_URL)


def primary_requests_allowed() -> bool:
    return _primary_circuit.allow_request()


def record_primary_success() -> None:
    _primary_circuit.record_success()


def record_primary_failure(exc: Exception) -> None:
    if not is_fallback_eligible(exc):
        return
    _primary_circuit.record_failure(
        settings.LLM_FAILURE_THRESHOLD,
        settings.LLM_CIRCUIT_OPEN_SECONDS,
    )


def reset_primary_circuit() -> None:
    """Reset process-local circuit state. Primarily useful for tests and recovery tooling."""
    _primary_circuit.reset()


def get_llm_runtime_status() -> dict[str, Any]:
    return {
        "primaryConfigured": bool(_primary_api_key()),
        "fallbackEnabled": _fallback_enabled(),
        "localBaseUrl": settings.LOCAL_LLM_BASE_URL if _fallback_enabled() else None,
        "localChatModel": settings.LOCAL_CHAT_MODEL if _fallback_enabled() else None,
        "localVisionModel": settings.LOCAL_VISION_MODEL if _fallback_enabled() else None,
        "localAuxiliaryEnabled": bool(_fallback_enabled() and settings.LOCAL_LLM_AUXILIARY_ENABLED),
        "primaryCircuit": _primary_circuit.snapshot(),
    }


def is_fallback_eligible(exc: Exception) -> bool:
    """Return whether a primary error represents availability/auth/quota failure."""
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        if status_code in {401, 403, 408, 409, 429} or status_code >= 500:
            return True
        if 400 <= status_code < 500:
            return False

    class_name = type(exc).__name__.lower()
    if "badrequest" in class_name or "unprocessable" in class_name:
        return False
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True

    # SDK/network wrappers and test doubles often surface transport failures as
    # RuntimeError. Unknown failures are eligible so the deterministic fallback
    # remains reachable instead of turning a provider outage into an API 500.
    return True


def _create_completion(
    *,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float,
    timeout: float,
    response_format: dict[str, Any] | None,
    max_tokens: int | None,
    base_url: str | None = None,
) -> Any:
    from openai import OpenAI

    client_kwargs: dict[str, Any] = {
        "api_key": api_key,
        "timeout": timeout,
        "max_retries": 0,
    }
    if base_url:
        client_kwargs["base_url"] = base_url.rstrip("/") + "/"

    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
    }
    is_selectable_openai_reasoning_model = base_url is None and model in SELECTABLE_OPENAI_CHAT_MODELS
    if is_selectable_openai_reasoning_model:
        # 기존 비추론 상담 모델의 지연/비용 특성을 보존합니다. GPT-5.5/5.6의
        # 생략 기본값(medium)을 그대로 사용하면 단순 상담도 예상보다 무거워집니다.
        request_kwargs["reasoning_effort"] = "none"
    else:
        request_kwargs["temperature"] = temperature
    if response_format is not None:
        request_kwargs["response_format"] = response_format
    if max_tokens is not None:
        token_field = "max_completion_tokens" if is_selectable_openai_reasoning_model else "max_tokens"
        request_kwargs[token_field] = max_tokens

    client = OpenAI(**client_kwargs)
    return client.chat.completions.create(**request_kwargs)


def chat_completion(
    *,
    messages: list[dict[str, Any]],
    primary_model: str,
    local_model: str,
    temperature: float = 0.1,
    response_format: dict[str, Any] | None = None,
    primary_timeout: float = 18.0,
    max_tokens: int | None = None,
    allow_local_fallback: bool = True,
    preferred_provider: PreferredProvider = "openai",
) -> ChatCompletionResult:
    """Call OpenAI first and use the configured local OpenAI-compatible API on failure."""
    primary_error: Exception | None = None
    primary_key = _primary_api_key()

    if preferred_provider == "openai" and primary_key and primary_requests_allowed():
        try:
            response = _create_completion(
                api_key=primary_key,
                model=primary_model,
                messages=messages,
                temperature=temperature,
                timeout=primary_timeout,
                response_format=response_format,
                max_tokens=max_tokens,
            )
            record_primary_success()
            return ChatCompletionResult(response=response, provider="openai", model=primary_model)
        except Exception as exc:  # noqa: BLE001
            if not is_fallback_eligible(exc):
                raise
            primary_error = exc
            record_primary_failure(exc)
            logger.warning(
                "Primary LLM failed; considering local fallback (model=%s, error=%s)",
                primary_model,
                type(exc).__name__,
            )

    if allow_local_fallback and _fallback_enabled():
        try:
            response = _create_completion(
                api_key=settings.LOCAL_LLM_API_KEY or "ollama",
                model=local_model,
                messages=messages,
                temperature=temperature,
                timeout=settings.LOCAL_LLM_TIMEOUT_SECONDS,
                response_format=response_format,
                max_tokens=max_tokens,
                base_url=settings.LOCAL_LLM_BASE_URL,
            )
            logger.info("Local LLM fallback served request (model=%s)", local_model)
            return ChatCompletionResult(response=response, provider="local", model=local_model)
        except Exception as local_error:  # noqa: BLE001
            logger.warning(
                "Local LLM fallback failed (model=%s, error=%s)",
                local_model,
                type(local_error).__name__,
            )
            raise LLMUnavailableError("Primary and local LLM providers are unavailable.") from local_error

    if preferred_provider == "local":
        raise LLMUnavailableError("The selected local LLM provider is unavailable.")
    if primary_error is not None:
        raise LLMUnavailableError("Primary LLM failed and local fallback is disabled.") from primary_error
    if primary_key and not primary_requests_allowed():
        raise LLMUnavailableError("Primary LLM circuit is open and local fallback is disabled.")
    raise LLMUnavailableError("No LLM provider is configured.")
