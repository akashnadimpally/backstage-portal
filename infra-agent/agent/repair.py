"""Agent 3 - Repair: fixes ONE classified, attributed error group at a time.

The repair LLM only ever sees the failing target (one module call or the
outputs list), its module spec, the diagnostics for that target and the
references it may use. Its patch is re-validated deterministically before
it is accepted.
"""

from __future__ import annotations

import json

from .builder import OUTPUTS_TARGET, map_keys, normalize_config
from .generator import deterministic_checks
from .models import ClassifiedError, GeneratedConfig, InfrastructureRequest, RepairPatch
from .module_catalog import Catalog
from .llm import LLMClient
from .prompting import render_prompt
from .settings import Settings


class RepairError(RuntimeError):
    pass


class RepairAgent:
    def __init__(self, llm: LLMClient, catalog: Catalog, settings: Settings):
        self.llm = llm
        self.catalog = catalog
        self.settings = settings

    def _reference_index(self, config: GeneratedConfig, exclude: str) -> str:
        lines = []
        for call in config.module_calls:
            if call.name == exclude:
                continue
            spec = self.catalog.get(call.module)
            if not spec:
                continue
            keys = sorted(map_keys(call.inputs, spec))
            lines.append(f"- {call.name} -> {call.module} -> outputs: {', '.join(spec.outputs)}; map keys: {keys}")
        return "\n".join(lines) or "(none)"

    def repair(
        self,
        errors: list[ClassifiedError],
        config: GeneratedConfig,
        request: InfrastructureRequest,
        request_id: str,
        history: list[str],
        recorder=None,
    ) -> tuple[GeneratedConfig, str]:
        target = errors[0].target
        if not target or any(e.target != target or not e.repairable for e in errors):
            raise RepairError("repair agent only accepts repairable errors for a single target")

        if target == OUTPUTS_TARGET:
            target_json = json.dumps([o.model_dump() for o in config.outputs], indent=1)
            module_spec = "\n\n".join(
                self.catalog.render_spec(m) for m in dict.fromkeys(c.module for c in config.module_calls))
        else:
            call = config.call(target)
            if call is None:
                raise RepairError(f"target module call '{target}' does not exist")
            target_json = json.dumps({"name": call.name, "module": call.module, "inputs": call.inputs}, indent=1)
            module_spec = self.catalog.render_spec(call.module)

        diagnostics = "\n".join(
            f"- [{e.diagnostic.stage}] {e.diagnostic.summary}\n  {e.diagnostic.detail[:1500]}" for e in errors)
        system, user = render_prompt(
            "repair",
            stage=errors[0].diagnostic.stage,
            category=", ".join(sorted({e.category.value for e in errors})),
            target=target,
            diagnostics=diagnostics,
            target_json=target_json,
            module_spec=module_spec,
            reference_index=self._reference_index(config, target),
            history="\n".join(f"- {h}" for h in history) or "(none)",
        )

        last_issues: list[str] = []
        for _ in range(2):
            patch = self.llm.chat_json("repair", system, user, RepairPatch, recorder)
            if patch.target != target:
                last_issues = [f"patch target '{patch.target}' does not match '{target}'"]
            else:
                candidate = self._apply(config, patch)
                candidate = normalize_config(candidate, self.catalog, request, request_id)
                issues = deterministic_checks(candidate, self.catalog, request, request_id, self.settings)
                if not issues:
                    return candidate, patch.explanation
                last_issues = [str(i) for i in issues]
            user += "\n\n## Your patch was rejected by deterministic validation\n" + "\n".join(
                f"- {i}" for i in last_issues)
        raise RepairError("repair patch failed deterministic validation: " + "; ".join(last_issues))

    @staticmethod
    def _apply(config: GeneratedConfig, patch: RepairPatch) -> GeneratedConfig:
        new = config.model_copy(deep=True)
        if patch.target == OUTPUTS_TARGET:
            new.outputs = list(patch.outputs or [])
        else:
            call = new.call(patch.target)
            call.inputs = dict(patch.inputs or {})
        return GeneratedConfig.model_validate(new.model_dump())
