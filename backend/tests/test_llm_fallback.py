import json
import types

import pytest

from app.core.config import settings
from app.services.llm import client as llm_client
from app.services.llm.schemas import VisionObservation, parse_json_object
from app.services.rag import vision


def completion(content: str):
    message = types.SimpleNamespace(content=content)
    choice = types.SimpleNamespace(message=message)
    return types.SimpleNamespace(choices=[choice])


def install_fake_openai(monkeypatch, *, primary_result=None, primary_error=None, local_result=None, local_error=None):
    import openai

    calls = []

    class FakeCompletions:
        def __init__(self, provider: str):
            self.provider = provider

        def create(self, **kwargs):
            calls.append((self.provider, kwargs))
            if self.provider == "local":
                if local_error:
                    raise local_error
                return completion(local_result or "{}")
            if primary_error:
                raise primary_error
            return completion(primary_result or "{}")

    class FakeOpenAI:
        def __init__(self, *args, **kwargs):
            provider = "local" if kwargs.get("base_url") else "openai"
            self.chat = types.SimpleNamespace(completions=FakeCompletions(provider))

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    return calls


@pytest.fixture(autouse=True)
def reset_provider_state(monkeypatch):
    llm_client.reset_primary_circuit()
    monkeypatch.setattr(settings, "LLM_FALLBACK_ENABLED", True)
    monkeypatch.setattr(settings, "LOCAL_LLM_AUXILIARY_ENABLED", False)
    monkeypatch.setattr(settings, "LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setattr(settings, "LOCAL_LLM_API_KEY", "ollama")
    monkeypatch.setattr(settings, "LOCAL_CHAT_MODEL", "qwen3-vl:4b-instruct")
    monkeypatch.setattr(settings, "LOCAL_VISION_MODEL", "qwen3-vl:4b-instruct")
    monkeypatch.setattr(settings, "LOCAL_LLM_TIMEOUT_SECONDS", 5.0)
    monkeypatch.setattr(settings, "LLM_FAILURE_THRESHOLD", 2)
    monkeypatch.setattr(settings, "LLM_CIRCUIT_OPEN_SECONDS", 60.0)
    yield
    llm_client.reset_primary_circuit()


def call_chat():
    return llm_client.chat_completion(
        messages=[{"role": "user", "content": "식물 상태를 JSON으로 알려주세요."}],
        primary_model="gpt-4o-mini",
        local_model="qwen3-vl:4b-instruct",
        response_format={"type": "json_object"},
    )


def test_primary_success_does_not_call_local(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    calls = install_fake_openai(monkeypatch, primary_result='{"summary":"primary"}')

    result = call_chat()

    assert result.provider == "openai"
    assert [provider for provider, _ in calls] == ["openai"]


def test_primary_timeout_uses_local_model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    calls = install_fake_openai(
        monkeypatch,
        primary_error=TimeoutError("primary timeout"),
        local_result='{"summary":"local"}',
    )

    result = call_chat()

    assert result.provider == "local"
    assert result.model == "qwen3-vl:4b-instruct"
    assert [provider for provider, _ in calls] == ["openai", "local"]


def test_missing_primary_key_calls_local_directly(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    calls = install_fake_openai(monkeypatch, local_result='{"summary":"local"}')

    result = call_chat()

    assert result.provider == "local"
    assert [provider for provider, _ in calls] == ["local"]


def test_explicit_local_selection_bypasses_configured_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    calls = install_fake_openai(
        monkeypatch,
        primary_result='{"summary":"primary"}',
        local_result='{"summary":"local"}',
    )

    result = llm_client.chat_completion(
        messages=[{"role": "user", "content": "로컬 모델로 답해주세요."}],
        primary_model="gpt-5.6-sol",
        local_model="qwen3-vl:4b-instruct",
        preferred_provider="local",
    )

    assert result.provider == "local"
    assert result.model == "qwen3-vl:4b-instruct"
    assert [provider for provider, _ in calls] == ["local"]


def test_selected_openai_model_is_forwarded(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    calls = install_fake_openai(monkeypatch, primary_result='{"summary":"primary"}')

    result = llm_client.chat_completion(
        messages=[{"role": "user", "content": "선택 모델로 답해주세요."}],
        primary_model="gpt-5.5",
        local_model="qwen3-vl:4b-instruct",
    )

    assert result.provider == "openai"
    assert result.model == "gpt-5.5"
    assert calls[0][1]["model"] == "gpt-5.5"
    assert calls[0][1]["reasoning_effort"] == "none"
    assert "temperature" not in calls[0][1]


def test_circuit_breaker_skips_primary_after_threshold(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    calls = install_fake_openai(
        monkeypatch,
        primary_error=TimeoutError("primary timeout"),
        local_result='{"summary":"local"}',
    )

    call_chat()
    call_chat()
    call_chat()

    assert [provider for provider, _ in calls].count("openai") == 2
    assert [provider for provider, _ in calls].count("local") == 3
    assert llm_client.get_llm_runtime_status()["primaryCircuit"]["state"] == "open"


def test_bad_request_does_not_fallback(monkeypatch):
    class BadRequest(RuntimeError):
        status_code = 400

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    calls = install_fake_openai(monkeypatch, primary_error=BadRequest("invalid request"))

    with pytest.raises(BadRequest):
        call_chat()

    assert [provider for provider, _ in calls] == ["openai"]


def test_both_providers_unavailable(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    install_fake_openai(
        monkeypatch,
        primary_error=TimeoutError("primary timeout"),
        local_error=ConnectionError("local unavailable"),
    )

    with pytest.raises(llm_client.LLMUnavailableError):
        call_chat()


def test_local_fallback_can_be_disabled_for_auxiliary_calls(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    calls = install_fake_openai(
        monkeypatch,
        primary_error=TimeoutError("primary timeout"),
        local_result='{"queries":["local"]}',
    )

    with pytest.raises(llm_client.LLMUnavailableError):
        llm_client.chat_completion(
            messages=[{"role": "user", "content": "검색어를 확장해 주세요."}],
            primary_model="gpt-4o-mini",
            local_model="qwen3-vl:4b-instruct",
            allow_local_fallback=False,
        )

    assert [provider for provider, _ in calls] == ["openai"]


def test_vision_uses_local_model_without_openai_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    payload = {
        "observedSymptoms": ["잎 끝 갈변"],
        "affectedParts": ["잎"],
        "severity": "경미",
        "description": "잎 끝에 갈색 변색이 관찰됩니다.",
    }
    calls = install_fake_openai(monkeypatch, local_result=json.dumps(payload, ensure_ascii=False))
    monkeypatch.setattr(vision, "create_signed_image_url", lambda db, storage_path: "https://example.com/signed.jpg")

    result = vision.analyze_plant_image(object(), "plants/test/photo.jpg", "잎 끝이 왜 갈색인가요?")

    assert result["description"] == payload["description"]
    assert "잎 끝 갈변" in result["signals"]
    assert [provider for provider, _ in calls] == ["local"]


def test_vision_respects_explicit_local_selection(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    payload = {
        "observedSymptoms": ["잎 끝 갈변"],
        "affectedParts": ["잎"],
        "severity": "경미",
        "description": "잎 끝에 갈색 변색이 관찰됩니다.",
    }
    calls = install_fake_openai(
        monkeypatch,
        primary_result=json.dumps(payload, ensure_ascii=False),
        local_result=json.dumps(payload, ensure_ascii=False),
    )
    monkeypatch.setattr(vision, "create_signed_image_url", lambda db, storage_path: "https://example.com/signed.jpg")

    vision.analyze_plant_image(
        object(),
        "plants/test/photo.jpg",
        "잎 끝이 왜 갈색인가요?",
        primary_model="gpt-5.6-sol",
        preferred_provider="local",
    )

    assert [provider for provider, _ in calls] == ["local"]


def test_structured_response_strips_thinking_and_normalizes_vision_severity():
    parsed = parse_json_object('<think>internal notes</think>\n```json\n{"severity":"중등도"}\n```')
    observation = VisionObservation.model_validate(parsed)

    assert observation.severity == "보통"
