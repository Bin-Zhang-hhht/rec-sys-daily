from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from openai import APIConnectionError, APITimeoutError, AuthenticationError, InternalServerError, RateLimitError

from recsys_daily.config import load_config
from recsys_daily.llm import ModelOutputError, ModelRequestError, TextClient, TokenBudget


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
        environ={"DEEPSEEK_BASE_URL": "https://example.test/v1", "DEEPSEEK_API_KEY": "test-key", "DEEPSEEK_MODEL": "selected-text-model"},
    )
    messages = [{"role": "system", "content": "instructions"}, {"role": "user", "content": "document"}]
    result = client.complete_json(messages, SCHEMA)

    assert result == {"score": 3}
    assert observed["base_url"] == "https://example.test/v1"
    assert observed["api_key"] == "test-key"
    assert observed["model"] == "selected-text-model"
    assert observed["max_tokens"] == config.models.text.reserved_output_tokens
    assert observed["messages"] == [
        *messages,
        {
            "role": "user",
            "content": (
                "Return exactly one JSON object matching this JSON Schema. Do not add, omit, or rename keys.\n"
                + json.dumps(SCHEMA, ensure_ascii=False, sort_keys=True)
            ),
        },
    ]
    assert messages == [
        {"role": "system", "content": "instructions"},
        {"role": "user", "content": "document"},
    ]
    assert observed["response_format"] == {"type": "json_object"}
    assert "input" not in observed
    assert "text" not in observed
    assert not hasattr(client, "_limiter")


def test_text_client_uses_provider_agnostic_json_mode_and_validates_locally() -> None:
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
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"ok": {"type": "boolean", "minLength": 1, "minimum": 0, "maximum": 1}},
        "required": ["ok"],
    }
    assert client.complete_json(
        [{"role": "user", "content": "hello"}],
        schema,
    ) == {"ok": True}
    assert observed["response_format"] == {"type": "json_object"}


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


@pytest.mark.parametrize(
    ("error_type", "status", "attempts", "code"),
    [(RateLimitError, 429, 3, "model_rate_limited"),
     (InternalServerError, 503, 3, "model_service_unavailable"),
     (AuthenticationError, 401, 1, "model_access_denied")],
)
def test_text_client_isolates_exhausted_sdk_errors_without_provider_details(error_type, status, attempts, code) -> None:
    response = SimpleNamespace(
        status_code=status,
        headers={"Retry-After": "7"},
        request=SimpleNamespace(),
    )
    error = error_type("private provider response", response=response, body=None)
    completions = FakeChatCompletions({}, [error] * attempts)
    sleeps: list[float] = []
    client = TextClient(
        base_url="https://example.test/v1", api_key="key", model="model", retries=3,
        sleeper=sleeps.append,
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )

    with pytest.raises(ModelRequestError, match="text model request failed") as exc_info:
        client.complete_json([{"role": "user", "content": "hello"}], SCHEMA)

    assert sleeps == [7.0] * (attempts - 1)
    assert completions.outputs == []
    assert "private provider response" not in str(exc_info.value)
    assert exc_info.value.__suppress_context__
    assert exc_info.value.code == code


@pytest.mark.parametrize("error_type,code", [
    (APIConnectionError, "model_connection_failed"), (APITimeoutError, "model_timeout"),
])
def test_text_client_reports_sanitized_connection_failure_codes(error_type, code) -> None:
    error = error_type(request=SimpleNamespace())
    completions = FakeChatCompletions({}, [error])
    client = TextClient(
        base_url="https://example.test/v1", api_key="key", model="model", retries=1,
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )
    with pytest.raises(ModelRequestError) as exc_info:
        client.complete_json([], SCHEMA)
    assert exc_info.value.code == code


@pytest.mark.parametrize("content,finish_reason,code", [
    (None, "stop", "model_empty_content"),
    (" \n\t ", "stop", "model_empty_content"),
    ("private invalid output", "stop", "model_invalid_json"),
    ('{"private_field": true}', "stop", "model_schema_invalid"),
    ('{"score": 3}', "length", "model_output_limit"),
])
def test_text_client_reports_sanitized_output_failure_codes(content, finish_reason, code) -> None:
    response = _chat_response(content)
    response.choices[0].finish_reason = finish_reason
    client = TextClient(
        base_url="https://example.test/v1", api_key="key", model="model", retries=1,
        client=SimpleNamespace(chat=SimpleNamespace(completions=FakeChatCompletions({}, [response]))),
    )
    with pytest.raises(ModelOutputError) as exc_info:
        client.complete_json([], SCHEMA)
    assert exc_info.value.code == code
    assert "private" not in str(exc_info.value)


def test_text_client_retries_output_limit_before_accepting_complete_json() -> None:
    limited = _chat_response('{"score": 1}')
    limited.choices[0].finish_reason = "length"
    sleeps = []
    completions = FakeChatCompletions({}, [limited, _chat_response('{"score": 0.8}')])
    client = TextClient(
        base_url="https://example.test/v1", api_key="key", model="model", retries=2,
        sleeper=sleeps.append,
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )
    assert client.complete_json([], SCHEMA) == {"score": 0.8}
    assert sleeps == [1.0]
    assert completions.outputs == []


def test_text_client_corrects_output_retries_without_echoing_response_or_growing_feedback() -> None:
    calls = []
    outputs = iter([_chat_response('private invalid response'), _chat_response(' \n '), _chat_response('{"score": 0.8}')])

    def create(**kwargs):
        calls.append(kwargs["messages"])
        return next(outputs)

    messages = [{"role": "user", "content": "source document"}]
    client = TextClient(
        base_url="https://example.test/v1", api_key="key", model="model", retries=3,
        sleeper=lambda _: None,
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
    )
    assert client.complete_json(messages, SCHEMA) == {"score": 0.8}
    assert [len(call) for call in calls] == [2, 3, 3]
    assert calls[1][-1] == calls[2][-1]
    assert "JSON-escape" in calls[1][-1]["content"]
    assert all(call[:2] == calls[0] for call in calls[1:])
    assert "private invalid response" not in json.dumps(calls)
    assert messages == [{"role": "user", "content": "source document"}]


def test_text_client_does_not_add_output_correction_for_http_retries() -> None:
    calls = []

    class TransientError(RuntimeError):
        status_code = 503

    outputs = iter([TransientError(), _chat_response('{"score": 0.8}')])

    def create(**kwargs):
        calls.append(kwargs["messages"])
        output = next(outputs)
        if isinstance(output, Exception):
            raise output
        return output

    client = TextClient(
        base_url="https://example.test/v1", api_key="key", model="model", retries=2,
        sleeper=lambda _: None,
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
    )
    assert client.complete_json([], SCHEMA) == {"score": 0.8}
    assert calls[0] == calls[1]


def test_text_client_uses_model_common_timeout_and_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_config(__import__("pathlib").Path(__file__).parents[2])
    models = config.models.model_copy(
        update={"common": config.models.common.model_copy(update={"timeout_seconds": 17, "retries": 2})}
    )
    monkeypatch.setattr("recsys_daily.llm.OpenAI", lambda **_kwargs: SimpleNamespace())
    text = TextClient.from_config(
        models,
        environ={"DEEPSEEK_BASE_URL": "https://example.test/v1", "DEEPSEEK_API_KEY": "key", "DEEPSEEK_MODEL": "selected-text-model"},
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
