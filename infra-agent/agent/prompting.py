"""Prompt template loading. Templates use {{name}} placeholders and two sections."""

from __future__ import annotations

from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


def render_prompt(name: str, **values: str) -> tuple[str, str]:
    text = (PROMPT_DIR / f"{name}.txt").read_text(encoding="utf-8")
    try:
        _, rest = text.split("=== SYSTEM ===", 1)
        system, user = rest.split("=== USER ===", 1)
    except ValueError as exc:
        raise ValueError(f"prompt {name}.txt must contain '=== SYSTEM ===' and '=== USER ===' sections") from exc
    for key, value in values.items():
        system = system.replace("{{" + key + "}}", str(value))
        user = user.replace("{{" + key + "}}", str(value))
    return system.strip(), user.strip()
