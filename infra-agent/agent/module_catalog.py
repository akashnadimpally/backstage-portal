"""Module catalog: discovery, persistence, type parsing and deterministic input checks.

The catalog is generated from the Terraform modules on disk (``catalog build``)
so no resource types are hard-coded in the agent. Operators may edit
``enabled`` / ``notes`` in catalog.yaml; those fields survive a rebuild.
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

import hcl2
import yaml
from pydantic import BaseModel, ConfigDict, Field

from .models import MODULE_REF_RE, VAR_REF_RE


# --------------------------------------------------------------------------- #
# Terraform type expressions
# --------------------------------------------------------------------------- #
PRIMITIVES = {"string", "number", "bool", "any"}


@dataclass
class TypeNode:
    kind: str
    elem: "TypeNode | None" = None
    attrs: dict[str, "AttrSpec"] = field(default_factory=dict)
    elems: list["TypeNode"] = field(default_factory=list)

    def render(self, indent: int = 0) -> str:
        pad = "  " * indent
        if self.kind in PRIMITIVES:
            return self.kind
        if self.kind in ("list", "set", "map"):
            return f"{self.kind}({self.elem.render(indent) if self.elem else 'any'})"
        if self.kind == "tuple":
            return "tuple([" + ", ".join(e.render(indent) for e in self.elems) + "])"
        if self.kind == "object":
            lines = ["{"]
            for name, spec in self.attrs.items():
                marker = "" if spec.optional else "  # REQUIRED"
                lines.append(f"{pad}  {name}{'?' if spec.optional else ''}: {spec.type.render(indent + 1)}{marker}")
            lines.append(pad + "}")
            return "\n".join(lines)
        return self.kind


@dataclass
class AttrSpec:
    type: TypeNode
    optional: bool


_TOKEN_RE = re.compile(
    r'\s*(?:(?P<str>"(?:\\.|[^"\\])*")|(?P<num>-?\d+(?:\.\d+)?)|(?P<ident>[A-Za-z_][A-Za-z0-9_\-]*)|(?P<punct>[(){}\[\]=,:]))'
)


class TypeParseError(ValueError):
    pass


class _TypeParser:
    def __init__(self, text: str):
        self.tokens: list[str] = []
        pos = 0
        text = text.strip()
        while pos < len(text):
            if text[pos].isspace():
                pos += 1
                continue
            m = _TOKEN_RE.match(text, pos)
            if not m or m.end() == pos:
                raise TypeParseError(f"unexpected character {text[pos]!r} at {pos}")
            self.tokens.append(m.group(m.lastgroup).strip())
            pos = m.end()
        self.i = 0

    def peek(self, offset: int = 0) -> str | None:
        j = self.i + offset
        return self.tokens[j] if j < len(self.tokens) else None

    def take(self, expected: str | None = None) -> str:
        tok = self.peek()
        if tok is None or (expected is not None and tok != expected):
            raise TypeParseError(f"expected {expected!r}, got {tok!r}")
        self.i += 1
        return tok

    def parse(self) -> TypeNode:
        node = self.parse_type()
        if self.peek() is not None:
            raise TypeParseError(f"trailing tokens: {self.tokens[self.i:]}")
        return node

    def parse_type(self) -> TypeNode:
        ident = self.take()
        if ident in PRIMITIVES:
            return TypeNode(ident)
        self.take("(")
        if ident in ("list", "set", "map"):
            node = TypeNode(ident, elem=self.parse_type())
        elif ident == "object":
            self.take("{")
            attrs: dict[str, AttrSpec] = {}
            while self.peek() != "}":
                name = self.take()
                if self.peek() in ("=", ":"):
                    self.take()
                attrs[name] = self.parse_attr()
                if self.peek() == ",":
                    self.take()
            self.take("}")
            node = TypeNode("object", attrs=attrs)
        elif ident == "tuple":
            self.take("[")
            elems = []
            while self.peek() != "]":
                elems.append(self.parse_type())
                if self.peek() == ",":
                    self.take()
            self.take("]")
            node = TypeNode("tuple", elems=elems)
        else:
            raise TypeParseError(f"unknown type constructor {ident!r}")
        self.take(")")
        return node

    def parse_attr(self) -> AttrSpec:
        if self.peek() == "optional" and self.peek(1) == "(":
            self.take()
            self.take("(")
            inner = self.parse_type()
            if self.peek() == ",":
                self.take()
                self._skip_default()
            self.take(")")
            return AttrSpec(inner, optional=True)
        return AttrSpec(self.parse_type(), optional=False)

    def _skip_default(self) -> None:
        depth = 0
        while True:
            tok = self.peek()
            if tok is None:
                raise TypeParseError("unterminated optional() default")
            if tok in "({[" and len(tok) == 1:
                depth += 1
            elif tok in ")}]" and len(tok) == 1:
                if depth == 0:
                    return
                depth -= 1
            self.i += 1


def parse_type(text: str | None) -> TypeNode:
    if not text:
        return TypeNode("any")
    try:
        return _TypeParser(text).parse()
    except TypeParseError:
        return TypeNode("any")


def is_reference(value: Any) -> bool:
    return isinstance(value, str) and bool(MODULE_REF_RE.fullmatch(value) or VAR_REF_RE.fullmatch(value))


def check_value(value: Any, node: TypeNode, path: str) -> list[tuple[str, str]]:
    """Return (path, message) problems for ``value`` against a Terraform type."""
    if is_reference(value) or node.kind == "any":
        return []
    if value is None:
        return []
    kind = node.kind
    if kind == "string":
        if isinstance(value, (dict, list)):
            return [(path, f"expected string, got {type(value).__name__}")]
        return []
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            return [(path, "expected number")]
        if isinstance(value, str):
            try:
                float(value)
            except ValueError:
                return [(path, f"expected number, got {value!r}")]
        return []
    if kind == "bool":
        if isinstance(value, bool) or (isinstance(value, str) and value in ("true", "false")):
            return []
        return [(path, f"expected bool, got {value!r}")]
    if kind in ("list", "set", "tuple"):
        if not isinstance(value, list):
            return [(path, f"expected {kind}, got {type(value).__name__}")]
        problems: list[tuple[str, str]] = []
        for idx, item in enumerate(value):
            elem = node.elem if kind != "tuple" else (node.elems[idx] if idx < len(node.elems) else TypeNode("any"))
            problems += check_value(item, elem or TypeNode("any"), f"{path}[{idx}]")
        return problems
    if kind == "map":
        if not isinstance(value, dict):
            return [(path, f"expected map, got {type(value).__name__}")]
        problems = []
        for key, item in value.items():
            problems += check_value(item, node.elem or TypeNode("any"), f'{path}["{key}"]')
        return problems
    if kind == "object":
        if not isinstance(value, dict):
            return [(path, f"expected object, got {type(value).__name__}")]
        problems = []
        unknown = sorted(set(value) - set(node.attrs))
        if unknown:
            problems.append((path, f"unknown attribute(s) {unknown}; allowed: {sorted(node.attrs)}"))
        for name, spec in node.attrs.items():
            if name not in value or value[name] is None:
                if not spec.optional:
                    problems.append((f"{path}.{name}", "required attribute is missing"))
                continue
            problems += check_value(value[name], spec.type, f"{path}.{name}")
        return problems
    return []


# --------------------------------------------------------------------------- #
# Catalog models
# --------------------------------------------------------------------------- #
class VariableSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str = "any"
    required: bool = True
    sensitive: bool = False
    description: str = ""
    constraints: list[str] = Field(default_factory=list)

    @cached_property
    def type_node(self) -> TypeNode:
        return parse_type(self.type)


class OutputSpecCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = ""
    sensitive: bool = False


class ModuleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    enabled: bool = True
    notes: str = ""
    description: str = ""
    resource_types: list[str] = Field(default_factory=list)
    has_provider_block: bool = False
    variables: dict[str, VariableSpec] = Field(default_factory=dict)
    outputs: dict[str, OutputSpecCatalog] = Field(default_factory=dict)

    @property
    def required_variables(self) -> list[str]:
        return [n for n, v in self.variables.items() if v.required]


class Catalog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    modules_root: str
    modules: dict[str, ModuleSpec]

    def get(self, key: str) -> ModuleSpec | None:
        return self.modules.get(key)

    def enabled_keys(self) -> list[str]:
        return sorted(k for k, m in self.modules.items() if m.enabled)

    def module_dir(self, key: str) -> Path:
        return Path(self.modules_root) / self.modules[key].path

    # ---------------------------- prompt rendering -------------------------- #
    def render_index(self, keys: list[str]) -> str:
        """Compact list used by the requirement parser to pick modules."""
        lines = []
        for key in keys:
            m = self.modules[key]
            res = ", ".join(m.resource_types[:6]) or "n/a"
            lines.append(f"- {key}: {m.description or 'module'} | creates: {res} | inputs: {', '.join(m.variables)}")
        return "\n".join(lines)

    def render_spec(self, key: str, max_constraints: int = 8) -> str:
        """Full spec for a single module, used by generator / repair prompts."""
        m = self.modules[key]
        out = [f"### module `{key}`", f"Purpose: {m.description or 'n/a'}", f"Creates: {', '.join(m.resource_types)}"]
        if m.notes:
            out.append(f"Notes: {m.notes}")
        out.append("Inputs:")
        for name, var in m.variables.items():
            status = "REQUIRED" if var.required else "optional"
            out.append(f"- `{name}` ({status}{', sensitive' if var.sensitive else ''}): {var.type_node.render(1)}")
            for c in var.constraints[:max_constraints]:
                out.append(f"    constraint: {c}")
        out.append("Outputs (reference as ${module.<call_name>.<output>}):")
        for name, o in m.outputs.items():
            out.append(f"- `{name}`: {o.description}")
        return "\n".join(out)


# --------------------------------------------------------------------------- #
# Discovery from disk
# --------------------------------------------------------------------------- #
def _unquote(value: Any) -> Any:
    if isinstance(value, str):
        v = value
        if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
            v = v[1:-1].replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
        if v.startswith("${") and v.endswith("}"):
            v = v[2:-1]
        return v
    return value


def _clean_text(value: Any) -> str:
    text = str(_unquote(value) or "")
    m = re.match(r"^<<-?(\w+)\n(.*)\n\s*\1\s*$", text, flags=re.S)
    if m:
        text = textwrap.dedent(m.group(2))
    return text.strip()


def _as_bool(value: Any) -> bool:
    return str(_unquote(value)).strip().lower() == "true"


def _load_blocks(files: list[Path], block: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for f in files:
        with open(f, encoding="utf-8") as fh:
            data = hcl2.load(fh)
        for entry in data.get(block, []):
            for raw_name, body in entry.items():
                result[str(_unquote(raw_name))] = body
    return result


def discover_module(module_dir: Path, rel_path: str) -> ModuleSpec:
    tf_files = sorted(module_dir.glob("*.tf"))
    variables_raw = _load_blocks(tf_files, "variable")
    outputs_raw = _load_blocks(tf_files, "output")

    variables: dict[str, VariableSpec] = {}
    for name, body in variables_raw.items():
        constraints = [_clean_text(v.get("error_message", "")) for v in body.get("validation", []) or []]
        variables[name] = VariableSpec(
            type=str(_unquote(body.get("type", "any"))),
            required="default" not in body,
            sensitive=_as_bool(body.get("sensitive", False)),
            description=_clean_text(body.get("description", ""))[:1500],
            constraints=[c for c in constraints if c],
        )
    outputs = {
        name: OutputSpecCatalog(
            description=_clean_text(body.get("description", ""))[:400],
            sensitive=_as_bool(body.get("sensitive", False)),
        )
        for name, body in outputs_raw.items()
    }

    resource_types: list[str] = []
    has_provider = False
    for f in tf_files:
        text = f.read_text(encoding="utf-8")
        resource_types += re.findall(r'^\s*resource\s+"([^"]+)"', text, flags=re.M)
        has_provider = has_provider or bool(re.search(r'^\s*provider\s+"', text, flags=re.M))
    resource_types = sorted(set(resource_types))

    primary = next((v for v in variables.values() if v.required), None)
    description = ""
    if primary and primary.description:
        description = primary.description.split("\n\n")[0].replace("\n", " ")[:300]
    if not description and resource_types:
        description = f"Manages {', '.join(resource_types[:3])}"

    return ModuleSpec(
        path=rel_path,
        description=description,
        resource_types=resource_types,
        has_provider_block=has_provider,
        variables=variables,
        outputs=outputs,
    )


def build_catalog(modules_root: Path, existing: Catalog | None = None) -> tuple[Catalog, list[str]]:
    """Scan ``modules_root`` (one module per sub-directory) and return a catalog plus warnings."""
    warnings: list[str] = []
    modules: dict[str, ModuleSpec] = {}
    for module_dir in sorted(p for p in modules_root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        if not list(module_dir.glob("*.tf")):
            continue
        key = module_dir.name
        try:
            spec = discover_module(module_dir, key)
        except Exception as exc:  # noqa: BLE001 - one broken module must not block the catalog
            warnings.append(f"{key}: failed to parse ({exc})")
            continue
        for vname, var in spec.variables.items():
            if var.type != "any" and var.type_node.kind == "any":
                warnings.append(f"{key}.{vname}: type expression could not be parsed; type checks skipped")
        if existing and key in existing.modules:
            spec.enabled = existing.modules[key].enabled
            spec.notes = existing.modules[key].notes
        modules[key] = spec
    return Catalog(modules_root=str(modules_root), modules=modules), warnings


def save_catalog(catalog: Catalog, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Generated by `python -m agent.main catalog build`.\n"
        "# You may edit `enabled` and `notes`; they are preserved on rebuild.\n"
    )
    body = yaml.safe_dump(catalog.model_dump(mode="json"), sort_keys=False, width=200, allow_unicode=True)
    path.write_text(header + body, encoding="utf-8")


def load_catalog(path: Path) -> Catalog:
    if not path.exists():
        raise FileNotFoundError(f"Module catalog not found at {path}. Run: python -m agent.main catalog build")
    with open(path, encoding="utf-8") as fh:
        return Catalog.model_validate(yaml.safe_load(fh))
