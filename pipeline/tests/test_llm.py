from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from recsys_daily.config import load_config
from recsys_daily.llm import TextClient, TokenBudget, VisionClient


def test_text_client_uses_active_profile_and_parses_json(monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_config(__import__("pathlib").Path(__file__).parents[2])
    observed: dict[str, object] = {}

    class FakeCompletions:
        def create(self, **kwargs: object) -> object:
            observed.update(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"score": 3}', reasoning_content="secret"))])

    class FakeOpenAI:
        def __init__(self, **kwargs: object) -> None:
            observed.update(kwargs)
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr("recsys_daily.llm.OpenAI", FakeOpenAI)
    client = TextClient.from_config(config.models, environ={"NVIDIA_BASE_URL": "https://example.test/v1", "NVIDIA_API_KEY": "test-key"})

    result = client.complete_json([{"role": "user", "content": "hello"}], {"type": "object"})

    assert result == {"score": 3}
    assert observed["base_url"] == "https://example.test/v1"
    assert observed["api_key"] == "test-key"
    assert observed["model"] == config.models.text.active().model
    assert "reasoning_content" not in result


def test_text_and_vision_clients_use_model_common_timeout_and_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_config(__import__("pathlib").Path(__file__).parents[2])
    models = config.models.model_copy(update={"common": config.models.common.model_copy(update={"timeout_seconds": 17, "retries": 2})})
    monkeypatch.setattr("recsys_daily.llm.OpenAI", lambda **kwargs: SimpleNamespace())
    text = TextClient.from_config(models, environ={"NVIDIA_BASE_URL": "https://example.test/v1", "NVIDIA_API_KEY": "key"})
    vision = VisionClient.from_config(models, {"NVIDIA_VLM_INVOKE_URL": "https://example.test/v1/chat/completions", "NVIDIA_API_KEY": "key"})
    assert (text.timeout_seconds, text.retries) == (17, 2)
    assert (vision.timeout_seconds, vision.retries) == (17, 2)


def test_vision_builds_single_multimage_request_and_ignores_reasoning(monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_config(__import__("pathlib").Path(__file__).parents[2])
    observed: dict[str, object] = {}

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"choices": [{"message": {"content": '{"status":"ok"}', "reasoning_content": "private"}}]}

    def post(url: str, **kwargs: object) -> Response:
        observed["url"] = url
        observed.update(kwargs)
        return Response()

    monkeypatch.setattr("recsys_daily.llm.requests.post", post)
    client = VisionClient.from_config(
        config.models,
        {"NVIDIA_VLM_INVOKE_URL": "https://example.test/v1/chat/completions", "NVIDIA_API_KEY": "test-key"},
    )
    result = client.analyze("inspect", ["data:image/png;base64,a", "data:image/png;base64,b"])

    assert result == {"status": "ok"}
    assert observed["url"] == "https://example.test/v1/chat/completions"
    assert observed["headers"]["Accept"] == "application/json"
    payload = observed["json"]
    assert payload["max_tokens"] == 65536
    assert payload["reasoning_budget"] == 16384
    assert payload["stream"] is False
    assert payload["messages"][0]["content"][0]["type"] == "text"
    assert [part["image_url"]["url"] for part in payload["messages"][0]["content"][1:]] == ["data:image/png;base64,a", "data:image/png;base64,b"]


def test_token_budget_fails_instead_of_silently_dropping_sections() -> None:
    budget = TokenBudget(context_window_tokens=100, prompt_tokens=10, schema_tokens=10, output_tokens=20)
    sections = [
        {"heading": "low", "text": "x" * 240, "importance": 1},
        {"heading": "high", "text": "y" * 80, "importance": 10},
    ]

    with pytest.raises(ValueError, match="omitted|fit"):
        budget.fit_sections(sections)


def test_token_budget_fails_when_one_section_cannot_fit() -> None:
    budget = TokenBudget(context_window_tokens=40, prompt_tokens=10, schema_tokens=10, output_tokens=10)
    with pytest.raises(ValueError, match="token budget"):
        budget.fit_sections([{"heading": "too big", "text": "z" * 200, "importance": 1}])
