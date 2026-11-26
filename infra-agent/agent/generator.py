"""Agent 1 - Generator: requirements + module specs -> GeneratedConfig.

The generator self-corrects against deterministic catalog/reference/policy
checks before its output is ever shown to the reviewer.
"""

from __future__ import annotations

import json

from .builder import normalize_config, validate_config
from .models import GeneratedConfig, InfrastructureRequest, ParsedRequirements, ReviewFinding, ValidationIssue
from .module_catalog import Catalog
from .llm import LLMClient
from .policy import check_config
from .prompting import render_prompt
from .settings import Settings


class GenerationError(RuntimeError):
    def __init__(self, message: str, issues: list[ValidationIssue]):
        super().__init__(message)
        self.issues = issues


def coverage_issues(config: GeneratedConfig, parsed: ParsedRequirements) -> list[ValidationIssue]:
    """Every explicitly requested module must be used; implicit dependencies are optional."""
    used = {c.module for c in config.module_calls}
    return [
        ValidationIssue(source="schema", message=f"required module '{comp.module}' ({comp.purpose}) is not used")
        for comp in parsed.explicit_components
        if comp.module not in used
    ]


def deterministic_checks(
    config: GeneratedConfig,
    catalog: Catalog,
    request: InfrastructureRequest,
    request_id: str,
    settings: Settings,
    parsed: ParsedRequirements | None = None,
) -> list[ValidationIssue]:
    issues = validate_config(config, catalog)
    issues += check_config(config, catalog, request, request_id, settings.policy)
    if parsed is not None:
        issues += coverage_issues(config, parsed)
    return issues


class Generator:
    def __init__(self, llm: LLMClient, catalog: Catalog, settings: Settings):
        self.llm = llm
        self.catalog = catalog
        self.settings = settings

    def modules_for(self, parsed: ParsedRequirements) -> list[str]:
        return list(dict.fromkeys(c.module for c in parsed.components))

    def generate(
        self,
        request: InfrastructureRequest,
        request_id: str,
        parsed: ParsedRequirements,
        review_findings: list[ReviewFinding] | None = None,
        previous: GeneratedConfig | None = None,
        recorder=None,
        on_attempt=None,
    ) -> GeneratedConfig:
        specs = "\n\n".join(self.catalog.render_spec(k) for k in self.modules_for(parsed))
        feedback = ""
        if review_findings:
            feedback = (
                "\n## Reviewer rejected the previous configuration. Address every finding:\n"
                + "\n".join(
                    f"- [{f.severity}] {f.module_call or 'general'}: {f.message} -> {f.recommendation}"
                    for f in review_findings
                )
                + "\n\n## Previous configuration\n"
                + (previous.model_dump_json(indent=1) if previous else "")
            )
        issues: list[ValidationIssue] = []
        for attempt in range(1, self.settings.loops.max_generation_attempts + 1):
            system, user = render_prompt(
                "generator",
                request_id=request_id,
                environment=request.environment.value,
                location=request.location,
                naming_prefix=request.naming_prefix,
                description=request.description,
                requirements=parsed.model_dump_json(indent=1),
                module_specs=specs,
                feedback=feedback,
            )
            raw = self.llm.chat_json("generator", system, user, GeneratedConfig, recorder)
            config = normalize_config(raw, self.catalog, request, request_id)
            issues = deterministic_checks(config, self.catalog, request, request_id, self.settings, parsed)
            if on_attempt:
                on_attempt(attempt, [str(i) for i in issues])
            if not issues:
                return config
            feedback = (
                "\n## Your previous configuration failed deterministic validation. Fix ALL of these issues:\n"
                + "\n".join(f"- {i}" for i in issues)
                + "\n\n## Previous configuration\n"
                + json.dumps(raw.model_dump(mode="json"), indent=1)
            )
        raise GenerationError("Generator could not produce a valid configuration.", issues)
