"""Provider-neutral LLM interface.

Every provider only implements ``_complete`` (one chat round-trip returning
text). The JSON/Pydantic validation and retry loop is shared, so the agents
behave identically whether Ollama or Azure AI Foundry is enabled.
"""

from __future__ import annotations

import json
import re
import time
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from .settings import LLM_ROLES, LLMProviderSettings, Settings

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class StructuredOutputUnsupported(LLMError):
    """Raised by a provider when the model rejects schema-constrained output."""


class LLMClient(Protocol):
    def chat_json(self, role: str, system: str, user: str, response_model: type[T], recorder=None) -> T: ...


class JSONChatClient:
    provider = "base"

    def __init__(self, cfg: LLMProviderSettings):
        self.cfg = cfg

    # ----------------------------------------------------------- provider API
    def ensure_configured(self) -> None:
        raise NotImplementedError

    def health(self) -> dict:
        raise NotImplementedError

    def _complete(self, role: str, messages: list[dict], schema: dict, schema_name: str, use_schema: bool) -> str:
        raise NotImplementedError

    # -------------------------------------------------------------- shared
    def model_for(self, role: str) -> str:
        return getattr(self.cfg.models, role)

    def temperature_for(self, role: str) -> float | None:
        return getattr(self.cfg.temperature, role)

    def distinct_models(self) -> list[str]:
        return sorted({self.model_for(r) for r in LLM_ROLES})

    def chat_json(self, role: str, system: str, user: str, response_model: type[T], recorder=None) -> T:
        """Ask ``role``'s model for JSON matching ``response_model``; retry with the validation error."""
        self.ensure_configured()
        schema = response_model.model_json_schema()
        use_schema = self.cfg.structured_output == "schema"
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last_error = ""
        attempt = 0
        while attempt <= self.cfg.max_json_retries:
            started = time.time()
            try:
                content = self._complete(role, messages, schema, response_model.__name__, use_schema).strip()
            except StructuredOutputUnsupported:
                if not use_schema:
                    raise
                use_schema = False
                continue
            record = {
                "provider": self.provider,
                "role": role,
                "model": self.model_for(role),
                "attempt": attempt,
                "structured_output": "schema" if use_schema else "json",
                "elapsed_s": round(time.time() - started, 2),
                "messages": messages,
                "response": content,
            }
            try:
                result = response_model.model_validate(json.loads(_strip_fences(content)))
                record["valid"] = True
                if recorder:
                    recorder(role, record)
                return result
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = str(exc)[:2000]
                record.update(valid=False, error=last_error)
                if recorder:
                    recorder(role, record)
                messages = messages + [
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": "Your previous answer was rejected by the schema validator:\n"
                        f"{last_error}\nReturn ONLY corrected JSON that matches the schema.",
                    },
                ]
            attempt += 1
        raise LLMError(f"{role} model did not return valid {response_model.__name__}: {last_error}")


_THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)


def _strip_fences(text: str) -> str:
    """Remove reasoning blocks (<think>...</think>) and ```json fences some models add around JSON."""
    t = _THINK_RE.sub("", text).strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def create_llm(settings: Settings) -> JSONChatClient:
    """Instantiate the single provider enabled in config.yaml."""
    if settings.llm_provider == "azure_foundry":
        from .foundry_client import FoundryClient

        return FoundryClient(settings.azure_foundry)
    from .ollama_client import OllamaClient

    return OllamaClient(settings.ollama)
