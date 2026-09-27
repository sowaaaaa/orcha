"""Вызов моделей через Claude Code CLI (`claude -p`) под аккаунтом пользователя."""
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import proc

# Какая роль отвечает за какой шаг.
ROLES = {"interview": "brain", "plan": "brain", "review": "brain", "tests": "worker", "code": "worker"}

BRAIN_TOOLS = "Read,Glob,Grep"
WORKER_TOOLS = "Read,Edit,Write,Glob,Grep"


class LLMError(RuntimeError):
    pass


@dataclass
class Reply:
    text: str
    session_id: str | None = None
    cost_usd: float = 0.0


class ClaudeCLI:
    def __init__(self, models: dict[str, str], exe: str | None = None, timeout: float = 1800, runner=None):
        self.models = models
        self.timeout = timeout
        self.exe = exe or shutil.which("claude")
        if not self.exe:
            raise LLMError("Не найден Claude Code CLI (`claude`). Установите: npm install -g @anthropic-ai/claude-code, затем войдите: claude")
        self.runner = runner or _default_runner

    def command(self, kind: str, session: str | None = None) -> list[str]:
        if kind not in ROLES:
            raise ValueError(f"Неизвестный шаг: {kind}")
        role = ROLES[kind]
        # --restricted: без запуска команд, без пользовательских хуков/настроек, правки только в cwd.
        cmd = [self.exe, "-p", "--restricted", "--output-format", "json", "--model", self.models[role]]
        if role == "worker":
            cmd += ["--permission-mode", "acceptEdits", "--tools", WORKER_TOOLS]
        else:
            cmd += ["--tools", BRAIN_TOOLS]
        if session:
            cmd += ["--resume", session]
        return cmd

    def ask(self, kind: str, prompt: str, cwd: Path, session: str | None = None) -> Reply:
        # Промпт идёт через stdin: так не нужно экранировать его для командной строки Windows.
        code, out, err = self.runner(self.command(kind, session), cwd, prompt, self.timeout)
        if code != 0 and not out.strip():
            raise LLMError(f"claude завершился с кодом {code}: {err.strip()[-2000:]}")
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            raise LLMError(f"claude вернул не JSON: {out.strip()[-500:]} {err.strip()[-500:]}")
        if data.get("is_error") or code != 0:
            raise LLMError(f"claude сообщил об ошибке: {str(data.get('result', ''))[-2000:]}")
        return Reply(text=data.get("result") or "", session_id=data.get("session_id"),
                     cost_usd=float(data.get("total_cost_usd") or 0))


def _default_runner(cmd, cwd, input, timeout):
    code, out, err, timed_out = proc.run(cmd, cwd, input=input, timeout=timeout)
    if timed_out:
        raise LLMError(f"claude не ответил за {timeout:.0f} с")
    return code, out, err


def extract_json(text: str) -> dict:
    """Достаёт JSON-объект из ответа модели (чистый, в ```json-блоке или внутри текста)."""
    candidates = [text]
    candidates += re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    for c in candidates:
        try:
            data = json.loads(c.strip())
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    raise ValueError("в ответе не найден JSON-объект")
