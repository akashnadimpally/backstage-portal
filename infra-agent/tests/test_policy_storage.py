import json

import pytest

from agent.approval import ApprovalError, issue_token, verify_token
from agent.builder import normalize_config
from agent.models import GeneratedConfig, RequestStatus
from agent.policy import check_config, check_plan, summarize_plan
from agent.settings import ApprovalSettings, PolicySettings
from agent.storage import RequestStore

from conftest import good_config

SECRET = "x" * 40


def test_config_policy(catalog, request_model, settings):
    raw = good_config()
    w = raw["module_calls"][1]["inputs"]["widgets"]["w1"]
    w["location"] = "antarctica"
    w["admin_password"] = "P@ssw0rd!"
    cfg = normalize_config(GeneratedConfig.model_validate(raw), catalog, request_model, "rid")
    text = "\n".join(str(i) for i in check_config(cfg, catalog, request_model, "rid", settings.policy))
    assert "location 'antarctica' is not allowed" in text
    assert "secrets must not be generated literally" in text

    w["location"] = "eastus"
    w["admin_password"] = "${var.widget_admin_password}"
    cfg = normalize_config(GeneratedConfig.model_validate(raw), catalog, request_model, "rid")
    assert check_config(cfg, catalog, request_model, "rid", settings.policy) == []


def test_denied_module(catalog, request_model, settings):
    settings.policy.denied_modules = ["wid*"]
    cfg = normalize_config(GeneratedConfig.model_validate(good_config()), catalog, request_model, "rid")
    assert any("not allowed by policy" in str(i) for i in check_config(cfg, catalog, request_model, "rid", settings.policy))


def _rc(actions, after=None, provider="registry.terraform.io/hashicorp/azurerm", rtype="azurerm_x"):
    return {"address": f"{rtype}.a", "type": rtype, "provider_name": provider,
            "change": {"actions": actions, "after": after or {}}}


def test_plan_policy():
    policy = PolicySettings(allowed_locations=["eastus"], required_tags=["owner"], denied_resource_types=["azurerm_bad*"])
    plan = {"resource_changes": [
        _rc(["create"], {"location": "East US", "tags": {"owner": "a"}}),
        _rc(["delete"]),
        _rc(["delete", "create"]),
        _rc(["create"], {"location": "westus"}),
        _rc(["create"], {"tags": {}}),
        _rc(["create"], provider="registry.terraform.io/hashicorp/random"),
        _rc(["create"], rtype="azurerm_badthing"),
        _rc(["no-op"]),
    ]}
    text = "\n".join(str(i) for i in check_plan(plan, policy))
    assert "deletion is not allowed" in text
    assert "replacement is not allowed" in text
    assert "location 'westus' is not allowed" in text
    assert "missing required tags ['owner']" in text
    assert "provider 'registry.terraform.io/hashicorp/random' is not allowed" in text
    assert "denied by policy" in text
    assert "East US" not in text
    assert summarize_plan(plan)["counts"]["replace"] == 1


def test_approval_tokens(monkeypatch):
    cfg = ApprovalSettings(token_ttl_minutes=5)
    with pytest.raises(ApprovalError):
        issue_token("rid", "hash", "me", cfg)
    monkeypatch.setenv(cfg.secret_env, SECRET)
    token, payload = issue_token("rid", "hash", "me", cfg)
    assert verify_token(token, "rid", "hash", [], cfg)["by"] == "me"
    with pytest.raises(ApprovalError, match="different request"):
        verify_token(token, "other", "hash", [], cfg)
    with pytest.raises(ApprovalError, match="plan changed"):
        verify_token(token, "rid", "other-hash", [], cfg)
    with pytest.raises(ApprovalError, match="already been used"):
        verify_token(token, "rid", "hash", [payload["nonce"]], cfg)
    body, sig = token.split(".")
    with pytest.raises(ApprovalError, match="signature"):
        verify_token(body + "." + sig[:-2] + "AA", "rid", "hash", [], cfg)
    monkeypatch.setenv(cfg.secret_env, "y" * 40)
    with pytest.raises(ApprovalError, match="signature"):
        verify_token(token, "rid", "hash", [], cfg)


def test_expired_token(monkeypatch):
    cfg = ApprovalSettings(token_ttl_minutes=1)
    monkeypatch.setenv(cfg.secret_env, SECRET)
    token, _ = issue_token("rid", "hash", "me", cfg)
    import agent.approval as approval
    from datetime import timedelta
    real = approval.utcnow
    monkeypatch.setattr(approval, "utcnow", lambda: real() + timedelta(minutes=5))
    with pytest.raises(ApprovalError, match="expired"):
        verify_token(token, "rid", "hash", [], cfg)


def test_store_audit_and_state(tmp_path):
    store = RequestStore.create(tmp_path)
    store.audit("hello", actor="tester", value=1)
    store.set_status(RequestStatus.parsed, "ok")
    lines = [json.loads(l) for l in (store.dir / "audit.jsonl").read_text().splitlines()]
    assert lines[0]["event"] == "hello" and lines[-1]["status"] == "PARSED"
    assert RequestStore.open(tmp_path, store.request_id).load_state().status == RequestStatus.parsed


@pytest.mark.parametrize("bad", ["../etc", "20260101-000000-zzzzzzzz", "..\\x", ""])
def test_store_rejects_bad_ids(tmp_path, bad):
    with pytest.raises(ValueError):
        RequestStore(tmp_path, bad)
