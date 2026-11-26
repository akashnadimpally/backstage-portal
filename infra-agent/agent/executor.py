"""Terraform CLI tool + static validation controller.

The executor is the only component that runs commands. It never uses a
shell, always runs inside the request directory and records every command's
output under ``logs/``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .models import Diagnostic
from .settings import Settings
from .storage import RequestStore

_TEXT_LOCATION_RE = re.compile(r'on (\S+) line (\d+)(?:, in (?:module|resource|output|variable) "([^"]+)")?')
_BOX_CHARS = re.compile(r"^[\u2502\u2577\u2575]\s?", re.M)


@dataclass
class CommandResult:
    step: str
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    log_file: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass
class StepOutcome:
    ok: bool
    stage: str
    diagnostics: list[Diagnostic] = field(default_factory=list)
    warnings: list[Diagnostic] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Diagnostic parsing
# --------------------------------------------------------------------------- #
def _from_json_diag(stage: str, d: dict) -> Diagnostic:
    rng = d.get("range") or {}
    snippet = d.get("snippet") or {}
    return Diagnostic(
        stage=stage,
        severity="error" if d.get("severity") == "error" else "warning",
        summary=str(d.get("summary", "")).strip() or "unknown error",
        detail=str(d.get("detail", "")).strip(),
        filename=rng.get("filename"),
        line=(rng.get("start") or {}).get("line"),
        address=d.get("address"),
        snippet=" ".join(filter(None, [snippet.get("context"), snippet.get("code")])) or None,
    )


def parse_json_stream(stage: str, text: str) -> list[Diagnostic]:
    """Parse ``terraform plan/apply -json`` line-delimited output."""
    diags = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("type") == "diagnostic" and isinstance(msg.get("diagnostic"), dict):
            diags.append(_from_json_diag(stage, msg["diagnostic"]))
    return diags


def parse_validate_json(text: str) -> list[Diagnostic]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    return [_from_json_diag("validate", d) for d in data.get("diagnostics", []) or []]


def parse_text_errors(stage: str, text: str) -> list[Diagnostic]:
    clean = _BOX_CHARS.sub("", text)
    diags = []
    matches = list(re.finditer(r"^Error: (.+)$", clean, flags=re.M))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(clean)
        body = clean[m.end():end].strip()
        loc = _TEXT_LOCATION_RE.search(body)
        diags.append(Diagnostic(
            stage=stage,
            summary=m.group(1).strip(),
            detail=body[:4000],
            filename=loc.group(1).rstrip(",") if loc else None,
            line=int(loc.group(2)) if loc else None,
            address=f"module.{loc.group(3)}" if loc and loc.group(3) and "in module" in loc.group(0) else None,
        ))
    return diags


# --------------------------------------------------------------------------- #
# Executor
# --------------------------------------------------------------------------- #
class TerraformExecutor:
    def __init__(self, settings: Settings, store: RequestStore, subscription_id: str | None):
        self.settings = settings
        self.store = store
        self.subscription_id = subscription_id
        self._seq = len(list((store.dir / "logs").glob("*.log"))) if (store.dir / "logs").exists() else 0

    @property
    def tf(self) -> str:
        return self.settings.terraform.binary

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        cache = self.settings.plugin_cache_dir
        cache.mkdir(parents=True, exist_ok=True)
        env.update({
            "TF_IN_AUTOMATION": "1",
            "TF_INPUT": "0",
            "CHECKPOINT_DISABLE": "1",
            "TF_PLUGIN_CACHE_DIR": str(cache),
        })
        if self.subscription_id:
            # Modules that declare their own provider block read the subscription from the environment.
            env["ARM_SUBSCRIPTION_ID"] = self.subscription_id
        return env

    def run(self, step: str, args: list[str], timeout: int) -> CommandResult:
        self._seq += 1
        self.store.audit("command.start", actor="executor", step=step, args=args)
        try:
            proc = subprocess.run(
                args, cwd=self.store.dir, env=self.env(), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout, shell=False,
            )
            result = CommandResult(step, args, proc.returncode, proc.stdout, proc.stderr)
        except FileNotFoundError:
            result = CommandResult(step, args, 127, "", f"Error: executable file not found: {args[0]}")
        except subprocess.TimeoutExpired as exc:
            result = CommandResult(step, args, 124, exc.stdout or "", f"Error: context deadline exceeded after {timeout}s")
        log_name = f"logs/{self._seq:03d}-{step}.log"
        self.store.write_text(
            log_name,
            f"$ {' '.join(args)}\nexit={result.returncode}\n\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}\n",
        )
        result.log_file = log_name
        self.store.audit("command.end", actor="executor", step=step, returncode=result.returncode, log=log_name)
        return result

    # ------------------------------ terraform ------------------------------ #
    def fmt(self) -> StepOutcome:
        self.run("fmt", [self.tf, "fmt", "-no-color"], 120)
        check = self.run("fmt-check", [self.tf, "fmt", "-check", "-no-color"], 120)
        if check.ok:
            return StepOutcome(True, "fmt")
        return StepOutcome(False, "fmt", [Diagnostic(stage="fmt", summary="terraform fmt -check failed",
                                                     detail=(check.stdout + check.stderr)[:2000])])

    def init(self) -> StepOutcome:
        res = self.run("init", [self.tf, "init", "-input=false", "-no-color"], self.settings.terraform.timeouts.init)
        if res.ok:
            return StepOutcome(True, "init")
        diags = parse_text_errors("init", res.stderr + "\n" + res.stdout) or [
            Diagnostic(stage="init", summary="terraform init failed", detail=(res.stderr or res.stdout)[-3000:])
        ]
        return StepOutcome(False, "init", diags)

    def validate(self) -> StepOutcome:
        res = self.run("validate", [self.tf, "validate", "-json", "-no-color"],
                       self.settings.terraform.timeouts.validation)
        diags = parse_validate_json(res.stdout)
        errors = [d for d in diags if d.severity == "error"]
        warnings = [d for d in diags if d.severity == "warning"]
        if res.ok and not errors:
            return StepOutcome(True, "validate", warnings=warnings)
        if not errors:
            errors = parse_text_errors("validate", res.stderr) or [
                Diagnostic(stage="validate", summary="terraform validate failed", detail=res.stderr[-3000:])]
        return StepOutcome(False, "validate", errors, warnings)

    def plan(self) -> StepOutcome:
        (self.store.dir / "tfplan").unlink(missing_ok=True)
        res = self.run("plan", [self.tf, "plan", "-input=false", "-no-color", "-json", "-out=tfplan"],
                       self.settings.terraform.timeouts.plan)
        diags = parse_json_stream("plan", res.stdout)
        errors = [d for d in diags if d.severity == "error"]
        if res.ok and not errors:
            show = self.run("show", [self.tf, "show", "-json", "-no-color", "tfplan"], 300)
            if not show.ok:
                return StepOutcome(False, "plan", [Diagnostic(stage="plan", summary="terraform show failed",
                                                              detail=show.stderr[-3000:])])
            self.store.write("plan.json", json.loads(show.stdout))
            return StepOutcome(True, "plan", warnings=[d for d in diags if d.severity == "warning"])
        if not errors:
            errors = parse_text_errors("plan", res.stderr) or [
                Diagnostic(stage="plan", summary="terraform plan failed", detail=(res.stderr or res.stdout)[-3000:])]
        return StepOutcome(False, "plan", errors)

    def apply(self) -> StepOutcome:
        res = self.run("apply", [self.tf, "apply", "-input=false", "-no-color", "-json", "tfplan"],
                       self.settings.terraform.timeouts.apply)
        diags = parse_json_stream("apply", res.stdout)
        errors = [d for d in diags if d.severity == "error"]
        if res.ok and not errors:
            return StepOutcome(True, "apply")
        if not errors:
            errors = parse_text_errors("apply", res.stderr) or [
                Diagnostic(stage="apply", summary="terraform apply failed", detail=(res.stderr or res.stdout)[-3000:])]
        return StepOutcome(False, "apply", errors)

    def outputs(self) -> dict:
        res = self.run("output", [self.tf, "output", "-json", "-no-color"], 300)
        if not res.ok:
            return {}
        data = json.loads(res.stdout or "{}")
        return {k: ("<sensitive>" if v.get("sensitive") else v.get("value")) for k, v in data.items()}

    # ------------------------------ linters -------------------------------- #
    def lint(self) -> StepOutcome:
        cfg = self.settings.static_checks.tflint
        if not cfg.enabled:
            return StepOutcome(True, "lint", skipped=["tflint disabled"])
        if not shutil.which("tflint"):
            return self._missing_tool("lint", "tflint", cfg.required)
        res = self.run("tflint", ["tflint", "--format=json", "--no-color"], 600)
        try:
            data = json.loads(res.stdout or "{}")
        except json.JSONDecodeError:
            data = {"errors": [{"message": res.stderr or res.stdout}]}
        diags = []
        for issue in data.get("issues", []) or []:
            sev = (issue.get("rule") or {}).get("severity", "warning")
            rng = issue.get("range") or {}
            diags.append(Diagnostic(
                stage="lint",
                severity="error" if sev == "error" and cfg.fail_on_findings else "warning",
                summary=f"{(issue.get('rule') or {}).get('name', 'tflint')}: {issue.get('message', '')}",
                filename=rng.get("filename"), line=(rng.get("start") or {}).get("line"),
            ))
        for err in data.get("errors", []) or []:
            diags.append(Diagnostic(stage="lint", summary="tflint error", detail=str(err.get("message", ""))[:2000]))
        errors = [d for d in diags if d.severity == "error"]
        return StepOutcome(not errors, "lint", errors, [d for d in diags if d.severity == "warning"])

    def security_scan(self) -> StepOutcome:
        cfg = self.settings.static_checks.security
        if not cfg.enabled or cfg.tool == "none":
            return StepOutcome(True, "security", skipped=["security scan disabled"])
        if not shutil.which(cfg.tool):
            return self._missing_tool("security", cfg.tool, cfg.required)
        commands = {
            "checkov": ["checkov", "-d", ".", "--framework", "terraform", "-o", "json", "--quiet", "--compact"],
            "trivy": ["trivy", "config", "--format", "json", "--exit-code", "0", "."],
            "tfsec": ["tfsec", ".", "--format", "json", "--soft-fail"],
        }
        res = self.run(f"security-{cfg.tool}", commands[cfg.tool], 900)
        findings = self._parse_security(cfg.tool, res.stdout)
        severity = "error" if cfg.fail_on_findings else "warning"
        diags = [Diagnostic(stage="security", severity=severity, summary=f, detail="") for f in findings]
        errors = [d for d in diags if d.severity == "error"]
        return StepOutcome(not errors, "security", errors, [d for d in diags if d.severity == "warning"])

    @staticmethod
    def _parse_security(tool: str, stdout: str) -> list[str]:
        try:
            data = json.loads(stdout or "{}")
        except json.JSONDecodeError:
            return []
        out: list[str] = []
        if tool == "checkov":
            reports = data if isinstance(data, list) else [data]
            for rep in reports:
                for chk in (rep.get("results") or {}).get("failed_checks", []) or []:
                    out.append(f"{chk.get('check_id')}: {chk.get('check_name')} ({chk.get('resource')})")
        elif tool == "trivy":
            for result in data.get("Results", []) or []:
                for mis in result.get("Misconfigurations", []) or []:
                    if mis.get("Severity") in ("HIGH", "CRITICAL"):
                        out.append(f"{mis.get('ID')}: {mis.get('Title')} [{mis.get('Severity')}]")
        elif tool == "tfsec":
            for r in data.get("results", []) or []:
                if r.get("severity") in ("HIGH", "CRITICAL"):
                    out.append(f"{r.get('rule_id')}: {r.get('description')} [{r.get('severity')}]")
        return out

    def _missing_tool(self, stage: str, tool: str, required: bool) -> StepOutcome:
        if required:
            return StepOutcome(False, stage, [Diagnostic(stage="init", summary=f"executable file not found: {tool}",
                                                         detail=f"{tool} is required by config.yaml but is not installed")])
        self.store.audit("check.skipped", actor="executor", stage=stage, reason=f"{tool} not installed")
        return StepOutcome(True, stage, skipped=[f"{tool} not installed"])


class StaticValidationController:
    """fmt -> init -> validate -> lint -> security scan. Stops at the first failing step."""

    def __init__(self, executor: TerraformExecutor):
        self.executor = executor

    def run(self) -> StepOutcome:
        warnings: list[Diagnostic] = []
        skipped: list[str] = []
        for step in (self.executor.fmt, self.executor.init, self.executor.validate,
                     self.executor.lint, self.executor.security_scan):
            outcome = step()
            warnings += outcome.warnings
            skipped += outcome.skipped
            if not outcome.ok:
                outcome.warnings = warnings
                outcome.skipped = skipped
                return outcome
        return StepOutcome(True, "static", warnings=warnings, skipped=skipped)


def plan_file(store: RequestStore) -> Path:
    return store.dir / "tfplan"
