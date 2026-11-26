"""Pydantic contracts for every boundary in the pipeline.

* ``InfrastructureRequest`` is validated before any LLM is contacted.
* Every LLM response is parsed into one of the models below; anything that
  does not validate is rejected and never reaches the file builder.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

IDENT = r"[a-z][a-z0-9_]*"
CALL_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
REQUEST_ID_RE = re.compile(r"^\d{8}-\d{6}-[a-f0-9]{8}$")

# The ONLY expression syntax an LLM may emit inside a value. Everything else is a literal.
#   ${module.<call>.<output>}  optionally followed by ["key"] / .attr segments
#   ${var.<name>}              a sensitive root variable supplied by the operator (TF_VAR_*)
MODULE_REF_RE = re.compile(
    rf'^\$\{{module\.(?P<call>{IDENT})\.(?P<output>{IDENT})(?P<path>(?:\["[A-Za-z0-9_.\-]+"\]|\.{IDENT})*)\}}$'
)
VAR_REF_RE = re.compile(rf"^\$\{{var\.(?P<name>{IDENT})\}}$")
PATH_SEGMENT_RE = re.compile(rf'\["(?P<key>[A-Za-z0-9_.\-]+)"\]|\.(?P<attr>{IDENT})')

_PROMPT_INJECTION_MARKERS = (
    "ignore previous instructions",
    "ignore all previous",
    "disregard the above",
    "system prompt",
    "you are now",
)
_FORBIDDEN_TEXT_TOKENS = ("```", "${", "%{", "<<EOT", "<<-EOT")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --------------------------------------------------------------------------- #
# User input
# --------------------------------------------------------------------------- #
class Environment(str, Enum):
    dev = "dev"
    test = "test"
    stage = "stage"
    prod = "prod"


class InfrastructureRequest(Strict):
    """What the user asks for. Rejected deterministically before any LLM call."""

    description: str = Field(
        min_length=20,
        max_length=4000,
        description="Natural-language description of the desired Azure infrastructure.",
    )
    environment: Environment
    location: str = Field(description="Default Azure region, e.g. 'eastus'.")
    naming_prefix: str = Field(
        pattern=r"^[a-z][a-z0-9]{1,9}$",
        description="Lowercase alphanumeric prefix (2-10 chars) used in resource names.",
    )
    subscription_id: uuid.UUID | None = Field(
        default=None,
        description="Target subscription. Falls back to ARM_SUBSCRIPTION_ID when omitted.",
    )
    requested_by: str = Field(min_length=2, max_length=128, pattern=r"^[A-Za-z0-9 ._@\-]+$")
    tags: dict[str, str] = Field(default_factory=dict, max_length=15)

    @field_validator("description")
    @classmethod
    def _safe_description(cls, v: str) -> str:
        if _CONTROL_CHARS.search(v):
            raise ValueError("description contains control characters")
        lowered = v.lower()
        for token in _FORBIDDEN_TEXT_TOKENS:
            if token.lower() in lowered:
                raise ValueError(f"description must be plain text; found forbidden token {token!r}")
        for marker in _PROMPT_INJECTION_MARKERS:
            if marker in lowered:
                raise ValueError("description contains instruction-override text")
        return v

    @field_validator("location")
    @classmethod
    def _location(cls, v: str, info: ValidationInfo) -> str:
        v = v.strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9]{2,30}", v):
            raise ValueError("location must be an Azure region name such as 'eastus'")
        allowed = (info.context or {}).get("allowed_locations")
        if allowed is not None and v not in allowed:
            raise ValueError(f"location '{v}' is not allowed by policy; allowed: {sorted(allowed)}")
        return v

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: dict[str, str]) -> dict[str, str]:
        for key, value in v.items():
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.\-]{0,63}", key):
                raise ValueError(f"invalid tag key {key!r}")
            if len(value) > 256 or _CONTROL_CHARS.search(value) or "${" in value:
                raise ValueError(f"invalid tag value for {key!r}")
        return v


# --------------------------------------------------------------------------- #
# Agent 0: requirement parser output
# --------------------------------------------------------------------------- #
IMPLICIT_PREFIX = "implicit:"


class RequiredComponent(Strict):
    module: str = Field(description="Exact module key from the module catalog.")
    purpose: str = Field(min_length=3, max_length=500)
    details: str = Field(
        default="",
        max_length=2000,
        description="Every user-provided quantity, name, size, address range or relationship for this module.",
    )
    evidence: str = Field(
        min_length=3,
        max_length=1000,
        description="Exact words copied from the user's request that ask for this module, "
        "or 'implicit: <reason>' when another requested module cannot work without it.",
    )

    @property
    def implicit(self) -> bool:
        return self.evidence.strip().lower().startswith(IMPLICIT_PREFIX)


class UnsupportedItem(Strict):
    capability: str = Field(min_length=2, max_length=300)
    evidence: str = Field(min_length=3, max_length=1000, description="Exact words copied from the user's request.")


class ParsedRequirements(Strict):
    summary: str = Field(min_length=3, max_length=1000)
    components: list[RequiredComponent] = Field(min_length=1, max_length=40)
    assumptions: list[str] = Field(default_factory=list, max_length=30)
    unsupported: list[UnsupportedItem | str] = Field(
        default_factory=list,
        max_length=30,
        description="Capabilities the user explicitly asked for that no catalog module can satisfy.",
    )

    @property
    def explicit_components(self) -> list[RequiredComponent]:
        return [c for c in self.components if not c.implicit]


# --------------------------------------------------------------------------- #
# Agent 1: generator output
# --------------------------------------------------------------------------- #
class ModuleCall(Strict):
    name: str = Field(description="Terraform module block label, snake_case, unique.")
    module: str = Field(description="Exact module key from the module catalog.")
    description: str = Field(default="", max_length=500)
    inputs: dict[str, Any] = Field(description="Module variable name -> value.")

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not CALL_NAME_RE.fullmatch(v) or len(v) > 48:
            raise ValueError("module call name must be snake_case (no double underscores), max 48 chars")
        return v


class OutputSpec(Strict):
    name: str
    value: str = Field(description='Reference such as ${module.<call>.<output>["<key>"]}')
    description: str = Field(default="", max_length=300)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not CALL_NAME_RE.fullmatch(v):
            raise ValueError("output name must be snake_case")
        return v

    @field_validator("value")
    @classmethod
    def _value(cls, v: str) -> str:
        if not MODULE_REF_RE.fullmatch(v):
            raise ValueError("output value must be a ${module.<call>.<output>...} reference")
        return v


class GeneratedConfig(Strict):
    summary: str = Field(default="", max_length=2000)
    module_calls: list[ModuleCall] = Field(min_length=1, max_length=100)
    outputs: list[OutputSpec] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def _unique(self) -> "GeneratedConfig":
        names = [c.name for c in self.module_calls]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"duplicate module call names: {sorted(dupes)}")
        out = [o.name for o in self.outputs]
        dupes = {n for n in out if out.count(n) > 1}
        if dupes:
            raise ValueError(f"duplicate output names: {sorted(dupes)}")
        return self

    def call(self, name: str) -> ModuleCall | None:
        return next((c for c in self.module_calls if c.name == name), None)


# --------------------------------------------------------------------------- #
# Agent 2: reviewer output
# --------------------------------------------------------------------------- #
Severity = Literal["critical", "high", "medium", "low", "info"]
BLOCKING_SEVERITIES = {"critical", "high"}


class ReviewFinding(Strict):
    severity: Severity
    category: Literal[
        "requirement_mismatch",
        "security",
        "naming",
        "dependency",
        "policy",
        "best_practice",
        "other",
    ]
    module_call: str | None = None
    message: str = Field(min_length=3, max_length=1000)
    recommendation: str = Field(default="", max_length=1000)


class ReviewResult(Strict):
    decision: Literal["APPROVED", "REJECTED"]
    summary: str = Field(default="", max_length=2000)
    findings: list[ReviewFinding] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _consistent(self) -> "ReviewResult":
        if self.decision == "REJECTED" and not self.findings:
            raise ValueError("a REJECTED review must include at least one finding")
        return self

    @property
    def blocking(self) -> list[ReviewFinding]:
        return [f for f in self.findings if f.severity in BLOCKING_SEVERITIES]

    def effective_decision(self) -> str:
        """Deterministic override: blocking findings always reject."""
        return "REJECTED" if self.decision == "REJECTED" or self.blocking else "APPROVED"


# --------------------------------------------------------------------------- #
# Validation, diagnostics, classification, repair
# --------------------------------------------------------------------------- #
class ValidationIssue(Strict):
    source: Literal["schema", "catalog", "reference", "policy", "plan_policy"]
    message: str
    module_call: str | None = None
    path: str | None = None

    def __str__(self) -> str:
        where = ".".join(p for p in (self.module_call, self.path) if p)
        return f"[{self.source}] {where + ': ' if where else ''}{self.message}"


class Diagnostic(Strict):
    stage: str
    severity: Literal["error", "warning"] = "error"
    summary: str
    detail: str = ""
    filename: str | None = None
    line: int | None = None
    address: str | None = None
    snippet: str | None = None


class ErrorCategory(str, Enum):
    unsupported_argument = "unsupported_argument"
    missing_required_argument = "missing_required_argument"
    type_mismatch = "type_mismatch"
    module_validation = "module_validation"
    invalid_reference = "invalid_reference"
    azure_invalid_value = "azure_invalid_value"
    name_conflict = "name_conflict"
    hcl_syntax = "hcl_syntax"
    authentication = "authentication"
    authorization = "authorization"
    quota_or_capacity = "quota_or_capacity"
    azure_policy = "azure_policy"
    resource_exists = "resource_exists"
    state_lock = "state_lock"
    transient = "transient"
    tooling = "tooling"
    module_defect = "module_defect"
    lint_finding = "lint_finding"
    security_finding = "security_finding"
    unknown = "unknown"


# Categories the repair LLM is allowed to touch. Everything else goes to a human.
LLM_REPAIRABLE = {
    ErrorCategory.unsupported_argument,
    ErrorCategory.missing_required_argument,
    ErrorCategory.type_mismatch,
    ErrorCategory.module_validation,
    ErrorCategory.invalid_reference,
    ErrorCategory.azure_invalid_value,
    ErrorCategory.name_conflict,
}


class ClassifiedError(Strict):
    category: ErrorCategory
    repairable: bool
    target: str | None = Field(
        default=None, description="Module call name, '__outputs__', or None when unattributable."
    )
    diagnostic: Diagnostic
    reason: str = ""

    @property
    def fingerprint(self) -> str:
        d = self.diagnostic
        return f"{self.category.value}|{self.target}|{d.summary}|{d.detail[:200]}"


class RepairPatch(Strict):
    target: str = Field(description="Module call name being repaired, or '__outputs__'.")
    inputs: dict[str, Any] | None = Field(
        default=None, description="Complete replacement inputs for the target module call."
    )
    outputs: list[OutputSpec] | None = Field(
        default=None, description="Complete replacement outputs list (only when target is '__outputs__')."
    )
    explanation: str = Field(min_length=3, max_length=1500)

    @model_validator(mode="after")
    def _shape(self) -> "RepairPatch":
        if self.target == "__outputs__":
            if self.outputs is None:
                raise ValueError("outputs must be provided when target is '__outputs__'")
        elif self.inputs is None:
            raise ValueError("inputs must be provided when repairing a module call")
        return self


# --------------------------------------------------------------------------- #
# Request lifecycle
# --------------------------------------------------------------------------- #
class RequestStatus(str, Enum):
    received = "RECEIVED"
    parsed = "PARSED"
    generated = "GENERATED"
    review_approved = "REVIEW_APPROVED"
    built = "BUILT"
    validated = "VALIDATED"
    awaiting_approval = "AWAITING_APPROVAL"
    applying = "APPLYING"
    deployed = "DEPLOYED"
    needs_user = "NEEDS_USER"
    failed = "FAILED"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RequestState(Strict):
    request_id: str
    status: RequestStatus = RequestStatus.received
    repair_attempts: int = 0
    plan_sha256: str | None = None
    used_approval_nonces: list[str] = Field(default_factory=list)
    message: str = ""
    updated_at: datetime = Field(default_factory=utcnow)
