"""Командная строка: orch run | resume | status."""
import argparse
import json
import sys
import threading
from pathlib import Path

from .bootstrap import init_project, install_skill
from .config import ConfigError, load_config
from .git_ops import GitError
from .llm import ClaudeCLI, LLMError
from .pipeline import OrchError, Orchestrator
from .plan import PlanError
from .state import list_runs, load_state


class ConsoleUI:
    def __init__(self, yes: bool = False):
        self.yes = yes
        self._lock = threading.Lock()

    def ask(self, question: str) -> str:
        print(f"\n? {question}")
        try:
            return input("> ").strip()
        except EOFError:
            return ""

    def confirm(self, text: str) -> bool:
        print(text)
        if self.yes:
            return True
        try:
            return input("\nЗапускать? [y/N] ").strip().lower() in ("y", "yes", "д", "да")
        except EOFError:
            return False

    def info(self, msg: str) -> None:
        with self._lock:  # воркеры пишут из разных потоков
            print(msg, flush=True)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="orch", description="Оркестратор: опрос → план → тесты до кода → параллельные воркеры")
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="выполнить новую задачу")
    run.add_argument("task", help="описание задачи")
    run.add_argument("--no-interview", action="store_true", help="без уточняющих вопросов")
    run.add_argument("--plan", help="готовый план (JSON-файл), например составленный в чате /orch")

    resume = sub.add_parser("resume", help="доделать незавершённые подзадачи запуска")
    resume.add_argument("run_id")

    for sp in (run, resume):
        sp.add_argument("--yes", "-y", action="store_true", help="не спрашивать подтверждение плана")
        sp.add_argument("--brain", help="модель мозга (иначе из orch.toml)")
        sp.add_argument("--worker", help="модель воркеров (иначе из orch.toml)")

    status = sub.add_parser("status", help="список запусков")
    status.add_argument("run_id", nargs="?")

    for sp in (run, resume, status):
        sp.add_argument("--repo", default=".", help="папка проекта (по умолчанию текущая)")

    init = sub.add_parser("init", help="подготовить папку проекта: git, первый коммит, .gitignore, orch.toml")
    init.add_argument("path", nargs="?", default=".")

    skill = sub.add_parser("install-skill", help="установить навык /orch для Claude Code")
    skill.add_argument("--dir", help="папка навыков (по умолчанию ~/.claude/skills)")
    return p


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    if args.cmd == "init":
        return _init(Path(args.path))
    if args.cmd == "install-skill":
        dst = install_skill(args.dir)
        print(f"Навык установлен: {dst}\nВ Claude Code откройте папку проекта и напишите: /orch <что сделать>")
        return 0
    repo = Path(args.repo).resolve()
    if args.cmd == "status":
        return _status(repo, args.run_id)

    orch = None
    try:
        cfg = load_config(repo)
        if args.brain:
            cfg.brain_model = args.brain
        if args.worker:
            cfg.worker_model = args.worker
        llm = ClaudeCLI({"brain": cfg.brain_model, "worker": cfg.worker_model}, timeout=cfg.llm_timeout)
        orch = Orchestrator(repo, llm, ConsoleUI(yes=args.yes), cfg)
        if args.cmd == "run":
            plan = _load_plan(args.plan) if args.plan else None
            state = orch.run(args.task, interview=not args.no_interview, plan=plan)
        else:
            state = orch.resume(args.run_id)
    except (OrchError, GitError, LLMError, ConfigError, PlanError) as e:
        print(f"Ошибка: {e}")
        return 1
    except KeyboardInterrupt:
        hint = f" Продолжить: orch resume {orch.state.run_id}" if orch and orch.state else ""
        print(f"\nПрервано.{hint}")
        return 130
    if state.status == "cancelled":
        print("Отменено.")
        return 0
    return 0 if all(e["status"] == "done" for e in state.tasks.values()) else 2


def _status(repo: Path, run_id: str | None) -> int:
    try:
        runs = [load_state(repo, run_id)] if run_id else list_runs(repo)
    except FileNotFoundError as e:
        print(f"Ошибка: {e}")
        return 1
    if not runs:
        print("Запусков нет.")
        return 0
    for s in runs:
        counts = {}
        for e in s.tasks.values():
            counts[e["status"]] = counts.get(e["status"], 0) + 1
        summary = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
        print(f"{s.run_id}  {s.status:<9}  {summary:<30}  {s.task[:60]}")
        if run_id:
            for tid, e in s.tasks.items():
                print(f"    {tid}: {e['status']} {e['reason']}")
                if e.get("detail"):
                    for line in e["detail"].strip().splitlines()[-15:]:
                        print(f"        {line}")
    return 0


def _load_plan(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))  # -sig: файл мог сохраниться с BOM
    except OSError as e:
        raise OrchError(f"не удалось прочитать план {path}: {e.strerror or e}")
    except ValueError as e:
        raise OrchError(f"план {path} — не JSON: {e}")


def _init(path: Path) -> int:
    try:
        actions = init_project(path)
    except GitError as e:
        print(f"Ошибка: {e}")
        return 1
    if actions:
        print("Готово:")
        for a in actions:
            print(f"  - {a}")
    else:
        print("Проект уже подготовлен — делать ничего не нужно.")
    print(f"Дальше: откройте {path.resolve()} в Claude Code и напишите /orch <что сделать>"
          " (или в терминале: orch run \"задача\").")
    return 0
