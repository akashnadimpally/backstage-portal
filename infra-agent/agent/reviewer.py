"""Agent 2 - Reviewer: independent judgement on the generated configuration."""

from __future__ import annotations

from .models import GeneratedConfig, InfrastructureRequest, ParsedRequirements, ReviewResult
from .module_catalog import Catalog
from .llm import LLMClient
from .prompting import render_prompt
from .settings import Settings


class Reviewer:
    def __init__(self, llm: LLMClient, catalog: Catalog, settings: Settings):
        self.llm = llm
        self.catalog = catalog
        self.settings = settings

    def review(
        self,
        request: InfrastructureRequest,
        parsed: ParsedRequirements,
        config: GeneratedConfig,
        main_tf: str,
        recorder=None,
    ) -> ReviewResult:
        modules = list(dict.fromkeys(c.module for c in config.module_calls))
        system, user = render_prompt(
            "reviewer",
            environment=request.environment.value,
            location=request.location,
            naming_prefix=request.naming_prefix,
            description=request.description,
            requirements=parsed.model_dump_json(indent=1),
            module_specs="\n\n".join(self.catalog.render_spec(k) for k in modules),
            config_json=config.model_dump_json(indent=1),
            main_tf=main_tf,
        )
        result = self.llm.chat_json("reviewer", system, user, ReviewResult, recorder)
        known = {c.name for c in config.module_calls}
        for finding in result.findings:
            if finding.module_call and finding.module_call not in known:
                finding.module_call = None
        if result.effective_decision() != result.decision:
            result = result.model_copy(update={"decision": result.effective_decision()})
        return result
