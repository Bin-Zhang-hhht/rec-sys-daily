"""Synchronous OpenAI-compatible Chat Completions API text client."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from openai import APIConnectionError, OpenAI

from .config import ModelConfig
from .rate_limit import request_with_retries


class ModelOutputError(ValueError):
    """A retryable malformed response without retaining provider output."""


def _json_content(content: Any) -> dict[str, Any]:
    if not isinstance(content, str):
        raise ModelOutputError("model response content must be JSON text")
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]) if len(lines) >= 3 else ""
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ModelOutputError("model response is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ModelOutputError("model response JSON must be an object")
    return value


@dataclass
class TokenBudget:
    context_window_tokens: int
    prompt_tokens: int = 0
    schema_tokens: int = 0
    output_tokens: int = 0

    def __init__(
        self,
        context_window_tokens: int,
        prompt_tokens: int = 0,
        schema_tokens: int = 0,
        output_tokens: int = 0,
        *,
        reserved_prompt_tokens: int | None = None,
        reserved_output_tokens: int | None = None,
    ) -> None:
        self.context_window_tokens = context_window_tokens
        self.prompt_tokens = prompt_tokens if reserved_prompt_tokens is None else reserved_prompt_tokens
        self.schema_tokens = schema_tokens
        self.output_tokens = output_tokens if reserved_output_tokens is None else reserved_output_tokens

    @property
    def available_tokens(self) -> int:
        return self.context_window_tokens - self.prompt_tokens - self.schema_tokens - self.output_tokens

    @staticmethod
    def _tokens(text: str) -> int:
        return max(1, (len(text) + 3) // 4)

    def fit_sections(self, sections: Sequence[Mapping[str, Any] | str]) -> str:
        if self.available_tokens <= 0:
            raise ValueError("token budget is exhausted")
        normalized: list[tuple[int, int, str]] = []
        for index, section in enumerate(sections):
            if isinstance(section, str):
                heading, text, importance = "", section, 0
            else:
                heading = str(section.get("heading", section.get("name", "")))
                text = str(section.get("text", section.get("content", "")))
                importance = int(section.get("importance", section.get("priority", 0)))
            rendered = f"{heading}\n{text}".strip() if heading else text
            normalized.append((importance, index, rendered))
        selected: list[tuple[int, str]] = []
        omitted: list[str] = []
        used = 0
        for importance, index, rendered in sorted(normalized, key=lambda item: (-item[0], item[1])):
            needed = self._tokens(rendered)
            if needed <= self.available_tokens - used:
                selected.append((index, rendered))
                used += needed
            else:
                # Keep source text out of exception messages and logs.
                omitted.append(f"section-{index}")
        if omitted:
            labels = ", ".join(omitted[:3])
            suffix = "..." if len(omitted) > 3 else ""
            raise ValueError(f"token budget cannot fit all sections; omitted: {labels}{suffix}")
        if normalized and not selected:
            raise ValueError("section cannot fit token budget")
        return "\n\n".join(rendered for _, rendered in sorted(selected))


class TextClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        max_output_tokens: int | None = None,
        timeout_seconds: int | None = None,
        retries: int | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        client: Any | None = None,
    ) -> None:
        self.base_url, self.api_key, self.model = base_url, api_key, model
        self.max_output_tokens = max_output_tokens
        self.timeout_seconds, self.retries = timeout_seconds, retries
        self._sleeper = sleeper
        self._client = client or OpenAI(api_key=api_key, base_url=base_url, timeout=timeout_seconds)

    @classmethod
    def from_config(
        cls,
        models: ModelConfig,
        environ: Mapping[str, str] | None = None,
        *,
        timeout_seconds: int | None = None,
        retries: int | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> "TextClient":
        env = os.environ if environ is None else environ
        text = models.text
        base_url, api_key = env.get(text.base_url_env), env.get(text.api_key_env)
        if not base_url or not api_key:
            raise ValueError("text model environment variables are missing")
        common = models.common
        return cls(
            base_url=base_url,
            api_key=api_key,
            model=text.model,
            max_output_tokens=text.reserved_output_tokens,
            timeout_seconds=common.timeout_seconds if timeout_seconds is None else timeout_seconds,
            retries=common.retries if retries is None else retries,
            sleeper=sleeper,
        )

    def complete_json(self, messages: Sequence[Mapping[str, Any]], schema: Mapping[str, Any]) -> dict[str, Any]:
        validator = Draft202012Validator(schema)
        schema_message = {
            "role": "user",
            "content": (
                "Return exactly one JSON object matching this JSON Schema. Do not add, omit, or rename keys.\n"
                + json.dumps(schema, ensure_ascii=False, sort_keys=True)
            ),
        }
        request_messages = [*messages, schema_message]

        def operation() -> dict[str, Any]:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=request_messages,
                # DeepSeek supports JSON mode across both legacy and current
                # Chat Completions environments. Full schema validation stays
                # local below because JSON mode only guarantees valid JSON.
                response_format={"type": "json_object"},
                max_tokens=self.max_output_tokens,
                temperature=0.6,
            )
            choices = getattr(response, "choices", None)
            if (
                not isinstance(choices, Sequence)
                or isinstance(choices, (str, bytes))
                or not choices
            ):
                raise ModelOutputError("model response must contain a completion choice")
            message = getattr(choices[0], "message", None)
            value = _json_content(getattr(message, "content", None))
            try:
                validator.validate(value)
            except JsonSchemaValidationError as exc:
                raise ModelOutputError("model response does not match the requested schema") from exc
            return value

        return request_with_retries(
            operation,
            sleeper=self._sleeper,
            max_attempts=self.retries,
            retry_on_exceptions=(APIConnectionError, ModelOutputError),
        )
