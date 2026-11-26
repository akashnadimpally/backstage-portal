import pytest

from agent.builder import BuildResult
from agent.classifier import classify
from agent.executor import parse_json_stream, parse_text_errors, parse_validate_json
from agent.models import Diagnostic, ErrorCategory

CALLS = {"groups": "group", "widgets": "widget"}
BUILD = BuildResult(files={}, line_map={"groups": (3, 8), "widgets": (10, 30)}, tfvars={})


def _one(diag):
    [res] = classify([diag], BUILD, CALLS)
    return res


@pytest.mark.parametrize("summary,detail,category,repairable", [
    ("Unsupported argument", 'An argument named "foo" is not expected here.', ErrorCategory.unsupported_argument, True),
    ("Missing required argument", 'The argument "x" is required.', ErrorCategory.missing_required_argument, True),
    ("Invalid value for input variable", "object required", ErrorCategory.type_mismatch, True),
    ("Invalid value for variable", "At least one group is required.", ErrorCategory.module_validation, True),
    ("Unsupported attribute", 'This object does not have an attribute named "x".', ErrorCategory.invalid_reference, True),
    ("Error building ARM Config", "please run 'az login'", ErrorCategory.authentication, False),
    ("creating thing", "AuthorizationFailed: The client does not have authorization", ErrorCategory.authorization, False),
    ("creating thing", "QuotaExceeded: cores", ErrorCategory.quota_or_capacity, False),
    ("creating thing", "RequestDisallowedByPolicy", ErrorCategory.azure_policy, False),
    ("A resource with the ID already exists - to be managed via Terraform this resource needs to be imported", "",
     ErrorCategory.resource_exists, False),
    ("Error acquiring the state lock", "", ErrorCategory.state_lock, False),
    ("Failed to query available provider packages", "i/o timeout", ErrorCategory.transient, False),
    ("creating storage", "StorageAccountAlreadyTaken", ErrorCategory.name_conflict, True),
    ("creating thing", "InvalidParameter: bad sku", ErrorCategory.azure_invalid_value, True),
    ("something odd", "nobody knows", ErrorCategory.unknown, False),
])
def test_categories(summary, detail, category, repairable):
    res = _one(Diagnostic(stage="plan", summary=summary, detail=detail, filename="main.tf", line=12))
    assert res.category == category
    assert res.target == "widgets"
    assert res.repairable is repairable


def test_unattributable_code_error_is_not_repairable():
    res = _one(Diagnostic(stage="validate", summary="Unsupported argument", detail="x"))
    assert res.category == ErrorCategory.unsupported_argument
    assert res.target is None and res.repairable is False


def test_module_source_defect_goes_to_human():
    res = _one(Diagnostic(stage="validate", summary="Not enough function arguments",
                          detail='Function "trim" expects 2 argument(s).',
                          filename="../modules/widget/variables.tf", line=4))
    assert res.category == ErrorCategory.module_defect
    assert res.target == "widgets" and res.repairable is False


def test_attribution_from_address_and_variables():
    res = _one(Diagnostic(stage="apply", summary="creating", detail="InvalidParameter",
                          address='module.groups.azurerm_x.this["a"]'))
    assert res.target == "groups"
    res = _one(Diagnostic(stage="validate", summary="Invalid value for input variable",
                          detail="var.widgets__widgets is object", filename="variables.tf", line=1))
    assert res.target == "widgets"


def test_lint_and_security_are_never_llm_repaired():
    assert _one(Diagnostic(stage="lint", summary="x", filename="main.tf", line=12)).repairable is False
    assert _one(Diagnostic(stage="security", summary="x", filename="main.tf", line=12)).repairable is False


def test_parsers():
    stream = '{"type":"log"}\n{"type":"diagnostic","diagnostic":{"severity":"error","summary":"S","detail":"D",' \
             '"address":"module.a.x","range":{"filename":"main.tf","start":{"line":4}}}}\n'
    [d] = parse_json_stream("plan", stream)
    assert (d.summary, d.line, d.address) == ("S", 4, "module.a.x")
    [v] = parse_validate_json('{"valid":false,"diagnostics":[{"severity":"error","summary":"V"}]}')
    assert v.summary == "V"
    text = 'Error: Module not installed\n\n  on main.tf line 3, in module "groups":\n   3: module "groups" {\n'
    [t] = parse_text_errors("init", text)
    assert (t.summary, t.filename, t.line, t.address) == ("Module not installed", "main.tf", 3, "module.groups")
