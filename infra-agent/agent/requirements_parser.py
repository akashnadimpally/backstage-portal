"""Requirement Parser: free-text request -> structured ParsedRequirements (JSON only).

LLM output is grounded deterministically against the user's own words:
* components / unsupported items must quote evidence found in the request,
  otherwise they are discarded (and audited) as hallucinations;
* duplicate components for the same module are merged;
* modules added only as dependencies ("implicit: ...") are capped.
"""

from __future__ import annotations

import re

from .models import (
    IMPLICIT_PREFIX,
    InfrastructureRequest,
    ParsedRequirements,
    RequiredComponent,
    UnsupportedItem,
    ValidationIssue,
)
from .module_catalog import Catalog
from .llm import LLMClient
from .policy import allowed_module_keys, check_requirements
from .prompting import render_prompt
from .settings import Settings

_WORD = re.compile(r"[a-z0-9][a-z0-9./:\-]*")
GROUNDING_THRESHOLD = 0.6


class RequirementsError(RuntimeError):
    def __init__(self, message: str, issues: list[ValidationIssue] | None = None, unsupported: list[str] | None = None):
        super().__init__(message)
        self.issues = issues or []
        self.unsupported = unsupported or []


def _words(text: str) -> list[str]:
    out = []
    for w in _WORD.findall(text.lower()):
        w = w.strip(".:-")
        if len(w) >= 3 or w.isdigit():
            out.append(w)
    return out


def _stem(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def is_grounded(evidence: str, description: str) -> bool:
    """True when most significant words of ``evidence`` appear in the user's description (plural-insensitive)."""
    words = _words(evidence)
    if not words:
        return False
    haystack = " ".join(_WORD.findall(description.lower()))
    stems = {_stem(w) for w in _words(description)}
    hits = sum(1 for w in words if w in haystack or _stem(w) in stems or _stem(w) in haystack)
    return hits / len(words) >= GROUNDING_THRESHOLD


def _covered_terms(catalog: Catalog, keys: list[str]) -> set[str]:
    terms: set[str] = set()
    for key in keys:
        spec = catalog.modules[key]
        terms.add(key.lower())
        for rtype in spec.resource_types:
            terms.add(rtype.lower())
            terms.add(rtype.lower().removeprefix("azurerm_"))
    return {t for t in terms if len(t) >= 4}


def ground(
    parsed: ParsedRequirements,
    description: str,
    valid_modules: set[str],
    covered_terms: set[str] | None = None,
) -> tuple[ParsedRequirements, list[str]]:
    """Drop ungrounded items and merge duplicate modules. Returns (cleaned, discarded-notes)."""
    covered_terms = covered_terms or set(valid_modules)
    discarded: list[str] = []
    merged: dict[str, RequiredComponent] = {}
    for comp in parsed.components:
        if not comp.implicit and not is_grounded(comp.evidence, description):
            discarded.append(f"component '{comp.module}' dropped: evidence {comp.evidence!r} not found in request")
            continue
        if comp.implicit and comp.module not in valid_modules:
            discarded.append(f"implicit component '{comp.module}' dropped: not an allowed catalog module")
            continue
        prev = merged.get(comp.module)
        if prev is None:
            merged[comp.module] = comp
            continue
        if prev.implicit:
            evidence = comp.evidence
        elif comp.implicit:
            evidence = prev.evidence
        else:
            evidence = f"{prev.evidence} | {comp.evidence}"
        merged[comp.module] = RequiredComponent(
            module=comp.module,
            purpose=f"{prev.purpose}; {comp.purpose}"[:500],
            details="\n".join(d for d in (prev.details, comp.details) if d)[:2000],
            evidence=evidence[:1000],
        )
        discarded.append(f"duplicate component '{comp.module}' merged")

    component_words = {w for c in merged.values() if not c.implicit for w in _words(c.evidence)}
    unsupported: list[UnsupportedItem | str] = []
    for item in parsed.unsupported:
        label = item.capability if isinstance(item, UnsupportedItem) else item
        normalized = re.sub(r"[\s\-]+", "_", label.lower())
        if not isinstance(item, UnsupportedItem) or not is_grounded(item.evidence, description):
            discarded.append(f"unsupported item {label!r} dropped: not grounded in the request")
        elif any(t in normalized or t.replace("-", "_") in normalized for t in covered_terms):
            discarded.append(f"unsupported item {label!r} dropped: provided by a catalog module")
        elif set(_words(item.evidence)) <= component_words:
            discarded.append(f"unsupported item {label!r} dropped: its evidence is covered by a selected component")
        else:
            unsupported.append(item)

    cleaned = parsed.model_copy(update={"components": list(merged.values()), "unsupported": unsupported})
    return cleaned, discarded


class RequirementParser:
    def __init__(self, llm: LLMClient, catalog: Catalog, settings: Settings):
        self.llm = llm
        self.catalog = catalog
        self.settings = settings

    def parse(self, request: InfrastructureRequest, recorder=None) -> tuple[ParsedRequirements, list[str]]:
        keys = allowed_module_keys(self.catalog, self.settings.policy)
        system, base_user = render_prompt(
            "parser",
            environment=request.environment.value,
            location=request.location,
            naming_prefix=request.naming_prefix,
            description=request.description,
            catalog_index=self.catalog.render_index(keys),
        )
        user = base_user
        covered = _covered_terms(self.catalog, keys)
        problems: list[str] = []
        discarded: list[str] = []
        for _ in range(self.settings.loops.max_generation_attempts):
            raw = self.llm.chat_json("parser", system, user, ParsedRequirements, recorder)
            parsed, discarded = ground(raw, request.description, set(keys), covered)
            problems = [str(i) for i in check_requirements(parsed, self.catalog, self.settings.policy)]
            explicit = parsed.explicit_components
            if not explicit:
                problems.append("no component is backed by evidence quoted from the request")
            implicit = len(parsed.components) - len(explicit)
            if explicit and implicit > len(explicit):
                problems.append(f"{implicit} implicit components for {len(explicit)} requested ones; "
                                "only add modules that the requested modules cannot work without")
            if not problems:
                if parsed.unsupported:
                    raise RequirementsError(
                        "The request includes capabilities no approved module provides.",
                        unsupported=[f'{u.capability} ("{u.evidence}")' for u in parsed.unsupported
                                     if isinstance(u, UnsupportedItem)],
                    )
                return parsed, discarded
            user = (
                base_user
                + "\n\n## Your previous answer was rejected\n"
                + "\n".join(f"- {p}" for p in problems + discarded)
                + f"\nAllowed module keys: {', '.join(keys)}"
                + f"\nEvidence must be words copied from the description; use '{IMPLICIT_PREFIX} <reason>' "
                "only for dependencies."
            )
        raise RequirementsError(
            "Requirement parser could not produce grounded requirements.",
            issues=[ValidationIssue(source="schema", message=p) for p in problems + discarded],
        )
