"""Ollama provider (/api/chat)."""

from __future__ import annotations

import requests

from .llm import JSONChatClient, LLMError, StructuredOutputUnsupported
from .settings import OllamaSettings


class OllamaClient(JSONChatClient):
    provider = "ollama"

    def __init__(self, cfg: OllamaSettings):
        super().__init__(cfg)
        self.cfg: OllamaSettings = cfg
        self.base_url = cfg.base_url.rstrip("/")

    def ensure_configured(self) -> None:
        if self.cfg.is_placeholder:
            raise LLMError(
                "Ollama host is still the placeholder. Set OLLAMA_BASE_URL (e.g. http://10.0.0.5:11434) "
                "or update ollama.base_url in config.yaml."
            )

    def health(self) -> dict:
        self.ensure_configured()
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=15)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise LLMError(f"cannot reach Ollama at {self.base_url}: {exc}") from exc
        installed = [m.get("name", "") for m in resp.json().get("models", [])]
        missing = [n for n in self.distinct_models()
                   if not any(i == n or i.startswith(n + ":") for i in installed)]
        return {"provider": self.provider, "base_url": self.base_url, "installed": installed, "missing": missing}

    def _complete(self, role: str, messages: list[dict], schema: dict, schema_name: str, use_schema: bool) -> str:
        options: dict = {"num_ctx": self.cfg.num_ctx}
        temperature = self.temperature_for(role)
        if temperature is not None:
            options["temperature"] = temperature
        payload = {
            "model": self.model_for(role),
            "messages": messages,
            "stream": False,
            "format": schema if use_schema else "json",
            "options": options,
        }
        try:
            resp = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=self.cfg.timeout_seconds)
        except requests.RequestException as exc:
            raise LLMError(f"cannot reach Ollama at {self.base_url}: {exc}") from exc
        if resp.status_code >= 400:
            if use_schema and ("format" in resp.text.lower() or "schema" in resp.text.lower()):
                raise StructuredOutputUnsupported(resp.text[:300])
            raise LLMError(f"Ollama HTTP {resp.status_code}: {resp.text[:500]}")
        return (resp.json().get("message") or {}).get("content", "")
