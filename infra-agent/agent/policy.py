"""Deterministic policy engine.

Three checkpoints:
* ``check_requirements`` - after the parser LLM, before generation.
* ``check_config``       - after generation/repair, before files are written.
* ``check_plan``         - on ``terraform show -json tfplan`` before approval.
"""

from __future__ import annotations

import fnmatch
import re
from typing import Any

from .builder import mandatory_tags
from .models import GeneratedConfig, InfrastructureRequest, ParsedRequirements, ValidationIssue
from .module_catalog import Catalog, is_reference
from .settings import PolicySettings


def module_allowed(key: str, policy: PolicySettings) -> bool:
    if any(fnmatch.fnmatch(key, pat) for pat in policy.denied_modules):
        return False
    return any(fnmatch.fnmatch(key, pat) for pat in policy.allowed_modules)


def allowed_module_keys(catalog: Catalog, policy: PolicySettings) -> list[str]:
    return [k for k in catalog.enabled_keys() if module_allowed(k, policy)]


def check_requirements(parsed: ParsedRequirements, catalog: Catalog, policy: PolicySettings) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for comp in parsed.components:
        if catalog.get(comp.module) is None:
            issues.append(ValidationIssue(source="catalog", message=f"unknown module '{comp.module}'"))
        elif not catalog.modules[comp.module].enabled or not module_allowed(comp.module, policy):
            issues.append(ValidationIssue(source="policy", message=f"module '{comp.module}' is not allowed by policy"))
    return issues


def _walk(value: Any, path: str = ""):
    if isinstance(value, dict):
        for k, v in value.items():
            p = f"{path}.{k}" if path else str(k)
            yield p, str(k), v
            yield from _walk(v, p)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _walk(v, f"{path}[{i}]")


def check_config(
    config: GeneratedConfig,
    catalog: Catalog,
    request: InfrastructureRequest,
    request_id: str,
    policy: PolicySettings,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if len(config.module_calls) > policy.max_module_calls:
        issues.append(ValidationIssue(source="policy",
                                      message=f"{len(config.module_calls)} module calls exceed the limit of {policy.max_module_calls}"))
    sensitive = [re.compile(p, re.I) for p in policy.sensitive_input_patterns]
    required_tags = set(policy.required_tags) | set(mandatory_tags(request, request_id))

    for call in config.module_calls:
        if not module_allowed(call.module, policy):
            issues.append(ValidationIssue(source="policy", module_call=call.name,
                                          message=f"module '{call.module}' is not allowed by policy"))
        for path, key, value in _walk(call.inputs):
            if key == "location" and isinstance(value, str) and not is_reference(value):
                if value.strip().lower() not in policy.allowed_locations:
                    issues.append(ValidationIssue(
                        source="policy", module_call=call.name, path=path,
                        message=f"location '{value}' is not allowed; allowed: {policy.allowed_locations}"))
            if any(p.search(key) for p in sensitive) and not isinstance(value, (dict, list)):
                if value not in (None, "", False) and not (isinstance(value, str) and is_reference(value)):
                    issues.append(ValidationIssue(
                        source="policy", module_call=call.name, path=path,
                        message="secrets must not be generated literally; use a ${var.<name>} operator variable "
                                "or a reference to another module output"))
            if key in policy.forbidden_input_values and value in policy.forbidden_input_values[key]:
                issues.append(ValidationIssue(source="policy", module_call=call.name, path=path,
                                              message=f"value {value!r} is forbidden for '{key}' by policy"))
            if key == "tags" and isinstance(value, dict):
                missing = sorted(required_tags - set(value))
                if missing:
                    issues.append(ValidationIssue(source="policy", module_call=call.name, path=path,
                                                  message=f"missing required tags {missing}"))
    return issues


# --------------------------------------------------------------------------- #
# Plan policy
# --------------------------------------------------------------------------- #
def summarize_plan(plan: dict) -> dict:
    counts = {"create": 0, "update": 0, "delete": 0, "replace": 0, "read": 0, "no-op": 0}
    changes = []
    for rc in plan.get("resource_changes", []) or []:
        actions = rc.get("change", {}).get("actions", [])
        if actions in (["delete", "create"], ["create", "delete"]):
            kind = "replace"
        elif actions:
            kind = actions[0]
        else:
            kind = "no-op"
        counts[kind] = counts.get(kind, 0) + 1
        if kind not in ("no-op", "read"):
            changes.append({"address": rc.get("address"), "type": rc.get("type"), "action": kind})
    return {"counts": counts, "changes": changes}


def check_plan(plan: dict, policy: PolicySettings) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    changes = [rc for rc in plan.get("resource_changes", []) or []
               if rc.get("change", {}).get("actions") not in (["no-op"], ["read"])]
    if len(changes) > policy.max_resource_changes:
        issues.append(ValidationIssue(source="plan_policy",
                                      message=f"{len(changes)} resource changes exceed the limit of {policy.max_resource_changes}"))
    for rc in changes:
        address = rc.get("address", "?")
        actions = rc.get("change", {}).get("actions", [])
        provider = rc.get("provider_name", "")
        rtype = rc.get("type", "")
        if provider not in policy.allowed_providers:
            issues.append(ValidationIssue(source="plan_policy", path=address,
                                          message=f"provider '{provider}' is not allowed"))
        if any(fnmatch.fnmatch(rtype, pat) for pat in policy.denied_resource_types):
            issues.append(ValidationIssue(source="plan_policy", path=address,
                                          message=f"resource type '{rtype}' is denied by policy"))
        if "delete" in actions and "create" in actions and not policy.allow_replace:
            issues.append(ValidationIssue(source="plan_policy", path=address,
                                          message="resource replacement is not allowed by policy"))
        elif "delete" in actions and not policy.allow_destroy:
            issues.append(ValidationIssue(source="plan_policy", path=address,
                                          message="resource deletion is not allowed by policy"))
        after = rc.get("change", {}).get("after") or {}
        loc = after.get("location") if isinstance(after, dict) else None
        if isinstance(loc, str) and loc.replace(" ", "").lower() not in policy.allowed_locations:
            issues.append(ValidationIssue(source="plan_policy", path=address,
                                          message=f"location '{loc}' is not allowed"))
        if isinstance(after, dict) and "tags" in after and isinstance(after["tags"], dict):
            missing = sorted(set(policy.required_tags) - set(after["tags"]))
            if missing:
                issues.append(ValidationIssue(source="plan_policy", path=address,
                                              message=f"missing required tags {missing}"))
    return issues
