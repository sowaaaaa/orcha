"""orch init — подготовка папки проекта; install-skill — навык /orch для Claude Code."""
import os
import sysconfig
from pathlib import Path

from . import git_ops
from .config import Config

SKILL_SOURCE = Path(__file__).parent / "skill" / "SKILL.md"

GITIGNORE = """# зависимости и окружения
node_modules/
.venv/
venv/
__pycache__/
*.pyc
# секреты
.env
.env.*
!.env.example
# сборка и кэши
dist/
build/
coverage/
.pytest_cache/
# служебное оркестратора
.orch/
"""


def _orch_toml() -> str:
    c = Config()
    return f"""# Настройки оркестратора orch. Все поля необязательны.
[models]
brain = "{c.brain_model}"    # опрос, план и ревью
worker = "{c.worker_model}"  # пишут тесты и код

[run]
parallel = {c.parallel}             # сколько воркеров одновременно
max_test_attempts = {c.max_test_attempts}    # попыток получить красные тесты
max_code_attempts = {c.max_code_attempts}    # попыток кода + ревью

# [tests]
# full = "npm test"        # полный набор тестов; иначе определяется автоматически

# [worktree]
# setup = "npm ci"         # подготовка каждой копии проекта
"""


def init_project(path) -> list[str]:
    """Готовит папку к работе оркестратора. Возвращает список сделанного (пустой — всё уже было готово)."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    done: list[str] = []

    if git_ops._ok(path, "rev-parse", "--is-inside-work-tree"):
        root = Path(git_ops.git(path, "rev-parse", "--show-toplevel"))
    else:
        git_ops.git(path, "init", "-q", "-b", "main")
        root = path
        done.append("создан git-репозиторий")

    created = []
    for name, text in ((".gitignore", GITIGNORE), ("orch.toml", _orch_toml())):
        f = root / name
        if not f.exists():
            f.write_text(text, encoding="utf-8")
            created.append(name)
    git_ops.exclude(root, ".orch/")

    if not git_ops._ok(root, "rev-parse", "--verify", "-q", "HEAD"):
        git_ops.commit_all(root, "init: проект подготовлен orch init")
        done += [f"создан {n}" for n in created] + ["сделан первый коммит"]
    elif created:
        # В существующей истории сами не коммитим — у пользователя могут быть свои незакоммиченные правки.
        done += [f"создан {n} (не закоммичен — закоммитьте его)" for n in created]
    return done


def install_skill(target_dir=None) -> Path:
    """Копирует навык в ~/.claude/skills/orch (или target_dir/orch). Повторный вызов обновляет его.
    В копию дописывается полный путь к orch на этом компьютере — на случай, если его нет в PATH."""
    base = Path(target_dir) if target_dir else Path.home() / ".claude" / "skills"
    dst = base / "orch" / "SKILL.md"
    dst.parent.mkdir(parents=True, exist_ok=True)
    exe = (Path(sysconfig.get_path("scripts")) / ("orch.exe" if os.name == "nt" else "orch")).as_posix()
    note = (f"\n## Где orch на этом компьютере\n\n"
            f"Если команда orch не найдена в PATH, вызывай её по полному пути: \"{exe}\"\n"
            f"(например: \"{exe}\" status).\n")
    dst.write_text(SKILL_SOURCE.read_text(encoding="utf-8") + note, encoding="utf-8")
    return dst
