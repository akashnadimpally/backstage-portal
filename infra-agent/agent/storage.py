"""Per-request artifact storage and append-only audit log."""

from __future__ import annotations

import json
import os
import secrets
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .models import REQUEST_ID_RE, RequestState, RequestStatus, utcnow


def new_request_id() -> str:
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}"


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(_jsonable(data), fh, indent=2, default=str)
    os.replace(tmp, path)


class RequestStore:
    def __init__(self, base_dir: Path, request_id: str):
        if not REQUEST_ID_RE.fullmatch(request_id):
            raise ValueError(f"invalid request id {request_id!r}")
        self.request_id = request_id
        self.dir = (base_dir / request_id).resolve()
        if base_dir.resolve() not in self.dir.parents:
            raise ValueError("request directory escapes the generated directory")
        self._llm_seq = 0

    @classmethod
    def create(cls, base_dir: Path) -> "RequestStore":
        store = cls(base_dir, new_request_id())
        store.dir.mkdir(parents=True, exist_ok=False)
        store.save_state(RequestState(request_id=store.request_id))
        return store

    @classmethod
    def open(cls, base_dir: Path, request_id: str) -> "RequestStore":
        store = cls(base_dir, request_id)
        if not store.dir.is_dir():
            raise FileNotFoundError(f"request {request_id} not found under {base_dir}")
        llm_dir = store.dir / "llm"
        store._llm_seq = len(list(llm_dir.glob("*.json"))) if llm_dir.exists() else 0
        return store

    def path(self, name: str) -> Path:
        return self.dir / name

    def write(self, name: str, data: Any) -> Path:
        p = self.path(name)
        write_json(p, data)
        return p

    def write_text(self, name: str, text: str) -> Path:
        p = self.path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def read(self, name: str) -> Any:
        return json.loads(self.path(name).read_text(encoding="utf-8"))

    def exists(self, name: str) -> bool:
        return self.path(name).exists()

    def audit(self, event: str, actor: str = "system", **data: Any) -> None:
        record = {"ts": utcnow().isoformat(), "request_id": self.request_id, "actor": actor, "event": event,
                  **{k: _jsonable(v) for k, v in data.items()}}
        with open(self.path("audit.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")

    def record_llm_call(self, role: str, payload: dict) -> str:
        self._llm_seq += 1
        name = f"llm/{self._llm_seq:03d}-{role}.json"
        self.write(name, payload)
        return name

    def load_state(self) -> RequestState:
        return RequestState.model_validate(self.read("state.json"))

    def save_state(self, state: RequestState) -> None:
        state.updated_at = utcnow()
        self.write("state.json", state)

    def set_status(self, status: RequestStatus, message: str = "", **changes: Any) -> RequestState:
        state = self.load_state()
        state.status = status
        state.message = message
        for k, v in changes.items():
            setattr(state, k, v)
        self.save_state(state)
        self.audit("status", status=status.value, message=message)
        return state
