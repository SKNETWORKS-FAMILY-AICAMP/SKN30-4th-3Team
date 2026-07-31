"""OpenAI-primary, local-model-fallback chat completion routing."""

from __future__ import annotations

import base64
import copy
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse

from app.core.config import settings

logger = logging.getLogger(__name__)

LOCAL_STATUS_TIMEOUT_SECONDS = 3.0
LOCAL_IMAGE_DOWNLOAD_TIMEOUT_SECONDS = 15.0
LOCAL_IMAGE_MAX_BYTES = 8 * 1024 * 1024
LOCAL_IMAGE_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

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


def _local_model_available() -> bool:
    if not _fallback_enabled():
        return False

    try:
        import httpx

        response = httpx.get(
            settings.LOCAL_LLM_BASE_URL.rstrip("/") + "/models",
            headers={"Authorization": f"Bearer {settings.LOCAL_LLM_API_KEY or 'ollama'}"},
            timeout=LOCAL_STATUS_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        model_ids = {
            str(item.get("id") or item.get("name") or "").strip()
            for item in payload.get("data", [])
            if isinstance(item, dict)
        }
        return settings.LOCAL_CHAT_MODEL in model_ids
    except Exception as exc:  # noqa: BLE001
        logger.info("Local LLM health check failed (%s)", type(exc).__name__)
        return False


def get_llm_runtime_status(*, probe_local: bool = False) -> dict[str, Any]:
    circuit = _primary_circuit.snapshot()
    primary_configured = bool(_primary_api_key())
    return {
        "primaryConfigured": primary_configured,
        "primaryAvailable": primary_configured and circuit["state"] == "closed",
        "fallbackEnabled": _fallback_enabled(),
        "localAvailable": _local_model_available() if probe_local else _fallback_enabled(),
        "localBaseUrl": settings.LOCAL_LLM_BASE_URL if _fallback_enabled() else None,
        "localChatModel": settings.LOCAL_CHAT_MODEL if _fallback_enabled() else None,
        "localVisionModel": settings.LOCAL_VISION_MODEL if _fallback_enabled() else None,
        "localAuxiliaryEnabled": bool(_fallback_enabled() and settings.LOCAL_LLM_AUXILIARY_ENABLED),
        "primaryCircuit": circuit,
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


def _download_local_image_as_data_url(image_url: str) -> str:
    """Download a trusted Supabase image and encode it for Ollama vision input."""
    if image_url.startswith("data:image/"):
        return image_url

    image_parts = urlparse(image_url)
    supabase_parts = urlparse(settings.SUPABASE_URL)
    if (
        image_parts.scheme not in {"http", "https"}
        or not image_parts.netloc
        or image_parts.netloc.lower() != supabase_parts.netloc.lower()
    ):
        raise ValueError("Local vision images must use the configured Supabase host.")

    import httpx

    image_bytes = bytearray()
    with httpx.stream(
        "GET",
        image_url,
        timeout=LOCAL_IMAGE_DOWNLOAD_TIMEOUT_SECONDS,
        follow_redirects=False,
    ) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type not in LOCAL_IMAGE_CONTENT_TYPES:
            raise ValueError(f"Unsupported local vision image type: {content_type or 'unknown'}")

        content_length = response.headers.get("content-length")
        if content_length and int(content_length) > LOCAL_IMAGE_MAX_BYTES:
            raise ValueError("Local vision image exceeds the 8MB size limit.")

        for chunk in response.iter_bytes():
            image_bytes.extend(chunk)
            if len(image_bytes) > LOCAL_IMAGE_MAX_BYTES:
                raise ValueError("Local vision image exceeds the 8MB size limit.")

    if not image_bytes:
        raise ValueError("Local vision image is empty.")

    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def _prepare_local_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert remote OpenAI image parts to data URLs accepted by local Ollama."""
    local_messages = copy.deepcopy(messages)
    converted_urls: dict[str, str] = {}

    for message in local_messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "image_url":
                continue
            image = part.get("image_url")
            if isinstance(image, str):
                image_url = image
            elif isinstance(image, dict):
                image_url = image.get("url")
            else:
                continue
            if not isinstance(image_url, str) or not image_url:
                continue

            data_url = converted_urls.get(image_url)
            if data_url is None:
                data_url = _download_local_image_as_data_url(image_url)
                converted_urls[image_url] = data_url
            if isinstance(image, str):
                part["image_url"] = data_url
            else:
                image["url"] = data_url

    if converted_urls:
        logger.info("Prepared %d image(s) as base64 for local vision", len(converted_urls))
    return local_messages


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
            local_messages = _prepare_local_messages(messages)
            response = _create_completion(
                api_key=settings.LOCAL_LLM_API_KEY or "ollama",
                model=local_model,
                messages=local_messages,
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
