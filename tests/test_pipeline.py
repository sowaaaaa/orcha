"""Весь конвейер: опрос → план → параллельные воркеры в worktree → слияние → полный прогон.
Модель фейковая, всё остальное (git, worktree, запуск тестов) — настоящее."""
import pytest

from conftest import PY, FakeLLM, FakeUI, git, write
from orch.config import Config
from orch.pipeline import OrchError, Orchestrator
from orch.state import load_state

TESTS = {
    "add": ("check_add.py", "from calc_add import add\nassert add(2, 3) == 5\n"),
    "mul": ("check_mul.py", "from calc_mul import mul\nassert mul(2, 3) == 6\n"),
    "power": ("check_power.py", "from calc_power import power\nassert power(2, 3) == 8\n"),
}
CODE = {
    "add": ("calc_add.py", "def add(a, b):\n    return a + b\n"),
    "mul": ("calc_mul.py", "def mul(a, b):\n    return a * b\n"),
    "power": ("calc_power.py",
              "from calc_mul import mul\n\n\ndef power(a, n):\n    r = 1\n    for _ in range(n):\n"
              "        r = mul(r, a)\n    return r\n"),
}
APPROVE = {"approved": True, "issues": []}


def task(id, deps=()):
    return {
        "id": id, "title": f"Операция {id}", "description": f"Реализовать {id}",
        "depends_on": list(deps), "test_files": [TESTS[id][0]],
        "test_command": f"{PY} {TESTS[id][0]}", "acceptance": [f"{id} работает"],
    }


def plan(*tasks):
    return {"spec": "Калькулятор", "full_test_command": f"{PY} run_all.py", "tasks": list(tasks)}


PLAN = plan(task("add"), task("mul"), task("power", ["mul"]))
CHAIN = plan(task("add"), task("mul", ["add"]), task("power", ["mul"]))


class Scenario:
    """Фейковые воркеры: по имени папки worktree (= id задачи) пишут нужные файлы."""

    def __init__(self, code=None):
        self.code = dict(CODE, **(code or {}))
        self.seen = {}

    def tests(self, call):
        tid = call.cwd.name
        self.seen[tid] = sorted(p.name for p in call.cwd.glob("calc_*.py"))
        write(call.cwd, *TESTS[tid])
        return "тесты написаны"

    def write_code(self, call):
        action = self.code[call.cwd.name]
        if callable(action):
            return action(call)
        write(call.cwd, *action)
        return "Готово"

    def llm(self, plan_reply=PLAN, **over):
        script = dict(interview={"questions": ["Какие операции нужны?"]}, plan=plan_reply,
                      tests=self.tests, code=self.write_code, review=APPROVE)
        script.update(over)
        return FakeLLM(**script)


def statuses(state):
    return {k: v["status"] for k, v in state.tasks.items()}


def worktree_count(repo):
    return git(repo, "worktree", "list", "--porcelain").count("worktree ")


def test_full_run_merges_everything_into_integration_branch(repo):
    base = git(repo, "rev-parse", "HEAD")
    sc = Scenario()
    llm = sc.llm()
    ui = FakeUI(answers=["сложение, умножение, степень"])
    state = Orchestrator(repo, llm, ui, Config(parallel=2)).run("Сделай калькулятор")

    assert state.status == "finished"
    assert statuses(state) == {"add": "done", "mul": "done", "power": "done"}
    assert state.final_green is True
    # опрос: вопрос задан, ответ дошёл до планирования, план показан на подтверждение
    assert ui.questions == ["Какие операции нужны?"]
    assert "сложение, умножение, степень" in llm.of("plan")[0].prompt
    assert "power" in ui.confirmed[0]
    # зависимая задача стартовала, когда зависимость уже была слита
    assert "calc_mul.py" in sc.seen["power"]
    # результат — в интеграционной ветке
    for f in ("calc_add.py", "calc_mul.py", "calc_power.py", "check_power.py"):
        git(repo, "show", f"{state.branch}:{f}")
    # ветка и папка пользователя не тронуты, временные worktree убраны
    assert git(repo, "rev-parse", "HEAD") == base
    assert not (repo / "calc_add.py").exists()
    assert git(repo, "status", "--porcelain") == ""
    assert worktree_count(repo) == 1


def test_models_are_routed_by_role(repo):
    llm = Scenario().llm()
    Orchestrator(repo, llm, FakeUI(), Config(parallel=2)).run("Калькулятор")
    for c in llm.calls:
        if c.kind in ("interview", "plan"):
            assert c.cwd == repo
        else:  # воркеры и ревью работают в копии задачи, не в папке пользователя
            assert ".orch" in c.cwd.parts and c.cwd.name in ("add", "mul", "power")


def test_regression_is_rolled_back_and_dependents_skipped(repo):
    def mul_breaks_add(call):
        write(call.cwd, *CODE["mul"])
        write(call.cwd, "calc_add.py", "def add(a, b):\n    return a - b\n")  # ломает чужой код
        return "Готово"

    sc = Scenario(code={"mul": mul_breaks_add})
    state = Orchestrator(repo, sc.llm(CHAIN), FakeUI(), Config(parallel=2)).run("Калькулятор")

    assert statuses(state) == {"add": "done", "mul": "failed", "power": "skipped"}
    assert state.tasks["mul"]["reason"] == "regression"
    assert "return a + b" in git(repo, "show", f"{state.branch}:calc_add.py")
    with pytest.raises(Exception):
        git(repo, "show", f"{state.branch}:calc_mul.py")
    assert state.final_green is True


