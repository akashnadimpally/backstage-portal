import json

from agent.builder import build_files, normalize_config, render_value, validate_config
from agent.models import GeneratedConfig
from agent.module_catalog import check_value, parse_type, save_catalog, load_catalog, build_catalog

from conftest import good_config


def test_catalog_discovery(catalog):
    widget = catalog.get("widget")
    assert widget.required_variables == ["widgets"]
    assert widget.variables["default_tags"].required is False
    assert widget.outputs["secret_value"].sensitive is True
    assert widget.resource_types == ["azurerm_widget"]
    group = catalog.get("group")
    assert group.variables["groups"].constraints == ["At least one group is required."]
    assert group.description.startswith("Map of groups")


def test_catalog_rebuild_preserves_operator_fields(catalog, modules_root, tmp_path):
    catalog.modules["widget"].enabled = False
    catalog.modules["widget"].notes = "keep me"
    path = tmp_path / "c.yaml"
    save_catalog(catalog, path)
    rebuilt, _ = build_catalog(modules_root, load_catalog(path))
    assert rebuilt.modules["widget"].enabled is False
    assert rebuilt.modules["widget"].notes == "keep me"


def test_type_parser_and_checker():
    node = parse_type("map(object({name = string, size = optional(number, 1), "
                      "rules = optional(list(object({port = number})), [])}))")
    assert node.kind == "map" and set(node.elem.attrs) == {"name", "size", "rules"}
    assert check_value({"a": {"name": "x"}}, node, "v") == []
    problems = check_value({"a": {"size": "big", "extra": 1, "rules": [{"port": "x"}]}}, node, "v")
    msgs = " ".join(m for _, m in problems)
    assert "unknown attribute" in msgs and "required attribute is missing" in msgs and "expected number" in msgs
    assert check_value({"a": {"name": '${module.x.y["k"]}'}}, node, "v") == []


def test_normalize_injects_location_and_tags(catalog, request_model):
    cfg = normalize_config(GeneratedConfig.model_validate(good_config()), catalog, request_model, "rid")
    grp = cfg.call("groups").inputs["groups"]["main"]
    assert grp["location"] == "eastus"
    assert grp["tags"] == {"owner": "team", "environment": "dev", "managed_by": "infra-agent", "request_id": "rid"}
    assert cfg.call("widgets").inputs["default_tags"]["managed_by"] == "infra-agent"


def test_validate_config_catches_llm_mistakes(catalog, request_model):
    raw = good_config()
    raw["module_calls"][1]["inputs"]["widgets"]["w1"]["group_name"] = '${module.groups.group_names["nope"]}'
    raw["module_calls"][1]["inputs"]["bogus"] = 1
    raw["module_calls"].append({"name": "x", "module": "does-not-exist", "inputs": {}})
    raw["outputs"].append({"name": "bad", "value": '${module.widgets.missing_output}'})
    cfg = normalize_config(GeneratedConfig.model_validate(raw), catalog, request_model, "rid")
    text = "\n".join(str(i) for i in validate_config(cfg, catalog))
    assert "key 'nope' is not defined" in text
    assert "unknown input(s) ['bogus']" in text
    assert "does not exist in the catalog" in text
    assert "has no output 'missing_output'" in text


def test_validate_config_rejects_arbitrary_expressions_and_cycles(catalog, request_model):
    raw = good_config()
    raw["module_calls"][0]["inputs"]["groups"]["main"]["name"] = '${module.widgets.widget_ids["w1"]}'
    cfg = normalize_config(GeneratedConfig.model_validate(raw), catalog, request_model, "rid")
    assert any("dependency cycle" in str(i) for i in validate_config(cfg, catalog))

    raw = good_config()
    raw["module_calls"][0]["inputs"]["groups"]["main"]["name"] = 'prefix-${file("/etc/passwd")}'
    cfg = normalize_config(GeneratedConfig.model_validate(raw), catalog, request_model, "rid")
    assert any("invalid expression" in str(i) for i in validate_config(cfg, catalog))


def test_render_value_escapes_and_references():
    assert render_value('${module.a.b["k"]}') == 'module.a.b["k"]'
    assert render_value('he said "hi" ${x} %{y}') == '"he said \\"hi\\" $${x} %%{y}"'
    assert render_value({"a": [1, True, None]}) == '{\n  "a" = [\n    1,\n    true,\n    null,\n  ]\n}'


def test_build_files(catalog, request_model, settings, tmp_path):
    cfg = normalize_config(GeneratedConfig.model_validate(good_config()), catalog, request_model, "rid")
    out = tmp_path / "out"
    result = build_files(cfg, catalog, request_model, "rid", settings, out, "sub-id")
    main = result.files["main.tf"]
    assert 'module "groups" {' in main and 'module "widgets" {' in main
    assert "groups = var.groups__groups" in main
    assert 'group_name" = module.groups.group_names["main"]' in main
    assert 'source = "../modules/group"' in main
    assert "for_each" not in main and "depends_on" not in main
    tfvars = json.loads((out / "terraform.tfvars.json").read_text())
    assert tfvars["subscription_id"] == "sub-id"
    assert tfvars["groups__groups"]["main"]["location"] == "eastus"
    assert result.target_for_line("main.tf", result.line_map["widgets"][0] + 2) == "widgets"
    assert result.target_for_line("outputs.tf", 3) == "__outputs__"
    assert 'subscription_id = var.subscription_id' in result.files["providers.tf"]
