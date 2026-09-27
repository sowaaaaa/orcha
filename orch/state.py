"""Состояние запуска в .orch/runs/<run_id>/state.json — для продолжения после сбоя."""
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class RunState:
    run_id: str
    task: str
    base: str = ""
    branch: str = ""
    status: str = "planning"  # planning | running | finished | cancelled
    qa: list = field(default_factory=list)
    plan: dict = field(default_factory=dict)
    full_test_command: str | None = None
    tasks: dict = field(default_factory=dict)  # id -> {"status", "reason", "attempts", "detail"}
    final_green: bool | None = None


def run_dir(repo, run_id: str) -> Path:
    return Path(repo) / ".orch" / "runs" / run_id


def save_state(repo, state: RunState) -> None:
    d = run_dir(repo, state.run_id)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "state.json.tmp"
    tmp.write_text(json.dumps(asdict(state), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, d / "state.json")


def load_state(repo, run_id: str) -> RunState:
    f = run_dir(repo, run_id) / "state.json"
    if not f.exists():
        raise FileNotFoundError(f"запуск {run_id} не найден")
    return RunState(**json.loads(f.read_text(encoding="utf-8")))


def list_runs(repo) -> list[RunState]:
    root = Path(repo) / ".orch" / "runs"
    if not root.is_dir():
        return []
    return [load_state(repo, d.name) for d in sorted(root.iterdir()) if (d / "state.json").exists()]
