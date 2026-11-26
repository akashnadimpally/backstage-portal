"""Command-line interface for the infra agent.

    python -m agent.main catalog build
    python -m agent.main check-llm
    python -m agent.main validate-request -f request.yaml
    python -m agent.main run -f request.yaml
    python -m agent.main status <request-id>
    python -m agent.main approve <request-id> --approver "Jane Doe"
    python -m agent.main apply <request-id> --token <token>
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from .approval import ApprovalError
from .models import (
    GeneratedConfig,
    InfrastructureRequest,
    ParsedRequirements,
    RepairPatch,
    RequestStatus,
    ReviewResult,
)
from .module_catalog import build_catalog, load_catalog, save_catalog
from .settings import PROJECT_ROOT, Settings, load_settings

EXIT_OK, EXIT_ERROR, EXIT_INVALID, EXIT_NEEDS_USER = 0, 1, 2, 3


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def load_request(path: Path, settings: Settings) -> InfrastructureRequest:
    """Load and validate a request file. Raises ValidationError before any LLM is contacted."""
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) if path.suffix.lower() in (".yaml", ".yml") else json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("request file must contain a mapping/object")
    return InfrastructureRequest.model_validate(
        data, context={"allowed_locations": settings.policy.allowed_locations})


def _report_validation(exc: ValidationError) -> int:
    print("Request rejected (no LLM was contacted):", file=sys.stderr)
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "<root>"
        print(f"  - {loc}: {err['msg']}", file=sys.stderr)
    return EXIT_INVALID


def _print_result(result) -> int:
    print(f"\nRequest ID : {result.request_id}")
    print(f"Status     : {result.status.value}")
    print(f"Message    : {result.message}")
    if result.request_dir:
        print(f"Artifacts  : {result.request_dir}")
    if result.plan_summary:
        counts = result.plan_summary["counts"]
        print("Plan       : " + ", ".join(f"{k}={v}" for k, v in counts.items() if v))
        for ch in result.plan_summary["changes"][:200]:
            print(f"   {ch['action']:>8}  {ch['address']}")
    if result.details:
        print("\nDetails:")
        for d in result.details:
            print(f"  - {d}")
    if result.outputs:
        print("\nOutputs:")
        _print(result.outputs)
    if result.status == RequestStatus.awaiting_approval:
        print(f"\nNext: python -m agent.main approve {result.request_id} --approver \"<your name>\"")
    return EXIT_OK if result.status in (RequestStatus.awaiting_approval, RequestStatus.deployed) else EXIT_NEEDS_USER


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_catalog(args, settings: Settings) -> int:
    if args.action == "build":
        existing = load_catalog(settings.catalog_path) if settings.catalog_path.exists() else None
        root = settings.modules_root
        if not root.is_dir():
            print(f"Modules root not found: {root}", file=sys.stderr)
            return EXIT_ERROR
        catalog, warnings = build_catalog(root, existing)
        save_catalog(catalog, settings.catalog_path)
        print(f"Catalog written to {settings.catalog_path} ({len(catalog.modules)} modules)")
        for w in warnings:
            print(f"  warning: {w}")
        return EXIT_OK
    catalog = load_catalog(settings.catalog_path)
    if args.action == "list":
        for key in sorted(catalog.modules):
            m = catalog.modules[key]
            print(f"{'  ' if m.enabled else 'x '}{key:32} {', '.join(m.variables)}")
        return EXIT_OK
    if args.action == "show":
        if args.module not in catalog.modules:
            print(f"Unknown module {args.module}", file=sys.stderr)
            return EXIT_ERROR
        print(catalog.render_spec(args.module))
        return EXIT_OK
    return EXIT_ERROR


def cmd_schemas(args, settings: Settings) -> int:
    out = PROJECT_ROOT / "schemas"
    out.mkdir(exist_ok=True)
    targets = {
        "infrastructure-request.json": InfrastructureRequest,
        "parsed-requirements.json": ParsedRequirements,
        "generated-config.json": GeneratedConfig,
        "review-result.json": ReviewResult,
        "repair-patch.json": RepairPatch,
    }
    for name, model in targets.items():
        (out / name).write_text(json.dumps(model.model_json_schema(), indent=2) + "\n", encoding="utf-8")
        print(f"wrote schemas/{name}")
    return EXIT_OK


def cmd_check_llm(args, settings: Settings) -> int:
    from .llm import create_llm

    try:
        info = create_llm(settings).health()
    except Exception as exc:  # noqa: BLE001
        print(f"LLM check failed ({settings.llm_provider}): {exc}", file=sys.stderr)
        return EXIT_ERROR
    _print(info)
    return EXIT_ERROR if info["missing"] else EXIT_OK


def cmd_validate_request(args, settings: Settings) -> int:
    try:
        req = load_request(Path(args.file), settings)
    except ValidationError as exc:
        return _report_validation(exc)
    print("Request is valid.")
    _print(req.model_dump(mode="json"))
    return EXIT_OK


def cmd_run(args, settings: Settings) -> int:
    from .pipeline import NeedsUser, Pipeline

    try:
        req = load_request(Path(args.file), settings)
    except ValidationError as exc:
        return _report_validation(exc)
    try:
        pipeline = Pipeline(settings)
        result = pipeline.run(req)
    except NeedsUser as exc:
        if getattr(args, "json", False):
            _print({
                "error": str(exc),
                "details": exc.details,
                "status": getattr(exc, "status", RequestStatus.needs_user).value,
            })
            return EXIT_NEEDS_USER
        print(f"Stopped: {exc}", file=sys.stderr)
        for d in exc.details:
            print(f"  - {d}", file=sys.stderr)
        return EXIT_NEEDS_USER
    if getattr(args, "json", False):
        _print({
            "request_id": result.request_id,
            "status": result.status.value,
            "message": result.message,
            "details": result.details,
            "plan_summary": result.plan_summary,
            "outputs": result.outputs,
            "request_dir": result.request_dir,
        })
        return EXIT_OK if result.status in (RequestStatus.awaiting_approval, RequestStatus.deployed) else EXIT_NEEDS_USER
    return _print_result(result)


def cmd_status(args, settings: Settings) -> int:
    from .pipeline import Pipeline

    _print(Pipeline(settings).status(args.request_id))
    return EXIT_OK


def cmd_approve(args, settings: Settings) -> int:
    from .pipeline import Pipeline

    pipeline = Pipeline(settings)
    status = pipeline.status(args.request_id)
    if status["status"] != RequestStatus.awaiting_approval.value:
        print(f"Request is {status['status']}; nothing to approve.", file=sys.stderr)
        return EXIT_ERROR
    summary = status.get("plan_summary") or {}
    if not getattr(args, "json", False):
        print(f"Plan for request {args.request_id}:")
        print("  " + ", ".join(f"{k}={v}" for k, v in (summary.get("counts") or {}).items() if v))
        for ch in summary.get("changes", []):
            print(f"   {ch['action']:>8}  {ch['address']}")
        print(f"\nFull plan: {Path(status['request_dir']) / 'plan.json'}")
    if not getattr(args, "yes", False):
        if not sys.stdin.isatty():
            print("Approval must be given interactively from a terminal or with --yes.", file=sys.stderr)
            return EXIT_ERROR
        typed = input(f"\nType the request id ({args.request_id}) to approve this plan: ").strip()
        if typed != args.request_id:
            print("Confirmation did not match. Not approved.", file=sys.stderr)
            return EXIT_ERROR
    try:
        token, _ = pipeline.approve(args.request_id, args.approver)
    except ApprovalError as exc:
        print(f"Approval failed: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if getattr(args, "json", False):
        _print({"request_id": args.request_id, "token": token, "approver": args.approver})
        return EXIT_OK
    print("\nApproval token (single use, bound to this exact plan):\n")
    print(token)
    print(f"\nApply with: python -m agent.main apply {args.request_id} --token <token>"
          "\n(or set INFRA_AGENT_APPROVAL_TOKEN and omit --token)")
    return EXIT_OK


def cmd_apply(args, settings: Settings) -> int:
    from .pipeline import Pipeline

    token = args.token or os.environ.get("INFRA_AGENT_APPROVAL_TOKEN") or getpass.getpass("Approval token: ")
    try:
        result = Pipeline(settings).apply(args.request_id, token)
    except ApprovalError as exc:
        if getattr(args, "json", False):
            _print({"error": str(exc), "status": "FAILED"})
            return EXIT_ERROR
        print(f"Apply refused: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if getattr(args, "json", False):
        _print({
            "request_id": result.request_id,
            "status": result.status.value,
            "message": result.message,
            "details": result.details,
            "plan_summary": result.plan_summary,
            "outputs": result.outputs,
            "request_dir": result.request_dir,
        })
        return EXIT_OK if result.status in (RequestStatus.awaiting_approval, RequestStatus.deployed) else EXIT_NEEDS_USER
    return _print_result(result)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="infra-agent", description="Azure Terraform infra agent")
    p.add_argument("--config", help="Path to config.yaml (default: project config.yaml or INFRA_AGENT_CONFIG)")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("catalog", help="Build or inspect the module catalog")
    c.add_argument("action", choices=["build", "list", "show"])
    c.add_argument("module", nargs="?")
    c.set_defaults(func=cmd_catalog)

    s = sub.add_parser("schemas", help="Export JSON schemas for all contracts")
    s.set_defaults(func=cmd_schemas)

    o = sub.add_parser("check-llm", aliases=["check-ollama"],
                       help="Verify the enabled LLM provider (Ollama or Azure AI Foundry) and its models")
    o.set_defaults(func=cmd_check_llm)

    v = sub.add_parser("validate-request", help="Validate a request file without contacting any LLM")
    v.add_argument("-f", "--file", required=True)
    v.set_defaults(func=cmd_validate_request)

    r = sub.add_parser("run", help="Parse -> generate -> review -> validate -> plan (stops at approval)")
    r.add_argument("-f", "--file", required=True)
    r.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    r.set_defaults(func=cmd_run)

    st = sub.add_parser("status", help="Show the status of a request")
    st.add_argument("request_id")
    st.set_defaults(func=cmd_status)

    a = sub.add_parser("approve", help="Human approval gate: issue a single-use apply token")
    a.add_argument("request_id")
    a.add_argument("--approver", required=True)
    a.add_argument("--yes", "-y", action="store_true", help="Non-interactive approval confirmation")
    a.add_argument("--json", action="store_true", help="Output JSON with token and approver")
    a.set_defaults(func=cmd_approve)

    ap = sub.add_parser("apply", help="Apply the saved plan (requires an approval token)")
    ap.add_argument("request_id")
    ap.add_argument("--token")
    ap.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    ap.set_defaults(func=cmd_apply)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings(args.config)
    except (OSError, ValidationError) as exc:
        print(f"Invalid configuration: {exc}", file=sys.stderr)
        return EXIT_ERROR
    try:
        return args.func(args, settings)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_ERROR
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
