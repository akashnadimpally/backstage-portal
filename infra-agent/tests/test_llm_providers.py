"""LLM provider selection and client behaviour (HTTP is mocked; no network)."""

import pytest
from pydantic import ValidationError

from agent.foundry_client import FoundryClient
from agent.llm import LLMError, _strip_fences, create_llm
from agent.models import ParsedRequirements
from agent.ollama_client import OllamaClient
from agent.settings import FoundrySettings, Settings

BASE = {"modules": {"root": "x"}, "policy": {"allowed_locations": ["eastus"]}}
VALID = '{"summary": "summary", "components": [{"module": "m", "purpose": "purpose", "evidence": "evidence"}]}'


class FakeResponse:
    def __init__(self, status, body=None, text=""):
        self.status_code = status
        self._body = body or {}
        self.text = text
        self.headers = {}

    def json(self):
        return self._body


def _choice(content, finish="stop"):
    return FakeResponse(200, {"choices": [{"message": {"content": content}, "finish_reason": finish}]})


def test_exactly_one_provider_must_be_enabled():
    assert Settings.model_validate(BASE).llm_provider == "ollama"
    s = Settings.model_validate({**BASE, "ollama": {"enabled": False}, "azure_foundry": {"enabled": True}})
    assert s.llm_provider == "azure_foundry" and isinstance(create_llm(s), FoundryClient)
    with pytest.raises(ValidationError, match="exactly one LLM provider"):
        Settings.model_validate({**BASE, "azure_foundry": {"enabled": True}})
    with pytest.raises(ValidationError, match="exactly one LLM provider"):
        Settings.model_validate({**BASE, "ollama": {"enabled": False}})
    assert isinstance(create_llm(Settings.model_validate(BASE)), OllamaClient)


def test_foundry_url_and_placeholder():
    assert FoundrySettings(endpoint="https://r.services.ai.azure.com/").chat_url == \
        "https://r.services.ai.azure.com/openai/v1/chat/completions"
    assert FoundrySettings(endpoint="https://r.openai.azure.com/openai/v1").chat_url == \
        "https://r.openai.azure.com/openai/v1/chat/completions"
    with pytest.raises(LLMError, match="placeholder"):
        FoundryClient(FoundrySettings()).ensure_configured()


@pytest.fixture
def foundry(monkeypatch):
    monkeypatch.setenv("AZURE_FOUNDRY_API_KEY", "k")
    cfg = FoundrySettings(enabled=True, endpoint="https://r.services.ai.azure.com", auth="api_key")
    return FoundryClient(cfg)


def test_foundry_request_shape(foundry, monkeypatch):
    sent = []

    def post(url, json, headers, timeout):
        sent.append((url, json, headers))
        return _choice("```json\n" + VALID + "\n```")

    monkeypatch.setattr("agent.foundry_client.requests.post", post)
    out = foundry.chat_json("parser", "sys", "usr", ParsedRequirements)
    assert out.components[0].module == "m"
    url, body, headers = sent[0]
    assert url.endswith("/openai/v1/chat/completions")
    assert headers["api-key"] == "k"
    assert body["model"] == "gpt-4.1" and body["temperature"] == 0.0
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["name"] == "ParsedRequirements"


def test_foundry_falls_back_on_unsupported_temperature_and_schema(foundry, monkeypatch):
    sent = []
    responses = [
        FakeResponse(400, text="Unsupported parameter: 'temperature' is not supported with this model."),
        FakeResponse(400, text="Invalid parameter: response_format of type 'json_schema' is not supported"),
        _choice(VALID),
    ]

    def post(url, json, headers, timeout):
        sent.append(json)
        return responses.pop(0)

    monkeypatch.setattr("agent.foundry_client.requests.post", post)
    foundry.chat_json("parser", "sys", "usr", ParsedRequirements)
    assert "temperature" not in sent[1] and "temperature" not in sent[2]
    assert sent[2]["response_format"] == {"type": "json_object"}


def test_foundry_retries_invalid_json_then_errors_clearly(foundry, monkeypatch):
    monkeypatch.setattr("agent.foundry_client.requests.post", lambda *a, **k: _choice("not json"))
    with pytest.raises(LLMError, match="did not return valid ParsedRequirements"):
        foundry.chat_json("parser", "sys", "usr", ParsedRequirements)
    monkeypatch.setattr("agent.foundry_client.requests.post", lambda *a, **k: FakeResponse(404, text="DeploymentNotFound"))
    with pytest.raises(LLMError, match="deployment 'gpt-4.1' not found"):
        foundry.chat_json("parser", "sys", "usr", ParsedRequirements)


def test_foundry_api_key_env_required(monkeypatch):
    monkeypatch.delenv("AZURE_FOUNDRY_API_KEY", raising=False)
    client = FoundryClient(FoundrySettings(endpoint="https://r.services.ai.azure.com", auth="api_key"))
    with pytest.raises(LLMError, match="AZURE_FOUNDRY_API_KEY"):
        client.ensure_configured()


def test_ollama_null_temperature_and_think_blocks(monkeypatch):
    s = Settings.model_validate({**BASE, "ollama": {"base_url": "http://h:11434", "temperature": {"parser": None}}})
    sent = []

    def post(url, json, timeout):
        sent.append(json)
        return FakeResponse(200, {"message": {"content": "<think>hmm</think>" + VALID}})

    monkeypatch.setattr("agent.ollama_client.requests.post", post)
    create_llm(s).chat_json("parser", "sys", "usr", ParsedRequirements)
    assert "temperature" not in sent[0]["options"]
    assert _strip_fences("<think>a</think>\n```json\n{}\n```") == "{}"
