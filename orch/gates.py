"""Запуск тестов. Решение «зелёное/красное» принимается только по коду возврата команды."""
import os
from dataclasses import dataclass
from pathlib import Path

from . import proc


@dataclass
class TestResult:
    __test__ = False  # не путать pytest

    passed: bool
    output: str
    code: int


def run_tests(command: str, cwd: Path, timeout: float = 900) -> TestResult:
    cwd = Path(cwd)
    env = dict(os.environ)
    # Код из этой копии проекта должен импортироваться раньше, чем установленный где-то ещё.
    paths = [str(cwd)] + ([str(cwd / "src")] if (cwd / "src").is_dir() else [])
    if env.get("PYTHONPATH"):
        paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env["PYTHONIOENCODING"] = "utf-8"
    # Без .pyc: иначе быстрая перезапись файла той же длины запускает устаревший кэш,
    # а __pycache__ попадает в коммиты воркера.
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    code, out, _, timed_out = proc.run(command, cwd, timeout=timeout, env=env, shell=True, merge_stderr=True)
    if timed_out:
        return TestResult(False, out + f"\nTIMEOUT: тесты не завершились за {timeout:.0f} с", -1)
    return TestResult(code == 0, out, code)


def tail(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return "…(начало обрезано)…\n" + text[-limit:]
