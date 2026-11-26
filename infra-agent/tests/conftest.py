"""Shared fixtures: a generic fake module tree, settings, catalog, fake LLM and executor."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.executor import StepOutcome  # noqa: E402
from agent.models import InfrastructureRequest  # noqa: E402
from agent.module_catalog import build_catalog  # noqa: E402
from agent.settings import Settings  # noqa: E402

GROUP_MODULE = {
    "variables.tf": '''
variable "groups" {
  description = <<-EOT
  Map of groups to create.
  EOT
  type = map(object({
    name     = string
    location = string
    tags     = optional(map(string), {})
  }))
  validation {
    condition     = length(var.groups) > 0
    error_message = "At least one group is required."
  }
}
''',
    "outputs.tf": '''
output "group_names" {
  description = "Names keyed by input key."
  value       = { for k, v in var.groups : k => v.name }
}
output "group_ids" {
  description = "IDs keyed by input key."
  value       = { for k, v in var.groups : k => v.name }
}
''',
    "main.tf": 'resource "azurerm_resource_group" "this" {\n  for_each = var.groups\n  name = each.value.name\n  location = each.value.location\n}\n',
}

WIDGET_MODULE = {
    "variables.tf": '''
variable "widgets" {
  description = "Map of widgets."
  type = map(object({
    name       = string
    group_name = string
    location   = string
    size       = optional(number, 1)
    enabled    = optional(bool)
    admin_password = optional(string)
    rules = optional(list(object({
      name = string
      port = number
    })), [])
  }))
}
variable "default_tags" {
  type    = map(string)
  default = {}
}
''',
    "outputs.tf": '''
output "widget_ids" {
  description = "Widget IDs keyed by input key."
  value       = { for k, v in var.widgets : k => v.name }
}
output "secret_value" {
  value     = "x"
  sensitive = true
}
''',
    "main.tf": 'resource "azurerm_widget" "this" {\n  for_each = var.widgets\n}\n',
}


@pytest.fixture
def modules_root(tmp_path: Path) -> Path:
    root = tmp_path / "modules"
    for name, files in {"group": GROUP_MODULE, "widget": WIDGET_MODULE}.items():
        d = root / name
        d.mkdir(parents=True)
        for fname, content in files.items():
            (d / fname).write_text(content, encoding="utf-8")
    return root


@pytest.fixture
def settings(tmp_path: Path, modules_root: Path) -> Settings:
    return Settings.model_validate({
        "ollama": {"base_url": "http://127.0.0.1:1"},
        "modules": {"root": str(modules_root), "catalog_file": str(tmp_path / "catalog.yaml")},
        "paths": {"generated_dir": str(tmp_path / "generated"), "plugin_cache_dir": str(tmp_path / "cache")},
        "static_checks": {"tflint": {"enabled": False}, "security": {"tool": "none"}},
        "policy": {"allowed_locations": ["eastus", "westeurope"]},
    })


@pytest.fixture
def catalog(modules_root: Path):
    cat, warnings = build_catalog(modules_root)
    assert not warnings
    return cat


@pytest.fixture
def request_model() -> InfrastructureRequest:
    return InfrastructureRequest.model_validate({
        "description": "Two widgets in one group, each with a single rule on port 443.",
        "environment": "dev",
        "location": "eastus",
        "naming_prefix": "demo",
        "requested_by": "Test User",
        "subscription_id": "11111111-1111-1111-1111-111111111111",
        "tags": {"owner": "team"},
    })


def good_config() -> dict:
    return {
        "summary": "groups and widgets",
        "module_calls": [
            {"name": "groups", "module": "group", "inputs": {"groups": {"main": {"name": "grp-demo-dev"}}}},
            {"name": "widgets", "module": "widget", "inputs": {"widgets": {
                "w1": {"name": "wdg-demo-1", "group_name": '${module.groups.group_names["main"]}',
                       "rules": [{"name": "https", "port": 443}]},
            }}},
        ],
        "outputs": [{"name": "widget_id", "value": '${module.widgets.widget_ids["w1"]}', "description": "id"}],
    }


class FakeLLM:
    """Returns scripted responses per role, in order."""

    def __init__(self, script: dict[str, list]):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls: list[tuple[str, str]] = []

    def chat_json(self, role, system, user, response_model, recorder=None):
        self.calls.append((role, user))
        if not self.script.get(role):
            raise AssertionError(f"unexpected LLM call for role {role}")
        item = self.script[role].pop(0)
        result = response_model.model_validate(item)
        if recorder:
            recorder(role, {"role": role, "valid": True, "response": json.dumps(item)})
        return result


class FakeExecutor:
    """Stand-in for TerraformExecutor. ``validate_outcomes`` / ``apply_outcomes`` are consumed in order."""

    def __init__(self, store, validate_outcomes=None, plan_doc=None, apply_outcomes=None):
        self.store = store
        self.validate_outcomes = list(validate_outcomes or [])
        self.apply_outcomes = list(apply_outcomes or [])
        self.plan_doc = plan_doc or {"resource_changes": [{
            "address": 'module.groups.azurerm_resource_group.this["main"]',
            "type": "azurerm_resource_group",
            "provider_name": "registry.terraform.io/hashicorp/azurerm",
            "change": {"actions": ["create"], "after": {
                "location": "eastus", "tags": {"environment": "dev", "managed_by": "infra-agent", "request_id": "x"}}},
        }]}
        self.counts = {"validate": 0, "plan": 0, "apply": 0}

    def fmt(self):
        return StepOutcome(True, "fmt")

    def init(self):
        return StepOutcome(True, "init")

    def validate(self):
        self.counts["validate"] += 1
        return self.validate_outcomes.pop(0) if self.validate_outcomes else StepOutcome(True, "validate")

    def lint(self):
        return StepOutcome(True, "lint")

    def security_scan(self):
        return StepOutcome(True, "security")

    def plan(self):
        self.counts["plan"] += 1
        (self.store.dir / "tfplan").write_bytes(f"plan-{self.counts['plan']}".encode())
        self.store.write("plan.json", self.plan_doc)
        return StepOutcome(True, "plan")

    def apply(self):
        self.counts["apply"] += 1
        return self.apply_outcomes.pop(0) if self.apply_outcomes else StepOutcome(True, "apply")

    def outputs(self):
        return {"widget_id": "abc"}
