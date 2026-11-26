"""Typed application settings loaded from config.yaml (with env overrides)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"
OLLAMA_PLACEHOLDER = "OLLAMA_HOST_PLACEHOLDER"
FOUNDRY_PLACEHOLDER = "FOUNDRY_RESOURCE_PLACEHOLDER"
LLM_ROLES = ("parser", "generator", "reviewer", "repair")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RoleModels(_Strict):
    """Model (Ollama) or deployment name (Azure AI Foundry) used for each agent role."""

    parser: str = "llama3.2"
    generator: str = "llama3.2"
    reviewer: str = "llama3.2"
    repair: str = "llama3.2"


class RoleTemperatures(_Strict):
    """Use null for models that reject a temperature (e.g. reasoning models)."""

    parser: float | None = 0.0
    generator: float | None = 0.1
    reviewer: float | None = 0.0
    repair: float | None = 0.0


class LLMProviderSettings(_Strict):
    enabled: bool = False
    timeout_seconds: int = Field(default=600, ge=10)
    structured_output: Literal["schema", "json"] = "schema"
    max_json_retries: int = Field(default=2, ge=0, le=5)
    models: RoleModels = RoleModels()
    temperature: RoleTemperatures = RoleTemperatures()


class OllamaSettings(LLMProviderSettings):
    enabled: bool = True
    base_url: str = f"http://{OLLAMA_PLACEHOLDER}:11434"
    num_ctx: int = Field(default=16384, ge=2048)

    @property
    def is_placeholder(self) -> bool:
        return OLLAMA_PLACEHOLDER in self.base_url


class FoundrySettings(LLMProviderSettings):
    """Azure AI Foundry (Azure OpenAI v1-compatible chat completions)."""

    endpoint: str = f"https://{FOUNDRY_PLACEHOLDER}.services.ai.azure.com"
    auth: Literal["entra_id", "api_key"] = "entra_id"
    api_key_env: str = "AZURE_FOUNDRY_API_KEY"
    token_scope: str = "https://ai.azure.com/.default"
    max_output_tokens: int | None = Field(default=None, ge=256)
    models: RoleModels = RoleModels(parser="gpt-4.1", generator="gpt-4.1", reviewer="gpt-4.1", repair="gpt-4.1")

    @property
    def is_placeholder(self) -> bool:
        return FOUNDRY_PLACEHOLDER in self.endpoint

    @property
    def chat_url(self) -> str:
        base = self.endpoint.rstrip("/")
        if not base.endswith("/openai/v1"):
            base += "/openai/v1"
        return base + "/chat/completions"


class ModuleSettings(_Strict):
    root: str
    catalog_file: str = "module-catalog/catalog.yaml"


class PathSettings(_Strict):
    generated_dir: str = "generated/requests"
    plugin_cache_dir: str = ".terraform-plugin-cache"


class TerraformTimeouts(_Strict):
    init: int = 900
    validation: int = 300
    plan: int = 1800
    apply: int = 3600


class TerraformSettings(_Strict):
    binary: str = "terraform"
    required_version: str = ">= 1.5.0"
    azurerm_version: str = "~> 4.0"
    timeouts: TerraformTimeouts = TerraformTimeouts()


class ToolCheck(_Strict):
    enabled: bool = True
    required: bool = False
    fail_on_findings: bool = True


class SecurityCheck(ToolCheck):
    tool: Literal["checkov", "trivy", "tfsec", "none"] = "checkov"


class StaticChecks(_Strict):
    tflint: ToolCheck = ToolCheck()
    security: SecurityCheck = SecurityCheck()


class LoopSettings(_Strict):
    max_generation_attempts: int = Field(default=3, ge=1, le=10)
    max_review_rounds: int = Field(default=3, ge=1, le=10)
    max_repair_attempts: int = Field(default=5, ge=1, le=10)
    max_transient_retries: int = Field(default=2, ge=0, le=5)


class PolicySettings(_Strict):
    allowed_locations: list[str] = Field(min_length=1)
    allowed_modules: list[str] = ["*"]
    denied_modules: list[str] = []
    max_module_calls: int = Field(default=25, ge=1)
    max_resource_changes: int = Field(default=100, ge=1)
    allow_destroy: bool = False
    allow_replace: bool = False
    allowed_providers: list[str] = ["registry.terraform.io/hashicorp/azurerm"]
    denied_resource_types: list[str] = []
    required_tags: list[str] = ["environment", "managed_by", "request_id"]
    sensitive_input_patterns: list[str] = [
        "password",
        "secret",
        "connection_string",
        "access_key",
        "private_key",
        "sas_token",
    ]
    forbidden_input_values: dict[str, list[object]] = {}

    @field_validator("allowed_locations")
    @classmethod
    def _lower(cls, v: list[str]) -> list[str]:
        return [x.strip().lower() for x in v]


class ApprovalSettings(_Strict):
    token_ttl_minutes: int = Field(default=60, ge=1, le=1440)
    secret_env: str = "INFRA_AGENT_APPROVAL_SECRET"


class AzureSettings(_Strict):
    subscription_id_env: str = "ARM_SUBSCRIPTION_ID"


class Settings(_Strict):
    ollama: OllamaSettings = OllamaSettings()
    azure_foundry: FoundrySettings = FoundrySettings()
    modules: ModuleSettings
    paths: PathSettings = PathSettings()
    terraform: TerraformSettings = TerraformSettings()
    static_checks: StaticChecks = StaticChecks()
    loops: LoopSettings = LoopSettings()
    policy: PolicySettings
    approval: ApprovalSettings = ApprovalSettings()
    azure: AzureSettings = AzureSettings()

    @model_validator(mode="after")
    def _one_llm_provider(self) -> "Settings":
        enabled = [name for name, p in (("ollama", self.ollama), ("azure_foundry", self.azure_foundry)) if p.enabled]
        if len(enabled) != 1:
            raise ValueError(
                "exactly one LLM provider must be enabled (set `enabled: true` on either `ollama` or "
                f"`azure_foundry` in config.yaml); currently enabled: {enabled or 'none'}"
            )
        return self

    @property
    def llm_provider(self) -> str:
        return "ollama" if self.ollama.enabled else "azure_foundry"

    @property
    def llm(self) -> OllamaSettings | FoundrySettings:
        return self.ollama if self.ollama.enabled else self.azure_foundry

    def resolve(self, relative: str) -> Path:
        p = Path(relative)
        return p if p.is_absolute() else (PROJECT_ROOT / p)

    @property
    def generated_dir(self) -> Path:
        return self.resolve(self.paths.generated_dir)

    @property
    def catalog_path(self) -> Path:
        return self.resolve(self.modules.catalog_file)

    @property
    def modules_root(self) -> Path:
        return self.resolve(self.modules.root)

    @property
    def plugin_cache_dir(self) -> Path:
        return self.resolve(self.paths.plugin_cache_dir)


def load_settings(path: Path | str | None = None) -> Settings:
    cfg_path = Path(path) if path else Path(os.environ.get("INFRA_AGENT_CONFIG", DEFAULT_CONFIG_PATH))
    with open(cfg_path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    settings = Settings.model_validate(raw)
    env_url = os.environ.get("OLLAMA_BASE_URL")
    if env_url:
        settings.ollama.base_url = env_url
    env_foundry = os.environ.get("AZURE_FOUNDRY_ENDPOINT")
    if env_foundry:
        settings.azure_foundry.endpoint = env_foundry
    env_modules = os.environ.get("INFRA_AGENT_MODULES_ROOT")
    if env_modules:
        settings.modules.root = env_modules
    return settings