def test_failed_task_work_is_kept_in_branch_and_copy_removed(repo):
    # Копии упавших задач не остаются в проекте (иначе jest/vitest найдут в .orch чужие тесты),
    # а наработки сохраняются в ветке задачи.
    def wrong_mul(call):
        write(call.cwd, "calc_mul.py", "def mul(a, b):\n    return a + b\n")
        return "Готово"

    ui = FakeUI()
    sc = Scenario(code={"mul": wrong_mul})
    state = Orchestrator(repo, sc.llm(), ui, Config(parallel=2, max_code_attempts=1)).run("Калькулятор")
    assert state.tasks["mul"]["status"] == "failed"
    assert worktree_count(repo) == 1
    assert "return a + b" in git(repo, "show", f"orch/{state.run_id}/mul:calc_mul.py")
    assert any(f"orch/{state.run_id}/mul" in m for m in ui.messages)


def test_merge_conflict_fails_only_that_task(repo):
    def a_code(call):
        write(call.cwd, *CODE["add"])
        write(call.cwd, "README.md", "версия add\n")
        return "Готово"

    def m_code(call):
        write(call.cwd, *CODE["mul"])
        write(call.cwd, "README.md", "версия mul\n")
        return "Готово"

    sc = Scenario(code={"add": a_code, "mul": m_code})
    state = Orchestrator(repo, sc.llm(plan(task("add"), task("mul"))), FakeUI(), Config(parallel=2)).run("x")
    st = statuses(state)
    assert sorted(st.values()) == ["done", "failed"]
    failed = next(k for k, v in st.items() if v == "failed")
    assert state.tasks[failed]["reason"] == "merge_conflict"


def test_resume_reruns_only_unfinished_tasks(repo):
    broken = Scenario(code={"mul": lambda call: "не получилось"})
    first = Orchestrator(repo, broken.llm(), FakeUI(), Config(parallel=2, max_code_attempts=1)).run("Калькулятор")
    assert statuses(first) == {"add": "done", "mul": "failed", "power": "skipped"}
    assert first.tasks["mul"]["reason"] == "tests_red"

    llm2 = Scenario().llm()
    state = Orchestrator(repo, llm2, FakeUI(), Config(parallel=2)).resume(first.run_id)
    assert statuses(state) == {"add": "done", "mul": "done", "power": "done"}
    assert llm2.of("interview") == [] and llm2.of("plan") == []
    assert all(c.cwd.name != "add" for c in llm2.calls)
    git(repo, "show", f"{state.branch}:calc_power.py")
    assert state.final_green is True


def test_invalid_plan_is_retried_with_error(repo):
    llm = Scenario().llm(plan_reply=["это не план", PLAN])
    state = Orchestrator(repo, llm, FakeUI(), Config(parallel=2)).run("Калькулятор")
    assert state.status == "finished"
    first, second = llm.of("plan")
    assert "отклонён" not in first.prompt
    assert "отклонён" in second.prompt


def test_invalid_plan_twice_aborts_before_any_work(repo):
    llm = Scenario().llm(plan_reply="мусор")
    with pytest.raises(OrchError):
        Orchestrator(repo, llm, FakeUI(), Config()).run("Калькулятор")
    assert llm.of("tests") == [] and llm.of("code") == []
    assert git(repo, "branch", "--list", "orch/*") == ""


def test_user_can_cancel_after_seeing_plan(repo):
    llm = Scenario().llm()
    state = Orchestrator(repo, llm, FakeUI(confirm=False), Config()).run("Калькулятор")
    assert state.status == "cancelled"
    assert llm.of("tests") == []
    assert git(repo, "branch", "--list", "orch/*") == ""


def test_interview_can_be_skipped(repo):
    llm = Scenario().llm()
    ui = FakeUI()
    Orchestrator(repo, llm, ui, Config(parallel=2)).run("Калькулятор", interview=False)
    assert llm.of("interview") == [] and ui.questions == []


def test_ready_plan_skips_interview_and_planning(repo):
    llm = Scenario().llm()
    ui = FakeUI()
    state = Orchestrator(repo, llm, ui, Config(parallel=2)).run("Калькулятор", plan=PLAN)
    assert llm.of("interview") == [] and llm.of("plan") == []
    assert statuses(state) == {"add": "done", "mul": "done", "power": "done"}
    assert state.plan == PLAN
    assert any(state.run_id in m for m in ui.messages[:3])  # номер запуска сообщается сразу


def test_invalid_ready_plan_is_rejected_before_work(repo):
    llm = Scenario().llm()
    with pytest.raises(OrchError) as e:
        Orchestrator(repo, llm, FakeUI(), Config()).run("x", plan=plan(task("add", ["nope"])))
    assert "nope" in str(e.value)
    assert llm.calls == []
    assert git(repo, "branch", "--list", "orch/*") == ""


def test_state_is_persisted(repo):
    state = Orchestrator(repo, Scenario().llm(), FakeUI(), Config(parallel=2)).run("Калькулятор")
    assert (repo / ".orch" / "runs" / state.run_id / "state.json").exists()
    loaded = load_state(repo, state.run_id)
    assert loaded.task == "Калькулятор"
    assert statuses(loaded) == statuses(state)
    assert loaded.branch == state.branch


def test_non_git_folder_is_rejected(tmp_path):
    with pytest.raises(Exception) as e:
        Orchestrator(tmp_path, Scenario().llm(), FakeUI(), Config()).run("x")
    assert "git" in str(e.value).lower()
