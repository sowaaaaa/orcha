"""Цикл одной подзадачи: тесты (красные) → код (зелёные) → ревью. Модель — фейковая, git и тесты — настоящие."""
from conftest import PY, FakeLLM, git, write
from orch import git_ops as g
from orch.config import Config
from orch.plan import Task
from orch.worker import run_task

TASK = Task(
    id="add",
    title="Сложение",
    description="Функция add(a, b) в calc_add.py",
    test_command=f"{PY} check_add.py",
    acceptance=["add(2, 3) == 5"],
    test_files=["check_add.py"],
)
GOOD_TEST = "from calc_add import add\nassert add(2, 3) == 5, 'add(2,3) должно быть 5'\nprint('ok')\n"
TRIVIAL_TEST = "assert True\n"
GOOD_CODE = "def add(a, b):\n    return a + b\n"
BAD_CODE = "def add(a, b):\n    return a - b\n"
APPROVE = {"approved": True, "issues": []}


def writes(*pairs, reply="Готово, всё работает!"):
    def handler(call):
        for rel, text in zip(pairs[::2], pairs[1::2]):
            write(call.cwd, rel, text)
        return reply
    return handler


def run(repo, llm, **cfg):
    return run_task(TASK, "Спека: калькулятор", repo, llm, Config(**cfg))


def test_happy_path(repo):
    base = g.head(repo)
    llm = FakeLLM(tests=writes("check_add.py", GOOD_TEST), code=writes("calc_add.py", GOOD_CODE), review=APPROVE)
    out = run(repo, llm)
    assert out.ok, out.reason
    assert out.attempts == 1
    assert llm.kinds() == ["tests", "code", "review"]
    assert git(repo, "status", "--porcelain") == ""
    assert git(repo, "rev-list", "--count", f"{base}..HEAD") == "2"  # коммит тестов + коммит кода
    assert git(repo, "show", "HEAD:calc_add.py").replace("\r\n", "\n") == GOOD_CODE.strip()


def test_tests_that_pass_without_code_are_rejected_then_rewritten(repo):
    llm = FakeLLM(
        tests=[writes("check_add.py", TRIVIAL_TEST), writes("check_add.py", GOOD_TEST)],
        code=writes("calc_add.py", GOOD_CODE),
        review=APPROVE,
    )
    out = run(repo, llm)
    assert out.ok, out.reason
    second = llm.of("tests")[1].prompt
    assert "без реализации" in second


def test_red_gate_gives_up_after_limit(repo):
    llm = FakeLLM(tests=writes("check_add.py", TRIVIAL_TEST), code=writes("calc_add.py", GOOD_CODE), review=APPROVE)
    out = run(repo, llm, max_test_attempts=2)
    assert not out.ok
    assert out.reason == "red_gate"
    assert len(llm.of("tests")) == 2
    assert llm.of("code") == []


def test_missing_test_files_do_not_count_as_red(repo):
    llm = FakeLLM(tests="Написал тесты!", code=writes("calc_add.py", GOOD_CODE), review=APPROVE)
    out = run(repo, llm, max_test_attempts=2)
    assert not out.ok and out.reason == "red_gate"
    assert "check_add.py" in llm.of("tests")[1].prompt


def test_worker_words_are_not_trusted(repo):
    llm = FakeLLM(tests=writes("check_add.py", GOOD_TEST), code="Готово, всё работает!", review=APPROVE)
    out = run(repo, llm, max_code_attempts=3)
    assert not out.ok
    assert out.reason == "tests_red"
    assert out.attempts == 3
    assert llm.of("review") == []


def test_test_failure_output_is_sent_back_to_worker(repo):
    llm = FakeLLM(
        tests=writes("check_add.py", GOOD_TEST),
        code=[writes("calc_add.py", BAD_CODE), writes("calc_add.py", GOOD_CODE)],
        review=APPROVE,
    )
    out = run(repo, llm)
    assert out.ok and out.attempts == 2
    assert "add(2,3) должно быть 5" in llm.of("code")[1].prompt


def test_editing_tests_is_rolled_back(repo):
    cheat = writes("check_add.py", "print('ok')\n", "calc_add.py", BAD_CODE)
    llm = FakeLLM(
        tests=writes("check_add.py", GOOD_TEST),
        code=[cheat, writes("calc_add.py", GOOD_CODE)],
        review=APPROVE,
    )
    out = run(repo, llm)
    assert out.ok and out.attempts == 2
    assert (repo / "check_add.py").read_text(encoding="utf-8") == GOOD_TEST
    assert git(repo, "show", "HEAD:check_add.py").replace("\r\n", "\n") == GOOD_TEST.strip()
    assert "check_add.py" in llm.of("code")[1].prompt


def test_review_rejection_goes_back_to_worker(repo):
    llm = FakeLLM(
        tests=writes("check_add.py", GOOD_TEST),
        code=writes("calc_add.py", GOOD_CODE),
        review=[{"approved": False, "issues": ["нет docstring"]}, APPROVE],
    )
    out = run(repo, llm)
    assert out.ok and out.attempts == 2
    assert "нет docstring" in llm.of("code")[1].prompt


def test_review_can_fail_the_task(repo):
    llm = FakeLLM(
        tests=writes("check_add.py", GOOD_TEST),
        code=writes("calc_add.py", GOOD_CODE),
        review={"approved": False, "issues": ["плохо"]},
    )
    out = run(repo, llm, max_code_attempts=2)
    assert not out.ok and out.reason == "review"


def test_unreadable_review_does_not_block_green_tests(repo):
    llm = FakeLLM(tests=writes("check_add.py", GOOD_TEST), code=writes("calc_add.py", GOOD_CODE), review="не json")
    assert run(repo, llm).ok


def test_review_sees_the_diff_and_uses_no_worker_session(repo):
    llm = FakeLLM(tests=writes("check_add.py", GOOD_TEST), code=writes("calc_add.py", GOOD_CODE), review=APPROVE)
    run(repo, llm)
    review = llm.of("review")[0]
    assert "return a + b" in review.prompt
    assert review.session is None


def test_worker_keeps_one_session(repo):
    llm = FakeLLM(
        tests=writes("check_add.py", GOOD_TEST),
        code=[writes("calc_add.py", BAD_CODE), writes("calc_add.py", GOOD_CODE)],
        review=APPROVE,
    )
    run(repo, llm)
    first = llm.of("tests")[0]
    assert first.session is None
    assert [c.session for c in llm.of("code")] == ["s1", "s1"]
