"""Настройки из orch.toml в корне проекта + автоопределение команды тестов."""
import json
import os
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

AUTO_LINK = ("node_modules", ".venv", "venv")
PY_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg", "pytest.ini", "tox.ini", "requirements.txt")


class ConfigError(ValueError):
    pass


@dataclass
class Config:
    brain_model: str = "claude-fable-5-1"
    worker_model: str = "claude-opus-5-5"
    parallel: int = 3
    max_test_attempts: int = 2
    max_code_attempts: int = 3
    full_test_command: str | None = None
    test_timeout: float = 900
    llm_timeout: float = 1800
    link: list[str] = field(default_factory=list)
    setup_command: str | None = None


def load_config(repo) -> Config:
    repo = Path(repo)
    f = repo / "orch.toml"
    try:
        data = tomllib.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"orch.toml: {e}")
    models, run = data.get("models", {}), data.get("run", {})
    tests, wt = data.get("tests", {}), data.get("worktree", {})

    c = Config()
    c.brain_model = models.get("brain", c.brain_model)
    c.worker_model = models.get("worker", c.worker_model)
    c.parallel = run.get("parallel", c.parallel)
    c.max_test_attempts = run.get("max_test_attempts", c.max_test_attempts)
    c.max_code_attempts = run.get("max_code_attempts", c.max_code_attempts)
    c.llm_timeout = run.get("llm_timeout", c.llm_timeout)
    c.test_timeout = tests.get("timeout", c.test_timeout)
    c.full_test_command = tests.get("full") or detect_test_command(repo)
    c.link = list(wt["link"]) if "link" in wt else [n for n in AUTO_LINK if (repo / n).is_dir()]
    c.setup_command = wt.get("setup") or None

    for name in ("parallel", "max_test_attempts", "max_code_attempts"):
        v = getattr(c, name)
        if not isinstance(v, int) or v < 1:
            raise ConfigError(f"orch.toml: {name} должно быть целым числом ≥ 1, сейчас {v!r}")
    return c


def detect_test_command(repo: Path) -> str | None:
    pkg = repo / "package.json"
    if pkg.exists():
        try:
            script = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {}).get("test", "")
        except (ValueError, AttributeError):
            script = ""
        if script and "no test specified" not in script:
            return "npm test"
    if any((repo / m).exists() for m in PY_MARKERS):
        return f"{_python(repo)} -m pytest -q"
    return None


def _python(repo: Path) -> str:
    # Путь относительный: в каждом worktree .venv подключается ссылкой, и команда работает там же.
    rel = os.path.join("Scripts", "python.exe") if os.name == "nt" else os.path.join("bin", "python")
    for venv in (".venv", "venv"):
        if (repo / venv / rel).exists():
            return os.path.join(venv, rel)
    return "python" if shutil.which("python") else "python3"
