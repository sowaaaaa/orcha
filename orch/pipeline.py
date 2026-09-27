"""Дирижёр: опрос → план → параллельные воркеры в worktree → слияние с проверкой регрессий."""
import threading
from datetime import datetime
from pathlib import Path

from . import git_ops, prompts
from .config import Config, load_config
from .gates import run_tests, tail
from .llm import extract_json
from .plan import parse_plan
from .scheduler import run_dag
from .state import RunState, load_state, run_dir, save_state
from .worker import TaskOutcome, run_task

PLAN_ATTEMPTS = 2
MAX_QUESTIONS = 7

REASONS = {
    "red_gate": "тесты не удалось сделать красными до кода",
    "tests_red": "тесты не позеленели",
    "review": "ревью не приняло результат",
    "merge_conflict": "конфликт при слиянии",
    "regression": "сломала другие тесты — слияние откатано",
    "setup": "не удалась подготовка копии проекта",
    "error": "ошибка",
    "dependency": "не выполнена зависимость",
}


class OrchError(RuntimeError):
    pass


class Orchestrator:
    def __init__(self, repo, llm, ui, cfg: Config | None = None):
        self.repo = Path(repo)
        self.llm = llm
        self.ui = ui
        self.cfg = cfg or load_config(repo)
        self.state: RunState | None = None
        self._save_lock = threading.Lock()

    # ---------- публичные команды ----------

    def run(self, task_text: str, interview: bool = True) -> RunState:
        self.repo = git_ops.ensure_repo(self.repo)
        git_ops.exclude(self.repo, ".orch/")
        state = self.state = RunState(run_id=self._new_run_id(), task=task_text, base=git_ops.head(self.repo))
        state.branch = f"orch/{state.run_id}/main"

        if interview:
            state.qa = self._interview(task_text)
        data, plan = self._plan(state)
        state.plan = data
        state.full_test_command = self.cfg.full_test_command or plan.full_test_command
        state.tasks = {t.id: {"status": "pending", "reason": "", "attempts": 0, "detail": ""} for t in plan.tasks}
        self._save()

        if not self.ui.confirm(render_plan(plan, state.full_test_command)):
            state.status = "cancelled"
            self._save()
            return state
        return self._execute(plan)

    def resume(self, run_id: str) -> RunState:
        self.repo = git_ops.ensure_repo(self.repo)
        try:
            state = self.state = load_state(self.repo, run_id)
        except FileNotFoundError as e:
            raise OrchError(str(e))
        for entry in state.tasks.values():
            if entry["status"] != "done":
                entry.update(status="pending", reason="", detail="")
        return self._execute(parse_plan(state.plan))

    # ---------- опрос и план (мозг) ----------

    def _interview(self, task_text: str) -> list:
        reply = self.llm.ask("interview", prompts.interview(task_text), self.repo)
        try:
            questions = extract_json(reply.text).get("questions") or []
        except ValueError:
            self.ui.info("Мозг не вернул вопросы — пропускаю опрос.")
            return []
        return [[str(q), self.ui.ask(str(q))] for q in questions[:MAX_QUESTIONS]]

    def _plan(self, state: RunState):
        error = None
        for _ in range(PLAN_ATTEMPTS):
            prompt = prompts.plan(state.task, state.qa, self.cfg.full_test_command, error)
            reply = self.llm.ask("plan", prompt, self.repo)
            try:
                data = extract_json(reply.text)
                return data, parse_plan(data)
            except ValueError as e:  # PlanError тоже ValueError
                error = str(e)
                self.ui.info(f"План отклонён: {error}")
        raise OrchError(f"мозг не смог составить корректный план: {error}")

    # ---------- выполнение (дирижёр) ----------

    def _execute(self, plan) -> RunState:
        state, cfg = self.state, self.cfg
        self._spec = plan.spec
        self._main = run_dir(self.repo, state.run_id) / "main"
        if git_ops.branch_exists(self.repo, state.branch):
            git_ops.add_worktree(self.repo, self._main, state.branch)
        else:
            git_ops.add_worktree(self.repo, self._main, state.branch, base=state.base)
        for name in cfg.link:
            git_ops.exclude(self.repo, f"/{name}")
        state.status = "running"
        self._save()

        # Проверка регрессий включается, как только полный набор тестов зелёный.
        self._gate = False
        if state.full_test_command:
            base = run_tests(state.full_test_command, self._main, cfg.test_timeout)
            self._gate = base.passed
            if not base.passed:
                self.ui.info("Внимание: полный набор тестов красный ещё до начала работы — проверка регрессий "
                             "включится, когда он станет зелёным.\n" + tail(base.output, 1500))

        done = {tid for tid, e in state.tasks.items() if e["status"] == "done"}
        final = run_dag(plan.tasks, self._work, self._finish, cfg.parallel, done=done, on_start=self._start)
        for tid, status in final.items():
            entry = state.tasks[tid]
            if status == "skipped":
                entry.update(status="skipped", reason="dependency")
            elif status == "failed" and entry["status"] != "failed":
                entry.update(status="failed", reason="error")

        if state.full_test_command:
            state.final_green = run_tests(state.full_test_command, self._main, cfg.test_timeout).passed
        state.status = "finished"
        self._save()
        git_ops.remove_worktree(self.repo, self._main)
        self.ui.info(render_summary(state))
        return state

    def _paths(self, tid):
        return run_dir(self.repo, self.state.run_id) / "wt" / tid, f"orch/{self.state.run_id}/{tid}"

    def _start(self, task) -> None:
        # В основном потоке: worktree создаётся от интеграционной ветки, где уже слиты зависимости.
        wt, branch = self._paths(task.id)
        git_ops.add_worktree(self.repo, wt, branch, base=git_ops.head(self._main))
        git_ops.link_dirs(self.repo, wt, self.cfg.link)
        self.state.tasks[task.id].update(status="running")
        self._save()
        self.ui.info(f"▶ {task.id}: {task.title}")

    def _work(self, task) -> TaskOutcome:
        # В потоке пула: своя копия проекта, свой воркер.
        wt, _ = self._paths(task.id)
        try:
            if self.cfg.setup_command:
                r = run_tests(self.cfg.setup_command, wt, self.cfg.test_timeout)
                if not r.passed:
                    return TaskOutcome(False, "setup", 0, tail(r.output, 2000))
            return run_task(task, self._spec, wt, self.llm, self.cfg,
                            log=lambda msg: self.ui.info(f"  [{task.id}] {msg}"))
        except Exception as e:
            return TaskOutcome(False, "error", 0, str(e))

    def _finish(self, task, outcome: TaskOutcome) -> bool:
        # В основном потоке, по одной задаче: слияние и проверка регрессий.
        entry = self.state.tasks[task.id]
        entry["attempts"] = outcome.attempts
        wt, branch = self._paths(task.id)
        if not outcome.ok:
            return self._fail(task, outcome.reason, outcome.detail)
        if not git_ops.merge(self._main, branch, f"orch: {task.id} — {task.title}"):
            return self._fail(task, "merge_conflict")
        full = self.state.full_test_command
        if full:
            r = run_tests(full, self._main, self.cfg.test_timeout)
            if r.passed:
                self._gate = True
            elif self._gate:
                git_ops.undo_last_merge(self._main)
                return self._fail(task, "regression", tail(r.output, 2000))
        git_ops.remove_worktree(self.repo, wt)
        git_ops.delete_branch(self.repo, branch)
        entry.update(status="done", reason="", detail="")
        self._save()
        self.ui.info(f"✔ {task.id}: слито (попыток: {outcome.attempts})")
        return True

    def _fail(self, task, reason: str, detail: str = "") -> bool:
        self.state.tasks[task.id].update(status="failed", reason=reason, detail=detail[-2000:])
        self._save()
        wt, _ = self._paths(task.id)
        self.ui.info(f"✘ {task.id}: {REASONS.get(reason, reason)}. Копия для разбора: {wt}")
        return False

    # ---------- служебное ----------

    def _new_run_id(self) -> str:
        base = datetime.now().strftime("%Y%m%d-%H%M%S")
        run_id, n = base, 1
        while run_dir(self.repo, run_id).exists():
            n += 1
            run_id = f"{base}-{n}"
        return run_id

    def _save(self) -> None:
        with self._save_lock:
            save_state(self.repo, self.state)


