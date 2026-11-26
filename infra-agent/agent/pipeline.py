"""End-to-end orchestration of the infra agent workflow.

request -> Pydantic validation -> Requirement Parser -> catalog/policy ->
Generator <-> Reviewer (reflection) -> deterministic builder ->
fmt/init/validate/lint/security -> plan -> plan policy -> human approval ->
apply (approval token) -> result.

Any Terraform failure is classified first; only attributable code-level
errors reach the Repair agent, at most ``loops.max_repair_attempts`` times.
"""

from __future__ import annotations

import os
import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from .approval import ApprovalError, file_sha256, issue_token, verify_token
from .builder import BuildError, BuildResult, build_files, load_build_result
from .classifier import classify
from .executor import StaticValidationController, StepOutcome, TerraformExecutor, plan_file
from .generator import GenerationError, Generator
from .models import (
    ClassifiedError,
    ErrorCategory,
    GeneratedConfig,
    InfrastructureRequest,
    ParsedRequirements,
    RequestStatus,
    ReviewResult,
)
from .module_catalog import Catalog, load_catalog
from .llm import JSONChatClient, LLMClient, LLMError, create_llm
from .policy import check_plan, summarize_plan
from .repair import RepairAgent, RepairError
from .requirements_parser import RequirementParser, RequirementsError
from .reviewer import Reviewer
from .settings import Settings
from .storage import RequestStore


class NeedsUser(RuntimeError):
    def __init__(self, message: str, details: list[str] | None = None, status: RequestStatus = RequestStatus.needs_user):
        super().__init__(message)
        self.details = details or []
        self.status = status


@dataclass
class PipelineResult:
    request_id: str
    status: RequestStatus
    message: str
    details: list[str] = field(default_factory=list)
    plan_summary: dict | None = None
    outputs: dict | None = None
    request_dir: str = ""


def resolve_subscription(request: InfrastructureRequest, settings: Settings) -> str:
    if request.subscription_id:
        return str(request.subscription_id)
    env_value = os.environ.get(settings.azure.subscription_id_env, "").strip()
    try:
        return str(uuid.UUID(env_value))
    except ValueError as exc:
        raise NeedsUser(
            f"No subscription: set 'subscription_id' in the request or the {settings.azure.subscription_id_env} "
            "environment variable to a valid GUID.") from exc


def _fmt_errors(errors: list[ClassifiedError]) -> list[str]:
    out = []
    for e in errors:
        d = e.diagnostic
        loc = f" ({d.filename}:{d.line})" if d.filename else ""
        out.append(f"[{d.stage}] [{e.category.value}] target={e.target or '-'}{loc}: {d.summary}"
                   + (f"\n    {d.detail[:800]}" if d.detail else "") + f"\n    -> {e.reason}")
    return out


