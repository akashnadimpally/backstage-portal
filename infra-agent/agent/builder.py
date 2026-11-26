"""Deterministic file builder.

LLMs never write HCL. They return a ``GeneratedConfig`` (JSON), which this
module validates against the catalog and renders into Terraform files.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import (
    MODULE_REF_RE,
    PATH_SEGMENT_RE,
    VAR_REF_RE,
    GeneratedConfig,
    InfrastructureRequest,
    ValidationIssue,
)
from .module_catalog import Catalog, TypeNode, check_value, is_reference
from .settings import Settings

VAR_SEPARATOR = "__"
RESERVED_VARS = {"subscription_id"}
OUTPUTS_TARGET = "__outputs__"


class BuildError(RuntimeError):
    pass


@dataclass
class BuildResult:
    files: dict[str, str]
    line_map: dict[str, tuple[int, int]]
    tfvars: dict[str, Any]
    operator_variables: list[str] = field(default_factory=list)

    def target_for_line(self, filename: str | None, line: int | None) -> str | None:
        if not filename or line is None:
            return None
        name = Path(filename).name
        if name == "outputs.tf":
            return OUTPUTS_TARGET
        if name != "main.tf":
            return None
        for call, (start, end) in self.line_map.items():
            if start <= line <= end:
                return call
        return None


# --------------------------------------------------------------------------- #
# Reference helpers
# --------------------------------------------------------------------------- #
def iter_strings(value: Any, path: str = ""):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from iter_strings(v, f'{path}["{k}"]' if path else str(k))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from iter_strings(v, f"{path}[{i}]")


def contains_reference(value: Any) -> bool:
    return any(is_reference(s) for _, s in iter_strings(value))


def operator_variables(config: GeneratedConfig) -> list[str]:
    names = set()
    for call in config.module_calls:
        for _, s in iter_strings(call.inputs):
            m = VAR_REF_RE.fullmatch(s)
            if m:
                names.add(m.group("name"))
    return sorted(names)


# --------------------------------------------------------------------------- #
# Deterministic normalisation (location + tags)
# --------------------------------------------------------------------------- #
def mandatory_tags(request: InfrastructureRequest, request_id: str) -> dict[str, str]:
    return {
        "environment": request.environment.value,
        "managed_by": "infra-agent",
        "request_id": request_id,
    }


def _item_type(node: TypeNode) -> TypeNode | None:
    if node.kind in ("map", "list", "set") and node.elem and node.elem.kind == "object":
        return node.elem
    if node.kind == "object":
        return node
    return None


def _items(value: Any) -> list[dict]:
    if isinstance(value, dict):
        return [v for v in value.values() if isinstance(v, dict)]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return []


def normalize_config(
    config: GeneratedConfig, catalog: Catalog, request: InfrastructureRequest, request_id: str
) -> GeneratedConfig:
    """Inject request location and mandatory tags wherever the module schema has them."""
    config = config.model_copy(deep=True)
    base_tags = {**request.tags}
    forced = mandatory_tags(request, request_id)
    for call in config.module_calls:
        spec = catalog.get(call.module)
        if not spec:
            continue
        for var_name, var in spec.variables.items():
            node = var.type_node
            if var_name in ("tags", "default_tags") and node.kind == "map":
                current = call.inputs.get(var_name)
                if current is None or isinstance(current, dict):
                    call.inputs[var_name] = {**base_tags, **(current or {}), **forced}
                continue
            if var_name == "location" and node.kind == "string" and var.required and not call.inputs.get(var_name):
                call.inputs[var_name] = request.location
                continue
            item_type = _item_type(node)
            if item_type is None or var_name not in call.inputs:
                continue
            target = call.inputs[var_name]
            items = [target] if node.kind == "object" and isinstance(target, dict) else _items(target)
            for item in items:
                loc = item_type.attrs.get("location")
                if loc and not loc.optional and not item.get("location"):
                    item["location"] = request.location
                tags = item_type.attrs.get("tags")
                if tags and tags.type.kind == "map":
                    existing = item.get("tags")
                    if existing is None or isinstance(existing, dict):
                        item["tags"] = {**base_tags, **(existing or {}), **forced}
    return config


# --------------------------------------------------------------------------- #
# Deterministic validation against the catalog
# --------------------------------------------------------------------------- #
def validate_config(config: GeneratedConfig, catalog: Catalog) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    calls = {c.name: c for c in config.module_calls}

    for call in config.module_calls:
        spec = catalog.get(call.module)
        if spec is None:
            issues.append(ValidationIssue(source="catalog", module_call=call.name,
                                          message=f"module '{call.module}' does not exist in the catalog"))
            continue
        if not spec.enabled:
            issues.append(ValidationIssue(source="catalog", module_call=call.name,
                                          message=f"module '{call.module}' is disabled in the catalog"))
        unknown = sorted(set(call.inputs) - set(spec.variables))
        if unknown:
            issues.append(ValidationIssue(
                source="catalog", module_call=call.name,
                message=f"unknown input(s) {unknown}; module '{call.module}' accepts {sorted(spec.variables)}"))
        for req in spec.required_variables:
            if call.inputs.get(req) is None:
                issues.append(ValidationIssue(source="catalog", module_call=call.name, path=req,
                                              message="required module input is missing"))
        for name, value in call.inputs.items():
            var = spec.variables.get(name)
            if var is None:
                continue
            for path, msg in check_value(value, var.type_node, name):
                issues.append(ValidationIssue(source="catalog", module_call=call.name, path=path, message=msg))
            if isinstance(value, (dict, list)) and not value and var.required:
                issues.append(ValidationIssue(source="catalog", module_call=call.name, path=name,
                                              message="required input must not be empty"))

    # references
    edges: dict[str, set[str]] = {c.name: set() for c in config.module_calls}
    sources = [(c.name, c.inputs) for c in config.module_calls]
    sources.append((OUTPUTS_TARGET, {o.name: o.value for o in config.outputs}))
    for owner, payload in sources:
        for path, s in iter_strings(payload):
            if "${" not in s and "%{" not in s:
                continue
            var_m = VAR_REF_RE.fullmatch(s)
            if var_m:
                vname = var_m.group("name")
                if owner == OUTPUTS_TARGET or VAR_SEPARATOR in vname or vname in RESERVED_VARS:
                    issues.append(ValidationIssue(source="reference", module_call=owner, path=path,
                                                  message=f"variable reference '{s}' is not allowed here"))
                continue
            m = MODULE_REF_RE.fullmatch(s)
            if not m:
                issues.append(ValidationIssue(
                    source="reference", module_call=owner, path=path,
                    message=f"invalid expression {s!r}; only ${{module.<call>.<output>[\"key\"]}} references are allowed"))
                continue
            target = m.group("call")
            output = m.group("output")
            if target == owner:
                issues.append(ValidationIssue(source="reference", module_call=owner, path=path,
                                              message="a module call cannot reference itself"))
                continue
            target_call = calls.get(target)
            if target_call is None:
                issues.append(ValidationIssue(source="reference", module_call=owner, path=path,
                                              message=f"reference to unknown module call '{target}'; known: {sorted(calls)}"))
                continue
            target_spec = catalog.get(target_call.module)
            if target_spec and output not in target_spec.outputs:
                issues.append(ValidationIssue(
                    source="reference", module_call=owner, path=path,
                    message=f"module '{target_call.module}' has no output '{output}'; available: {sorted(target_spec.outputs)}"))
                continue
            first = PATH_SEGMENT_RE.match(m.group("path") or "")
            if first and first.group("key") and target_spec:
                keys = map_keys(target_call.inputs, target_spec)
                if keys and first.group("key") not in keys:
                    issues.append(ValidationIssue(
                        source="reference", module_call=owner, path=path,
                        message=f"key '{first.group('key')}' is not defined in module call '{target}'; known keys: {sorted(keys)}"))
            if owner in edges:
                edges[owner].add(target)

    cycle = _find_cycle(edges)
    if cycle:
        issues.append(ValidationIssue(source="reference", message=f"dependency cycle: {' -> '.join(cycle)}"))
    return issues


def map_keys(inputs: dict[str, Any], spec) -> set[str]:
    keys: set[str] = set()
    for name, value in inputs.items():
        var = spec.variables.get(name)
        if var and var.type_node.kind == "map" and isinstance(value, dict) and var.type_node.elem \
                and var.type_node.elem.kind == "object":
            keys |= set(value)
    return keys


def _find_cycle(edges: dict[str, set[str]]) -> list[str] | None:
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        stack.append(node)
        for nxt in sorted(edges.get(node, ())):
            if state.get(nxt) == 1:
                return stack[stack.index(nxt):] + [nxt]
            if state.get(nxt) is None:
                found = visit(nxt)
                if found:
                    return found
        stack.pop()
        state[node] = 2
        return None

    for n in sorted(edges):
        if state.get(n) is None:
            found = visit(n)
            if found:
                return found
    return None


# --------------------------------------------------------------------------- #
# HCL rendering
# --------------------------------------------------------------------------- #
def _hcl_string(s: str) -> str:
    escaped = (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
        .replace("${", "$${")
        .replace("%{", "%%{")
    )
    return f'"{escaped}"'


def render_value(value: Any, indent: int = 0) -> str:
    pad = "  " * indent
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    if isinstance(value, str):
        if is_reference(value):
            return value[2:-1]
        return _hcl_string(value)
    if isinstance(value, list):
        if not value:
            return "[]"
        inner = ",\n".join(f"{pad}  {render_value(v, indent + 1)}" for v in value)
        return f"[\n{inner},\n{pad}]"
    if isinstance(value, dict):
        if not value:
            return "{}"
        inner = "\n".join(f"{pad}  {_hcl_string(str(k))} = {render_value(v, indent + 1)}" for k, v in value.items())
        return f"{{\n{inner}\n{pad}}}"
    raise BuildError(f"unsupported value type {type(value).__name__}")


def _module_source(module_dir: Path, out_dir: Path) -> str:
    try:
        rel = os.path.relpath(module_dir.resolve(), out_dir.resolve())
    except ValueError as exc:
        raise BuildError(
            f"module directory {module_dir} is on a different drive than {out_dir}; "
            "Terraform local module sources must be relative paths"
        ) from exc
    rel = rel.replace("\\", "/")
    return rel if rel.startswith("../") or rel.startswith("./") else f"./{rel}"


def build_files(
    config: GeneratedConfig,
    catalog: Catalog,
    request: InfrastructureRequest,
    request_id: str,
    settings: Settings,
    out_dir: Path,
    subscription_id: str | None,
) -> BuildResult:
    tf = settings.terraform
    header = f"# Generated by infra-agent for request {request_id}. Do not edit by hand.\n"

    providers_tf = header + (
        "terraform {\n"
        f"  required_version = {_hcl_string(tf.required_version)}\n"
        "  required_providers {\n"
        "    azurerm = {\n"
        '      source  = "hashicorp/azurerm"\n'
        f"      version = {_hcl_string(tf.azurerm_version)}\n"
        "    }\n"
        "  }\n"
        "}\n\n"
        'provider "azurerm" {\n'
        "  features {}\n"
        "  subscription_id = var.subscription_id\n"
        "}\n"
    )

    variables: list[str] = [
        'variable "subscription_id" {\n'
        '  description = "Target Azure subscription ID."\n'
        "  type        = string\n"
        "}\n"
    ]
    tfvars: dict[str, Any] = {"subscription_id": subscription_id or ""}

    main_lines: list[str] = header.rstrip("\n").split("\n") + [""]
    line_map: dict[str, tuple[int, int]] = {}
    for call in config.module_calls:
        if catalog.get(call.module) is None:
            raise BuildError(f"module '{call.module}' is not in the catalog")
        start = len(main_lines) + 1
        main_lines.append(f'module "{call.name}" {{')
        main_lines.append(f"  source = {_hcl_string(_module_source(catalog.module_dir(call.module), out_dir))}")
        if call.description:
            main_lines.append(f"  # {call.description.replace(chr(10), ' ')[:200]}")
        main_lines.append("")
        for name in sorted(call.inputs):
            value = call.inputs[name]
            if contains_reference(value):
                rendered = render_value(value, 1)
            else:
                var_name = f"{call.name}{VAR_SEPARATOR}{name}"
                variables.append(
                    f'variable "{var_name}" {{\n'
                    f"  description = {_hcl_string(f'Input {name} for module call {call.name} ({call.module}).')}\n"
                    "  type        = any\n"
                    "}\n"
                )
                tfvars[var_name] = value
                rendered = f"var.{var_name}"
            main_lines.extend(f"  {name} = {rendered}".split("\n"))
        main_lines.append("}")
        line_map[call.name] = (start, len(main_lines))
        main_lines.append("")

    op_vars = operator_variables(config)
    for name in op_vars:
        variables.append(
            f'variable "{name}" {{\n'
            '  description = "Operator-supplied sensitive value (set TF_VAR_' + name + ')."\n'
            "  type        = string\n"
            "  sensitive   = true\n"
            "}\n"
        )

    outputs: list[str] = [header]
    for out in config.outputs:
        m = MODULE_REF_RE.fullmatch(out.value)
        sensitive = False
        if m:
            target = config.call(m.group("call"))
            spec = catalog.get(target.module) if target else None
            sensitive = bool(spec and spec.outputs.get(m.group("output"), None) and spec.outputs[m.group("output")].sensitive)
        outputs.append(
            f'output "{out.name}" {{\n'
            f"  description = {_hcl_string(out.description)}\n"
            f"  value       = {render_value(out.value)}\n"
            + ("  sensitive   = true\n" if sensitive else "")
            + "}\n"
        )

    files = {
        "providers.tf": providers_tf,
        "main.tf": "\n".join(main_lines).rstrip("\n") + "\n",
        "variables.tf": header + "\n".join(variables),
        "outputs.tf": "\n".join(outputs),
        "terraform.tfvars.json": json.dumps(tfvars, indent=2, sort_keys=True) + "\n",
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (out_dir / name).write_text(content, encoding="utf-8", newline="\n")
    (out_dir / "build.json").write_text(json.dumps({
        "line_map": line_map,
        "operator_variables": op_vars,
        "sha256": {n: hashlib.sha256(c.encode()).hexdigest() for n, c in files.items()},
    }, indent=2), encoding="utf-8")
    return BuildResult(files=files, line_map=line_map, tfvars=copy.deepcopy(tfvars), operator_variables=op_vars)


def load_build_result(out_dir: Path) -> BuildResult:
    meta = json.loads((out_dir / "build.json").read_text(encoding="utf-8"))
    files = {n: (out_dir / n).read_text(encoding="utf-8") for n in meta["sha256"]}
    return BuildResult(
        files=files,
        line_map={k: tuple(v) for k, v in meta["line_map"].items()},
        tfvars=json.loads(files["terraform.tfvars.json"]),
        operator_variables=meta.get("operator_variables", []),
    )
