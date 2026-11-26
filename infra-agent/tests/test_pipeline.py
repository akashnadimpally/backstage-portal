"""End-to-end pipeline tests with a scripted LLM and a fake Terraform executor."""

import copy

import pytest

from agent.approval import ApprovalError
from agent.executor import StepOutcome
from agent.models import Diagnostic, RequestStatus
from agent.pipeline import Pipeline

from conftest import FakeExecutor, FakeLLM, good_config

SECRET = "s" * 40

PARSED = {
    "summary": "groups and widgets",
    "components": [
        {"module": "group", "purpose": "container", "details": "one group", "evidence": "in one group"},
        {"module": "widget", "purpose": "workload", "details": "one widget with https rule", "evidence": "Two widgets"},
    ],
}
APPROVED = {"decision": "APPROVED", "summary": "ok", "findings": []}
REJECTED = {"decision": "REJECTED", "summary": "missing", "findings": [
    {"severity": "high", "category": "requirement_mismatch", "module_call": "widgets",
     "message": "only one widget but two requested", "recommendation": "add w2"}]}


def _validate_fail(summary, detail, line):
    return StepOutcome(False, "validate", [Diagnostic(stage="validate", summary=summary, detail=detail,
                                                      filename="main.tf", line=line)])


def _pipeline(settings, catalog, script, **executor_kwargs):
    llm = FakeLLM(script)
    holder = {}

    def factory(store, sub):
        if "exec" not in holder:
            holder["exec"] = FakeExecutor(store, **executor_kwargs)
        holder["exec"].store = store
        return holder["exec"]

    return Pipeline(settings, llm=llm, catalog=catalog, executor_factory=factory), llm, holder


def test_happy_path_then_approval_and_apply(settings, catalog, request_model, monkeypatch):
    monkeypatch.setenv(settings.approval.secret_env, SECRET)
    pipe, llm, holder = _pipeline(settings, catalog, {
        "parser": [PARSED], "generator": [good_config()], "reviewer": [APPROVED]})
    result = pipe.run(request_model)
    assert result.status == RequestStatus.awaiting_approval, result.details
    assert result.plan_summary["counts"]["create"] == 1
    assert [r for r, _ in llm.calls] == ["parser", "generator", "reviewer"]

    with pytest.raises(ApprovalError):
        pipe.apply(result.request_id, "garbage.token")
    token, _ = pipe.approve(result.request_id, "Approver")
    applied = pipe.apply(result.request_id, token)
    assert applied.status == RequestStatus.deployed
    assert applied.outputs == {"widget_id": "abc"}
    with pytest.raises(ApprovalError):
        pipe.apply(result.request_id, token)


def test_tampered_plan_blocks_apply(settings, catalog, request_model, monkeypatch):
    monkeypatch.setenv(settings.approval.secret_env, SECRET)
    pipe, _, _ = _pipeline(settings, catalog, {"parser": [PARSED], "generator": [good_config()], "reviewer": [APPROVED]})
    result = pipe.run(request_model)
    token, _ = pipe.approve(result.request_id, "Approver")
    (pipe.settings.generated_dir / result.request_id / "tfplan").write_bytes(b"evil")
    with pytest.raises(ApprovalError, match="tfplan changed"):
        pipe.apply(result.request_id, token)


def test_reviewer_rejection_drives_regeneration(settings, catalog, request_model):
    second = good_config()
    second["module_calls"][1]["inputs"]["widgets"]["w2"] = copy.deepcopy(
        second["module_calls"][1]["inputs"]["widgets"]["w1"]) | {"name": "wdg-demo-2"}
    pipe, llm, _ = _pipeline(settings, catalog, {
        "parser": [PARSED], "generator": [good_config(), second], "reviewer": [REJECTED, APPROVED]})
    result = pipe.run(request_model)
    assert result.status == RequestStatus.awaiting_approval
    gen_prompts = [u for r, u in llm.calls if r == "generator"]
    assert "only one widget but two requested" in gen_prompts[1]


def test_generator_self_corrects_on_deterministic_errors(settings, catalog, request_model):
    bad = good_config()
    bad["module_calls"][1]["inputs"]["widgets"]["w1"]["colour"] = "red"
    pipe, llm, _ = _pipeline(settings, catalog, {
        "parser": [PARSED], "generator": [bad, good_config()], "reviewer": [APPROVED]})
    result = pipe.run(request_model)
    assert result.status == RequestStatus.awaiting_approval
    assert "unknown attribute(s) ['colour']" in [u for r, u in llm.calls if r == "generator"][1]