def render_plan(plan, full_test_command) -> str:
    lines = ["", "=== Спецификация ===", plan.spec.strip(), "", "=== План ==="]
    for t in plan.tasks:
        deps = f" (после: {', '.join(t.depends_on)})" if t.depends_on else ""
        lines.append(f"[{t.id}] {t.title}{deps}")
        lines.append(f"    тесты: {', '.join(t.test_files)} → {t.test_command}")
        for a in t.acceptance:
            lines.append(f"    ✓ {a}")
    lines.append(f"Полный набор тестов: {full_test_command or '(нет — регрессии не проверяются)'}")
    return "\n".join(lines)


def render_summary(state: RunState) -> str:
    marks = {"done": "✔", "failed": "✘", "skipped": "–"}
    lines = ["", "=== Итог ==="]
    for tid, e in state.tasks.items():
        note = "" if e["status"] == "done" else f" — {REASONS.get(e['reason'], e['reason'])}"
        lines.append(f"{marks.get(e['status'], '?')} {tid}: {e['status']}{note} (попыток: {e['attempts']})")
    if state.final_green is not None:
        lines.append(f"Полный набор тестов: {'зелёный' if state.final_green else 'КРАСНЫЙ'}")
    lines.append(f"Результат в ветке {state.branch}")
    lines.append(f"  посмотреть: git log --oneline HEAD..{state.branch}")
    lines.append(f"  влить:      git merge {state.branch}")
    if any(e["status"] != "done" for e in state.tasks.values()):
        lines.append(f"  доделать:   orch resume {state.run_id}")
    return "\n".join(lines)
