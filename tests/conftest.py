"""Общие фикстуры: временный git-репозиторий и фейковая модель со сценарием."""
import json
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest

from orch.llm import Reply

# Интерпретатор, которым запускаются «тесты проекта» внутри сценариев.
PY = f'"{sys.executable}"'

# Полный набор тестов игрушечного проекта: запускает все check_*.py.
RUN_ALL = """import glob, subprocess, sys
rc = 0
for f in sorted(glob.glob("check_*.py")):
    rc |= subprocess.call([sys.executable, f])
sys.exit(rc)
"""


def git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout.strip()


def write(root, rel, text):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "core.autocrlf", "false")
    write(path, "README.md", "# proj\n")
    write(path, "run_all.py", RUN_ALL)
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "init")
    return path


@pytest.fixture
def repo(tmp_path) -> Path:
    return init_repo(tmp_path / "proj")


@dataclass
class Call:
    kind: str
    prompt: str
    cwd: Path
    session: str | None


class FakeLLM:
    """Модель по сценарию: script[kind] — ответ (str/dict), функция call -> ответ,
    или список таких (берутся по очереди, последний повторяется).
    Функция может писать файлы в call.cwd — так «модель» правит проект."""

    def __init__(self, **script):
        self.script = {k: (list(v) if isinstance(v, list) else v) for k, v in script.items()}
        self.calls: list[Call] = []
        self._lock = threading.Lock()

    def ask(self, kind, prompt, cwd, session=None):
        with self._lock:
            call = Call(kind, prompt, Path(cwd), session)
            self.calls.append(call)
            sid = session or f"s{len(self.calls)}"
            h = self.script[kind]
            if isinstance(h, list):
                h = h.pop(0) if len(h) > 1 else h[0]
        out = h(call) if callable(h) else h
        if isinstance(out, dict):
            out = json.dumps(out, ensure_ascii=False)
        return Reply(text=out, session_id=sid)

    def kinds(self):
        return [c.kind for c in self.calls]

    def of(self, kind):
        return [c for c in self.calls if c.kind == kind]


class FakeUI:
    def __init__(self, answers=None, confirm=True):
        self.answers = list(answers or [])
        self.questions: list[str] = []
        self.confirm_value = confirm
        self.confirmed: list[str] = []
        self.messages: list[str] = []

    def ask(self, question):
        self.questions.append(question)
        return self.answers.pop(0) if self.answers else "нет ответа"

    def confirm(self, text):
        self.confirmed.append(text)
        return self.confirm_value

    def info(self, msg):
        self.messages.append(msg)