def test_unsupported_capability_stops_before_generation(settings, catalog, request_model):
    req = request_model.model_copy(update={"description": request_model.description + " Also a quantum computer."})
    parsed = dict(PARSED, unsupported=[{"capability": "quantum computing", "evidence": "a quantum computer"}])
    pipe, llm, _ = _pipeline(settings, catalog, {"parser": [parsed]})
    result = pipe.run(req)
    assert result.status == RequestStatus.needs_user
    assert any("quantum" in d for d in result.details)
    assert [r for r, _ in llm.calls] == ["parser"]


def test_repairable_error_is_repaired(settings, catalog, request_model):
    pipe, llm, holder = _pipeline(settings, catalog, {
        "parser": [PARSED], "generator": [good_config()], "reviewer": [APPROVED],
        "repair": [{"target": "widgets", "inputs": good_config()["module_calls"][1]["inputs"] | {
            "widgets": {"w1": {**good_config()["module_calls"][1]["inputs"]["widgets"]["w1"], "size": 2}}},
            "explanation": "set size"}],
    }, validate_outcomes=[_validate_fail("Invalid value for variable", "size must be >= 2", 14)])
    result = pipe.run(request_model)
    assert result.status == RequestStatus.awaiting_approval, result.details
    repair_prompt = [u for r, u in llm.calls if r == "repair"][0]
    assert "size must be >= 2" in repair_prompt
    assert "module `widget`" in repair_prompt and "module `group`" not in repair_prompt
    state = pipe.status(result.request_id)
    assert state["repair_attempts"] == 1


def test_non_repairable_error_never_reaches_llm(settings, catalog, request_model):
    pipe, llm, _ = _pipeline(settings, catalog, {
        "parser": [PARSED], "generator": [good_config()], "reviewer": [APPROVED]},
        validate_outcomes=[StepOutcome(False, "init", [Diagnostic(stage="init", summary="Error building ARM Config",
                                                                   detail="please run 'az login'")])])
    result = pipe.run(request_model)
    assert result.status == RequestStatus.needs_user
    assert "authentication" in result.details[0]
    assert "repair" not in [r for r, _ in llm.calls]


def test_repair_stops_after_max_attempts(settings, catalog, request_model):
    failure = _validate_fail("Unsupported argument", 'An argument named "x" is not expected here.', 14)
    patch = {"target": "widgets", "inputs": good_config()["module_calls"][1]["inputs"], "explanation": "retry"}
    max_attempts = settings.loops.max_repair_attempts
    pipe, llm, holder = _pipeline(settings, catalog, {
        "parser": [PARSED], "generator": [good_config()], "reviewer": [APPROVED],
        "repair": [patch] * max_attempts,
    }, validate_outcomes=[failure] * (max_attempts + 1))
    result = pipe.run(request_model)
    assert result.status == RequestStatus.needs_user
    assert f"after {max_attempts} repair attempts" in result.message
    assert [r for r, _ in llm.calls].count("repair") == max_attempts
    assert holder["exec"].counts["validate"] == max_attempts + 1


def test_failed_apply_is_repaired_and_requires_new_approval(settings, catalog, request_model, monkeypatch):
    monkeypatch.setenv(settings.approval.secret_env, SECRET)
    apply_fail = StepOutcome(False, "apply", [Diagnostic(
        stage="apply", summary="creating widget", detail="InvalidParameter: size",
        address='module.widgets.azurerm_widget.this["w1"]')])
    patch = {"target": "widgets", "inputs": good_config()["module_calls"][1]["inputs"], "explanation": "fix size"}
    pipe, _, holder = _pipeline(settings, catalog, {
        "parser": [PARSED], "generator": [good_config()], "reviewer": [APPROVED], "repair": [patch]},
        apply_outcomes=[apply_fail])
    first = pipe.run(request_model)
    token, _ = pipe.approve(first.request_id, "Approver")
    result = pipe.apply(first.request_id, token)
    assert result.status == RequestStatus.awaiting_approval
    assert "NEW approval" in result.message
    with pytest.raises(ApprovalError):
        pipe.apply(first.request_id, token)
    token2, _ = pipe.approve(first.request_id, "Approver")
    assert pipe.apply(first.request_id, token2).status == RequestStatus.deployed


