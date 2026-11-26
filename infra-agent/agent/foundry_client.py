"""Azure AI Foundry provider (Azure OpenAI v1-compatible chat completions).

POST {endpoint}/openai/v1/chat/completions  with  {"model": <deployment name>, ...}
Works for Azure OpenAI deployments and other Foundry model deployments on an
AI Services / Foundry resource. Authentication: Microsoft Entra ID
(DefaultAzureCredential, recommended) or an API key read from an env var.
"""

from __future__ import annotations

import os
import threading
import time

import requests

from .llm import JSONChatClient, LLMError, StructuredOutputUnsupported
from .settings import FoundrySettings

_RETRY_STATUS = {429, 500, 502, 503, 504}
_MAX_HTTP_RETRIES = 4


class FoundryClient(JSONChatClient):
    provider = "azure_foundry"

    def __init__(self, cfg: FoundrySettings):
        super().__init__(cfg)
        self.cfg: FoundrySettings = cfg
        self._credential = None
        self._token: tuple[str, float] | None = None
        self._lock = threading.Lock()
        self._no_temperature: set[str] = set()

    # --------------------------------------------------------------- config
    def ensure_configured(self) -> None:
        if self.cfg.is_placeholder:
            raise LLMError(
                "Azure AI Foundry endpoint is still the placeholder. Set AZURE_FOUNDRY_ENDPOINT "
                "(e.g. https://<resource>.services.ai.azure.com) or update azure_foundry.endpoint in config.yaml."
            )
        if self.cfg.auth == "api_key" and not os.environ.get(self.cfg.api_key_env):
            raise LLMError(f"azure_foundry.auth is 'api_key' but environment variable {self.cfg.api_key_env} is not set")

    def _auth_headers(self) -> dict[str, str]:
        if self.cfg.auth == "api_key":
            return {"api-key": os.environ[self.cfg.api_key_env]}
        with self._lock:
            if self._token is None or self._token[1] - time.time() < 300:
                if self._credential is None:
                    try:
                        from azure.identity import DefaultAzureCredential
                    except ImportError as exc:
                        raise LLMError("azure-identity is required for Entra ID auth: pip install azure-identity") from exc
                    self._credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
                try:
                    tok = self._credential.get_token(self.cfg.token_scope)
                except Exception as exc:  # noqa: BLE001 - surface any credential failure clearly
                    raise LLMError(f"could not get an Entra ID token for {self.cfg.token_scope}: {exc}") from exc
                self._token = (tok.token, float(tok.expires_on))
            return {"Authorization": f"Bearer {self._token[0]}"}

    # ----------------------------------------------------------------- http
    def _post(self, payload: dict) -> requests.Response:
        delay = 2.0
        for attempt in range(_MAX_HTTP_RETRIES + 1):
            try:
                resp = requests.post(
                    self.cfg.chat_url,
                    json=payload,
                    headers={**self._auth_headers(), "Content-Type": "application/json"},
                    timeout=self.cfg.timeout_seconds,
                )
            except requests.RequestException as exc:
                if attempt == _MAX_HTTP_RETRIES:
                    raise LLMError(f"cannot reach Azure AI Foundry at {self.cfg.chat_url}: {exc}") from exc
                time.sleep(delay)
                delay *= 2
                continue
            if resp.status_code in _RETRY_STATUS and attempt < _MAX_HTTP_RETRIES:
                retry_after = resp.headers.get("retry-after")
                time.sleep(float(retry_after) if retry_after and retry_after.isdigit() else delay)
                delay *= 2
                continue
            return resp
        raise LLMError("Azure AI Foundry request failed after retries")

    def _complete(self, role: str, messages: list[dict], schema: dict, schema_name: str, use_schema: bool) -> str:
        model = self.model_for(role)
        payload: dict = {"model": model, "messages": messages}
        temperature = self.temperature_for(role)
        if temperature is not None and model not in self._no_temperature:
            payload["temperature"] = temperature
        if self.cfg.max_output_tokens:
            payload["max_completion_tokens"] = self.cfg.max_output_tokens
        if use_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": schema, "strict": False},
            }
        else:
            payload["response_format"] = {"type": "json_object"}

        resp = self._post(payload)
        if resp.status_code == 400:
            body = resp.text.lower()
            if "temperature" in body and "temperature" in payload:
                self._no_temperature.add(model)
                return self._complete(role, messages, schema, schema_name, use_schema)
            if use_schema and ("response_format" in body or "json_schema" in body):
                raise StructuredOutputUnsupported(resp.text[:300])
        if resp.status_code in (401, 403):
            raise LLMError(f"Azure AI Foundry auth failed (HTTP {resp.status_code}); check azure_foundry.auth and "
                           f"RBAC ('Cognitive Services OpenAI User' / 'Azure AI User'): {resp.text[:300]}")
        if resp.status_code == 404:
            raise LLMError(f"Azure AI Foundry deployment '{model}' not found at {self.cfg.chat_url}: {resp.text[:300]}")
        if resp.status_code >= 400:
            raise LLMError(f"Azure AI Foundry HTTP {resp.status_code}: {resp.text[:500]}")

        choice = (resp.json().get("choices") or [{}])[0]
        message = choice.get("message") or {}
        if message.get("refusal"):
            raise LLMError(f"model refused the request: {message['refusal'][:300]}")
        if choice.get("finish_reason") == "length":
            raise LLMError(f"deployment '{model}' hit the output token limit; raise azure_foundry.max_output_tokens")
        return message.get("content") or ""

    # --------------------------------------------------------------- health
    def health(self) -> dict:
        self.ensure_configured()
        results = {}
        for model in self.distinct_models():
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": 'Reply with the JSON {"ok": true}'}],
                "response_format": {"type": "json_object"},
            }
            resp = self._post(payload)
            results[model] = "ok" if resp.status_code < 400 else f"HTTP {resp.status_code}: {resp.text[:200]}"
        missing = [m for m, r in results.items() if r != "ok"]
        return {"provider": self.provider, "endpoint": self.cfg.chat_url, "auth": self.cfg.auth,
                "deployments": results, "missing": missing}
