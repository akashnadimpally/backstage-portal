"""Deterministic error classification.

Rule: classify first, then decide who fixes it. Only code-level errors that
can be attributed to a single module call (or the outputs block) are ever
sent to the repair LLM. Credentials, permissions, quota, policy, state and
tooling problems go straight to a human.
"""

from __future__ import annotations

import re

from .builder import OUTPUTS_TARGET, VAR_SEPARATOR, BuildResult
from .models import LLM_REPAIRABLE, ClassifiedError, Diagnostic, ErrorCategory

_RULES: list[tuple[ErrorCategory, tuple[str, ...]]] = [
    (ErrorCategory.state_lock, (r"error acquiring the state lock", r"state lock")),
    (ErrorCategory.authentication, (
        r"az login", r"error building arm config", r"aadsts\d+", r"obtain(ing)? an access token",
        r"authenticating using the azure cli", r"unable to build authorizer", r"subscription id could not be determined",
        r"invalidauthenticationtoken", r"clientauthenticationerror", r"please run 'az login'",
    )),
    (ErrorCategory.authorization, (
        r"authorizationfailed", r"does not have authorization", r"linkedauthorizationfailed",
        r"status(code)?[=: ]+403", r"\b403 forbidden\b",
    )),
    (ErrorCategory.azure_policy, (r"requestdisallowedbypolicy", r"disallowed by policy")),
    (ErrorCategory.quota_or_capacity, (
        r"quotaexceeded", r"exceeding approved", r"skunotavailable", r"allocationfailed",
        r"zonalallocationfailed", r"operation could not be completed as it results in exceeding",
        r"insufficient capacity",
    )),
    (ErrorCategory.resource_exists, (r"already exists - to be managed via terraform", r"needs to be imported")),
    (ErrorCategory.name_conflict, (
        r"alreadytaken", r"already taken", r"namenotavailable", r"name .* is not available",
        r"already in use", r"storageaccountalreadytaken",
    )),
    (ErrorCategory.tooling, (
        r"module not installed", r"failed to install provider", r"unsupported terraform core version",
        r"invalid module source", r"unreadable module directory", r"failed to read module directory",
        r"executable file not found", r"inconsistent dependency lock file", r"plugin did not respond",
    )),
    (ErrorCategory.transient, (
        r"tls handshake timeout", r"connection reset", r"i/o timeout", r"failed to query available provider packages",
        r"could not connect", r"toomanyrequests", r"status(code)?[=: ]+429", r"temporarily unavailable",
        r"serviceunavailable", r"internalservererror", r"context deadline exceeded", r"retryable",
    )),
    (ErrorCategory.hcl_syntax, (
        r"argument or block definition required", r"unclosed configuration block", r"invalid character",
        r"missing newline after argument", r"invalid multi-line string",
    )),
    (ErrorCategory.unsupported_argument, (
        r"unsupported argument", r"an argument named .* is not expected", r"unsupported block type",
    )),
    (ErrorCategory.missing_required_argument, (r"missing required argument", r"attribute .* is required")),
    (ErrorCategory.invalid_reference, (
        r"reference to undeclared", r"unsupported attribute", r"invalid index", r"invalid reference",
        r"does not have an attribute named", r"the given key does not identify an element",
    )),
    (ErrorCategory.module_validation, (r"invalid value for variable",)),
    (ErrorCategory.type_mismatch, (
        r"invalid value for input variable", r"inappropriate value", r"incorrect attribute value type",
        r"unsuitable value type", r"invalid function argument", r"(object|number|string|bool|list|map|set) required",
    )),
    (ErrorCategory.azure_invalid_value, (
        r"invalidparameter", r"invalidresourcename", r"accountnameinvalid", r"invalidaddressprefix", r"badrequest",
        r"validationerror", r"expected .* to be one of", r"expected .* to match", r"must be between",
        r"is not a valid", r"invalid value for",
    )),
]

_MODULE_ADDR_RE = re.compile(r"module\.([a-z][a-z0-9_]*)")
_IN_MODULE_RE = re.compile(r'in module "([a-z][a-z0-9_]*)"')
_VAR_RE = re.compile(rf"var\.([a-z][a-z0-9_]*?){VAR_SEPARATOR}[a-z0-9_]+")


def in_module_source(diag: Diagnostic) -> bool:
    """True when Terraform located the error inside a module's own source files.

    Root files are always reported as bare names (``main.tf``); module files carry a path.
    """
    return bool(diag.filename) and ("/" in diag.filename or "\\" in diag.filename)


def categorize(diag: Diagnostic) -> ErrorCategory:
    if diag.stage == "lint":
        return ErrorCategory.lint_finding
    if diag.stage == "security":
        return ErrorCategory.security_finding
    if diag.stage == "fmt":
        return ErrorCategory.hcl_syntax
    text = f"{diag.summary}\n{diag.detail}".lower()
    for category, patterns in _RULES:
        if any(re.search(p, text) for p in patterns):
            if category in LLM_REPAIRABLE and diag.stage in ("init", "validate") and in_module_source(diag) \
                    and category != ErrorCategory.module_validation:
                return ErrorCategory.module_defect
            return category
    if in_module_source(diag):
        return ErrorCategory.module_defect
    return ErrorCategory.unknown


def attribute(diag: Diagnostic, build: BuildResult | None, calls: dict[str, str]) -> str | None:
    """Map a diagnostic to a module call name (``calls`` maps call name -> catalog module key)."""
    if build:
        target = build.target_for_line(diag.filename, diag.line)
        if target:
            return target
    for source in (diag.address or "", diag.snippet or "", diag.detail, diag.summary):
        for regex in (_MODULE_ADDR_RE, _IN_MODULE_RE, _VAR_RE):
            m = regex.search(source)
            if m and m.group(1) in calls:
                return m.group(1)
    if in_module_source(diag):
        module_key = diag.filename.replace("\\", "/").rstrip("/").split("/")[-2]
        users = [c for c, k in calls.items() if k == module_key]
        if len(users) == 1:
            return users[0]
    if diag.filename and diag.filename.replace("\\", "/").endswith("outputs.tf"):
        return OUTPUTS_TARGET
    return None


def classify(diagnostics: list[Diagnostic], build: BuildResult | None, calls: dict[str, str]) -> list[ClassifiedError]:
    results = []
    for diag in diagnostics:
        if diag.severity != "error":
            continue
        category = categorize(diag)
        target = attribute(diag, build, calls)
        repairable = category in LLM_REPAIRABLE and target is not None
        if category in LLM_REPAIRABLE and target is None:
            reason = "code-level error but it could not be attributed to a single module call"
        elif repairable:
            reason = f"code-level error attributed to '{target}'"
        else:
            reason = f"'{category.value}' errors require human action"
        results.append(ClassifiedError(category=category, repairable=repairable, target=target,
                                       diagnostic=diag, reason=reason))
    return results
