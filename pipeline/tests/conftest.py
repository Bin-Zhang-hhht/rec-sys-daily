import pytest


@pytest.fixture(autouse=True)
def fixture_text_model(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_MODEL", "fixture-text-model")