def test_plan_policy_violation_blocks(settings, catalog, request_model):
    plan = {"resource_changes": [{"address": "x", "type": "azurerm_x",
                                  "provider_name": "registry.terraform.io/hashicorp/azurerm",
                                  "change": {"actions": ["delete"], "after": None}}]}
    pipe, _, _ = _pipeline(settings, catalog, {"parser": [PARSED], "generator": [good_config()],
                                               "reviewer": [APPROVED]}, plan_doc=plan)
    result = pipe.run(request_model)
    assert result.status == RequestStatus.needs_user
    assert any("deletion is not allowed" in d for d in result.details)


def test_missing_subscription_rejected_before_llm(settings, catalog, request_model, monkeypatch):
    from agent.pipeline import NeedsUser
    monkeypatch.delenv("ARM_SUBSCRIPTION_ID", raising=False)
    req = request_model.model_copy(update={"subscription_id": None})
    pipe, llm, _ = _pipeline(settings, catalog, {})
    with pytest.raises(NeedsUser):
        pipe.run(req)
    assert llm.calls == []


def test_hallucinated_parser_items_are_dropped(settings, catalog, request_model):
    parsed = {
        "summary": "summary",
        "components": [
            PARSED["components"][0],
            PARSED["components"][0] | {"details": "again"},
            PARSED["components"][1],
            {"module": "widget", "purpose": "extra", "details": "", "evidence": "load balancer for traffic"},
        ],
        "unsupported": ["traffic-manager-profile",
                        {"capability": "traffic manager", "evidence": "global traffic routing"}],
    }
    pipe, llm, _ = _pipeline(settings, catalog, {
        "parser": [parsed], "generator": [good_config()], "reviewer": [APPROVED]})
    result = pipe.run(request_model)
    assert result.status == RequestStatus.awaiting_approval, result.details
    req = pipe.settings.generated_dir / result.request_id / "requirements.json"
    import json
    comps = json.loads(req.read_text())["components"]
    assert [c["module"] for c in comps] == ["group", "widget"]
    assert json.loads(req.read_text())["unsupported"] == []
    audit = (pipe.settings.generated_dir / result.request_id / "audit.jsonl").read_text()
    assert "traffic-manager-profile" in audit and "merged" in audit


def test_parser_over_selection_retries(settings, catalog, request_model):
    too_many = {"summary": "summary", "components": [
        PARSED["components"][1],
        {"module": "group", "purpose": "purpose", "evidence": "implicit: widgets need a group"},
    ] + [{"module": "nope", "purpose": "purpose", "evidence": "implicit: just in case"}]}
    no_evidence = {"summary": "summary", "components": [
        {"module": "widget", "purpose": "purpose", "evidence": "virtual machines everywhere"}]}
    pipe, llm, _ = _pipeline(settings, catalog, {
        "parser": [no_evidence, too_many], "generator": [good_config()], "reviewer": [APPROVED]})
    result = pipe.run(request_model)
    assert result.status == RequestStatus.awaiting_approval, result.details
    second = [u for r, u in llm.calls if r == "parser"][1]
    assert "no component is backed by evidence" in second


def test_unsupported_items_provided_by_catalog_are_dropped(catalog):
    from agent.models import ParsedRequirements
    from agent.requirements_parser import _covered_terms, ground
    desc = "Two widgets in one group with global traffic routing"
    parsed = ParsedRequirements.model_validate({"summary": "summary", "components": [
        {"module": "widget", "purpose": "workload", "evidence": "Two widgets"}],
        "unsupported": [
            {"capability": "azurerm_widget", "evidence": "widgets"},
            {"capability": "widget-thing", "evidence": "Two widgets"},
            {"capability": "traffic routing", "evidence": "global traffic routing"}]})
    keys = catalog.enabled_keys()
    cleaned, discarded = ground(parsed, desc, set(keys), _covered_terms(catalog, keys))
    assert [u.capability for u in cleaned.unsupported] == ["traffic routing"]
    assert sum("provided by a catalog module" in d for d in discarded) == 2