class Pipeline:
    def __init__(self, settings: Settings, llm: LLMClient | None = None, catalog: Catalog | None = None,
                 executor_factory=None):
        self.settings = settings
        self.catalog = catalog or load_catalog(settings.catalog_path)
        self.llm = llm or create_llm(settings)
        self.parser = RequirementParser(self.llm, self.catalog, settings)
        self.generator = Generator(self.llm, self.catalog, settings)
        self.reviewer = Reviewer(self.llm, self.catalog, settings)
        self.repairer = RepairAgent(self.llm, self.catalog, settings)
        self.executor_factory = executor_factory or (lambda store, sub: TerraformExecutor(settings, store, sub))

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _recorder(store: RequestStore):
        def record(role: str, payload: dict) -> None:
            name = store.record_llm_call(role, payload)
            store.audit("llm.call", actor=role, file=name, valid=payload.get("valid"),
                        elapsed_s=payload.get("elapsed_s"))
        return record

    def _fail(self, store: RequestStore, exc: NeedsUser) -> PipelineResult:
        store.set_status(exc.status, str(exc))
        store.audit("stopped", message=str(exc), details=exc.details)
        return PipelineResult(store.request_id, exc.status, str(exc), exc.details, request_dir=str(store.dir))

    def _build(self, store, config, request, subscription) -> BuildResult:
        store.write("generated-config.json", config)
        return build_files(config, self.catalog, request, store.request_id, self.settings, store.dir, subscription)

    # ------------------------------------------------------------------- run
    def run(self, request: InfrastructureRequest) -> PipelineResult:
        if isinstance(self.llm, JSONChatClient):
            try:
                self.llm.ensure_configured()
            except LLMError as exc:
                raise NeedsUser(str(exc), status=RequestStatus.failed) from exc
        subscription = resolve_subscription(request, self.settings)
        store = RequestStore.create(self.settings.generated_dir)
        store.write("request.json", request)
        store.audit("request.received", actor=request.requested_by)
        rec = self._recorder(store)
        try:
            parsed, discarded = self.parser.parse(request, rec)
            if discarded:
                store.audit("parser.grounding", actor="system", discarded=discarded)
            store.write("requirements.json", parsed)
            store.set_status(RequestStatus.parsed, parsed.summary)

            config, build, review = self._generate_and_review(store, request, parsed, subscription, rec)
            store.set_status(RequestStatus.review_approved, review.summary)
            return self._validate_and_plan(store, request, config, build, subscription)
        except RequirementsError as exc:
            details = [str(i) for i in exc.issues] + [f"unsupported: {u}" for u in exc.unsupported]
            return self._fail(store, NeedsUser(str(exc), details))
        except GenerationError as exc:
            return self._fail(store, NeedsUser(str(exc), [str(i) for i in exc.issues]))
        except (LLMError, BuildError) as exc:
            return self._fail(store, NeedsUser(str(exc), status=RequestStatus.failed))
        except NeedsUser as exc:
            return self._fail(store, exc)

    def _generate_and_review(self, store, request, parsed: ParsedRequirements, subscription, rec):
        findings = None
        previous: GeneratedConfig | None = None
        review: ReviewResult | None = None
        rounds = self.settings.loops.max_review_rounds
        for round_no in range(1, rounds + 1):
            config = self.generator.generate(
                request, store.request_id, parsed, findings, previous, rec,
                on_attempt=lambda a, issues: store.audit("generator.attempt", actor="generator",
                                                         round=round_no, attempt=a, issues=issues),
            )
            build = self._build(store, config, request, subscription)
            store.set_status(RequestStatus.generated, f"review round {round_no}")
            review = self.reviewer.review(request, parsed, config, build.files["main.tf"], rec)
            store.write(f"reviews/round-{round_no}.json", review)
            store.write("review.json", review)
            store.audit("review", actor="reviewer", round=round_no, decision=review.decision,
                        findings=len(review.findings))
            if review.decision == "APPROVED":
                return config, build, review
            findings, previous = review.findings, config
        raise NeedsUser(
            f"Reviewer rejected the configuration after {rounds} round(s).",
            [f"[{f.severity}] {f.module_call or 'general'}: {f.message} -> {f.recommendation}"
             for f in (review.findings if review else [])],
        )

    # -------------------------------------------- static checks + plan loop
    def _validate_and_plan(self, store: RequestStore, request: InfrastructureRequest, config: GeneratedConfig,
                           build: BuildResult, subscription: str, reason: str = "") -> PipelineResult:
        rec = self._recorder(store)
        executor = self.executor_factory(store, subscription)
        controller = StaticValidationController(executor)
        transient_retries = 0
        history: dict[str, list[str]] = defaultdict(list)

        while True:
            outcome: StepOutcome = controller.run()
            if outcome.ok:
                store.set_status(RequestStatus.validated, "static checks passed",)
                store.audit("static.passed", warnings=[d.summary for d in outcome.warnings], skipped=outcome.skipped)
                outcome = executor.plan()
            if outcome.ok:
                break
            config, transient_retries = self._handle_failure(
                store, outcome, config, build, request, transient_retries, history, rec)
            build = self._build(store, config, request, subscription)

        plan = store.read("plan.json")
        summary = summarize_plan(plan)
        store.write("plan-summary.json", summary)
        issues = check_plan(plan, self.settings.policy)
        if issues:
            raise NeedsUser("The plan violates policy; apply is blocked.", [str(i) for i in issues])
        plan_sha = file_sha256(plan_file(store))
        message = (reason + " " if reason else "") + "Plan ready. Human approval required before apply."
        store.set_status(RequestStatus.awaiting_approval, message, plan_sha256=plan_sha)
        return PipelineResult(store.request_id, RequestStatus.awaiting_approval, message,
                              plan_summary=summary, request_dir=str(store.dir))

    def _handle_failure(self, store, outcome: StepOutcome, config, build, request, transient_retries,
                        history, rec) -> tuple[GeneratedConfig, int]:
        errors = classify(outcome.diagnostics, build, {c.name: c.module for c in config.module_calls})
        state = store.load_state()
        store.write(f"errors/{outcome.stage}-{len(list((store.dir / 'errors').glob('*.json'))) + 1:02d}.json",
                    [e.model_dump(mode="json") for e in errors])
        store.audit("terraform.failed", stage=outcome.stage,
                    classified=[{"category": e.category.value, "target": e.target, "repairable": e.repairable}
                                for e in errors])
        if not errors:
            raise NeedsUser(f"terraform {outcome.stage} failed without diagnostics; see logs/.",
                            status=RequestStatus.failed)

        if all(e.category == ErrorCategory.transient for e in errors):
            if transient_retries < self.settings.loops.max_transient_retries:
                store.audit("retry.transient", attempt=transient_retries + 1)
                return config, transient_retries + 1
            raise NeedsUser("Transient errors persisted after retries.", _fmt_errors(errors))

        blocking = [e for e in errors if not e.repairable]
        if blocking:
            raise NeedsUser(f"terraform {outcome.stage} failed with errors that need human action.",
                            _fmt_errors(errors))

        max_attempts = self.settings.loops.max_repair_attempts
        if state.repair_attempts >= max_attempts:
            raise NeedsUser(f"Errors persist after {max_attempts} repair attempts.", _fmt_errors(errors))
        state.repair_attempts += 1
        store.save_state(state)

        groups: dict[str, list[ClassifiedError]] = defaultdict(list)
        for e in errors:
            groups[e.target].append(e)
        for target, group in groups.items():
            try:
                config, explanation = self.repairer.repair(
                    group, config, request, store.request_id, history[target], rec)
                history[target].append(f"attempt {state.repair_attempts}: {explanation}")
                store.audit("repair.applied", actor="repair", attempt=state.repair_attempts, target=target,
                            explanation=explanation)
            except (RepairError, LLMError) as exc:
                history[target].append(f"attempt {state.repair_attempts}: rejected ({exc})")
                store.audit("repair.rejected", actor="repair", attempt=state.repair_attempts, target=target,
                            error=str(exc))
        return config, transient_retries

    # ---------------------------------------------------------- approvals
    def _open(self, request_id: str) -> RequestStore:
        return RequestStore.open(self.settings.generated_dir, request_id)

    def approve(self, request_id: str, approver: str) -> tuple[str, dict]:
        store = self._open(request_id)
        state = store.load_state()
        if state.status != RequestStatus.awaiting_approval:
            raise ApprovalError(f"request is {state.status.value}, not AWAITING_APPROVAL")
        current = file_sha256(plan_file(store))
        if current != state.plan_sha256:
            raise ApprovalError("tfplan on disk does not match the recorded plan hash; re-run the plan")
        token, payload = issue_token(request_id, current, approver, self.settings.approval)
        store.audit("approval.issued", actor=approver, nonce=payload["nonce"], expires=payload["exp"],
                    plan_sha256=current)
        return token, store.read("plan-summary.json")

    def apply(self, request_id: str, token: str) -> PipelineResult:
        store = self._open(request_id)
        state = store.load_state()
        if state.status != RequestStatus.awaiting_approval:
            raise ApprovalError(f"request is {state.status.value}, not AWAITING_APPROVAL")
        tfplan = plan_file(store)
        if not tfplan.exists():
            raise ApprovalError("saved plan (tfplan) is missing")
        current = file_sha256(tfplan)
        if current != state.plan_sha256:
            raise ApprovalError("tfplan changed since it was planned; re-run the plan")
        payload = verify_token(token, request_id, current, state.used_approval_nonces, self.settings.approval)
        state.used_approval_nonces.append(payload["nonce"])
        store.save_state(state)
        store.audit("approval.accepted", actor=payload["by"], nonce=payload["nonce"])

        request = InfrastructureRequest.model_validate(store.read("request.json"))
        subscription = resolve_subscription(request, self.settings)
        store.set_status(RequestStatus.applying, f"approved by {payload['by']}")
        executor = self.executor_factory(store, subscription)
        outcome = executor.apply()
        if outcome.ok:
            outputs = executor.outputs()
            store.write("result.json", {"status": "DEPLOYED", "outputs": outputs})
            store.set_status(RequestStatus.deployed, "apply completed")
            return PipelineResult(request_id, RequestStatus.deployed, "Deployment completed.",
                                  outputs=outputs, request_dir=str(store.dir))

        config = GeneratedConfig.model_validate(store.read("generated-config.json"))
        build = load_build_result(store.dir)
        rec = self._recorder(store)
        try:
            config, _ = self._handle_failure(store, outcome, config, build, request, 0, defaultdict(list), rec)
            build = self._build(store, config, request, subscription)
            return self._validate_and_plan(store, request, config, build, subscription,
                                           reason="Apply failed and was repaired; a NEW approval is required.")
        except (NeedsUser, BuildError) as exc:
            if isinstance(exc, BuildError):
                exc = NeedsUser(str(exc), status=RequestStatus.failed)
            return self._fail(store, exc)

    def status(self, request_id: str) -> dict:
        store = self._open(request_id)
        state = store.load_state()
        data = state.model_dump(mode="json")
        data.pop("used_approval_nonces", None)
        data["request_dir"] = str(store.dir)
        if store.exists("plan-summary.json"):
            data["plan_summary"] = store.read("plan-summary.json")
        return data
