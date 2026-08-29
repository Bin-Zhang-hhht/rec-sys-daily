from __future__ import annotations

from types import SimpleNamespace

import pytest

from recsys_daily.config import load_config
from recsys_daily.llm import ModelOutputError, TextClient, TokenBudget


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"score": {"type": "number"}},
    "required": ["score"],
}


def _chat_response(content: object) -> object:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class FakeChatCompletions:
    def __init__(self, observed: dict[str, object], outputs: list[object] | None = None) -> None:
        self.observed = observed
        self.outputs = outputs or [_chat_response('{"score": 3}')]

    def create(self, **kwargs: object) -> object:
        self.observed.update(kwargs)
        value = self.outputs.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def test_text_client_uses_single_model_chat_completions_api_and_parses_json(monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_config(__import__("pathlib").Path(__file__).parents[2])
    observed: dict[str, object] = {}

    class FakeOpenAI:
        def __init__(self, **kwargs: object) -> None:
            observed.update(kwargs)
            self.chat = SimpleNamespace(completions=FakeChatCompletions(observed))

    monkeypatch.setattr("recsys_daily.llm.OpenAI", FakeOpenAI)
    client = TextClient.from_config(
        config.models,
        environ={"DEEPSEEK_BASE_URL": "https://example.test/v1", "DEEPSEEK_API_KEY": "test-key"},
    )
    messages = [{"role": "system", "content": "instructions"}, {"role": "user", "content": "document"}]
    result = client.complete_json(messages, SCHEMA)

    assert result == {"score": 3}
    assert observed["base_url"] == "https://example.test/v1"
    assert observed["api_key"] == "test-key"
    assert observed["model"] == config.models.text.model
    assert observed["max_tokens"] == config.models.text.reserved_output_tokens
    assert observed["messages"] == messages
    assert observed["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "response", "strict": True, "schema": SCHEMA},
    }
    assert "input" not in observed
    assert "text" not in observed
    assert not hasattr(client, "_limiter")


def test_text_client_strips_unsupported_provider_schema_constraints() -> None:
    observed: dict[str, object] = {}
    client = TextClient(
        base_url="https://example.test/v1",
        api_key="key",
        model="model",
        retries=1,
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=FakeChatCompletions(observed, [_chat_response('{"ok": true}')]))
        ),
    )
    client.complete_json(
        [{"role": "user", "content": "hello"}],
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {"ok": {"type": "boolean", "minLength": 1, "minimum": 0, "maximum": 1}},
            "required": ["ok"],
        },
    )

    schema = observed["response_format"]["json_schema"]["schema"]  # type: ignore[index]
    assert schema["properties"]["ok"] == {"type": "boolean", "minimum": 0, "maximum": 1}


@pytest.mark.parametrize("output", [None, "", "not json", "[]"])
def test_text_client_rejects_missing_or_invalid_message_content(output: object) -> None:
    client = TextClient(
        base_url="https://example.test/v1",
        api_key="key",
        model="model",
        retries=1,
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=FakeChatCompletions({}, [_chat_response(output)]))
        ),
    )
    with pytest.raises(ModelOutputError, match="JSON text|valid JSON|object"):
        client.complete_json([{"role": "user", "content": "hello"}], SCHEMA)


@pytest.mark.parametrize(
    "response",
    [SimpleNamespace(), SimpleNamespace(choices=[]), SimpleNamespace(choices=[SimpleNamespace()])],
)
def test_text_client_rejects_missing_completion_choice_or_message(response: object) -> None:
    client = TextClient(
        base_url="https://example.test/v1",
        api_key="key",
        model="model",
        retries=1,
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=FakeChatCompletions({}, [response]))
        ),
    )
    with pytest.raises(ModelOutputError, match="choice|JSON text"):
        client.complete_json([{"role": "user", "content": "hello"}], SCHEMA)


def test_text_client_retries_invalid_json() -> None:
    sleeps: list[float] = []
    completions = FakeChatCompletions(
        {},
        [_chat_response('{"score":'), _chat_response('{"score": 0.8}')],
    )
    client = TextClient(
        base_url="https://example.test/v1",
        api_key="key",
        model="model",
        retries=2,
        sleeper=sleeps.append,
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )

    assert client.complete_json([{"role": "user", "content": "hello"}], SCHEMA) == {"score": 0.8}
    assert sleeps == [1.0]


def test_text_client_retries_schema_mismatch_and_fails_explicitly() -> None:
    sleeps: list[float] = []
    completions = FakeChatCompletions(
        {},
        [_chat_response('{"unexpected": true}'), _chat_response('{"score": 2}')],
    )
    client = TextClient(
        base_url="https://example.test/v1",
        api_key="key",
        model="model",
        retries=2,
        sleeper=sleeps.append,
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )

    bounded_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"score": {"type": "number", "minimum": 0, "maximum": 1}},
        "required": ["score"],
    }
    with pytest.raises(ModelOutputError, match="requested schema"):
        client.complete_json([{"role": "user", "content": "hello"}], bounded_schema)
    assert sleeps == [1.0]


def test_text_client_requires_environment_without_exposing_values() -> None:
    config = load_config(__import__("pathlib").Path(__file__).parents[2])
    with pytest.raises(ValueError) as exc_info:
        TextClient.from_config(config.models, environ={"DEEPSEEK_API_KEY": "secret-value"})
    assert "secret-value" not in str(exc_info.value)


def test_text_client_retries_transient_errors() -> None:
    class TransientError(RuntimeError):
        status_code = 503

    sleeps: list[float] = []
    completions = FakeChatCompletions({}, [TransientError(), _chat_response('{"score": 3}')])
    client = TextClient(
        base_url="https://example.test/v1",
        api_key="key",
        model="model",
        retries=2,
        sleeper=sleeps.append,
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )
    assert client.complete_json([{"role": "user", "content": "hello"}], SCHEMA) == {"score": 3}
    assert sleeps == [1.0]


def test_text_client_uses_model_common_timeout_and_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_config(__import__("pathlib").Path(__file__).parents[2])
    models = config.models.model_copy(
        update={"common": config.models.common.model_copy(update={"timeout_seconds": 17, "retries": 2})}
    )
    monkeypatch.setattr("recsys_daily.llm.OpenAI", lambda **_kwargs: SimpleNamespace())
    text = TextClient.from_config(
        models,
        environ={"DEEPSEEK_BASE_URL": "https://example.test/v1", "DEEPSEEK_API_KEY": "key"},
    )
    assert (text.timeout_seconds, text.retries, text.max_output_tokens) == (
        17,
        2,
        config.models.text.reserved_output_tokens,
    )


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
