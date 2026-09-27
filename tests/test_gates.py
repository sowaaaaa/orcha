import time

from conftest import PY, write
from orch.gates import run_tests, tail


def test_passing_command(tmp_path):
    r = run_tests(f'{PY} -c "print(123)"', tmp_path)
    assert r.passed and r.code == 0
    assert "123" in r.output


def test_failing_command_keeps_output(tmp_path):
    write(tmp_path, "check.py", "print('до падения')\nassert 1 == 2, 'сломалось'\n")
    r = run_tests(f"{PY} check.py", tmp_path)
    assert not r.passed and r.code != 0
    assert "до падения" in r.output
    assert "сломалось" in r.output  # stderr тоже попадает в вывод


def test_timeout_kills_command_and_fails(tmp_path):
    started = time.monotonic()
    r = run_tests(f'{PY} -c "import time; time.sleep(30)"', tmp_path, timeout=1)
    assert time.monotonic() - started < 15
    assert not r.passed
    assert "TIMEOUT" in r.output


def test_worktree_is_first_on_pythonpath(tmp_path):
    (tmp_path / "src").mkdir()
    write(tmp_path, "show.py", "import os; print(os.environ['PYTHONPATH'])\n")
    r = run_tests(f"{PY} show.py", tmp_path)
    first = r.output.strip().split(__import__("os").pathsep)[0]
    assert first == str(tmp_path)
    assert str(tmp_path / "src") in r.output


def test_fast_rewrite_is_not_hidden_by_bytecode_cache(tmp_path):
    # Перезапись файла той же длины в ту же секунду не должна запускать старый .pyc.
    write(tmp_path, "check.py", "from calc import f\nassert f() == 2\n")
    write(tmp_path, "calc.py", "def f():\n    return 1\n")
    assert not run_tests(f"{PY} check.py", tmp_path).passed
    write(tmp_path, "calc.py", "def f():\n    return 2\n")
    assert run_tests(f"{PY} check.py", tmp_path).passed
    assert not (tmp_path / "__pycache__").exists()


def test_tail_keeps_the_end():
    text = "начало " + "x" * 10_000 + " конец"
    t = tail(text, 100)
    assert t.endswith(" конец")
    assert len(t) < 200
    assert tail("коротко", 100) == "коротко"
