"""План от мозга-оркестратора: подзадачи, зависимости, тесты, критерии приёмки."""
import re
from dataclasses import dataclass, field

ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class PlanError(ValueError):
    pass


@dataclass
class Task:
    id: str
    title: str
    description: str
    test_command: str
    acceptance: list[str]
    depends_on: list[str] = field(default_factory=list)
    test_files: list[str] = field(default_factory=list)


@dataclass
class Plan:
    spec: str
    tasks: list[Task]
    full_test_command: str | None = None


def parse_plan(data) -> Plan:
    if not isinstance(data, dict):
        raise PlanError("план должен быть JSON-объектом")
    raw = data.get("tasks")
    if not isinstance(raw, list) or not raw:
        raise PlanError("в плане нет подзадач (tasks)")
    tasks = [_parse_task(t, i) for i, t in enumerate(raw)]

    ids = [t.id for t in tasks]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        raise PlanError(f"повторяются id подзадач: {', '.join(sorted(dup))}")
    for t in tasks:
        for d in t.depends_on:
            if d == t.id:
                raise PlanError(f"подзадача {t.id} зависит сама от себя")
            if d not in ids:
                raise PlanError(f"подзадача {t.id} зависит от несуществующей {d}")
    topo_order(tasks)  # проверка на цикл

    full = data.get("full_test_command")
    return Plan(spec=str(data.get("spec") or ""), tasks=tasks,
                full_test_command=full if isinstance(full, str) and full.strip() else None)


def _parse_task(t, i) -> Task:
    if not isinstance(t, dict):
        raise PlanError(f"подзадача №{i + 1} должна быть объектом")
    tid = t.get("id")
    if not isinstance(tid, str) or not ID_RE.match(tid):
        raise PlanError(f"подзадача №{i + 1}: некорректный id {tid!r} (латиница, цифры, - и _)")

    def text(key):
        v = t.get(key)
        if not isinstance(v, str) or not v.strip():
            raise PlanError(f"подзадача {tid}: пустое поле {key}")
        return v

    def strings(key, required):
        v = t.get(key, [])
        if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
            raise PlanError(f"подзадача {tid}: поле {key} должно быть списком строк")
        if required and not v:
            raise PlanError(f"подзадача {tid}: пустое поле {key}")
        return v

    return Task(
        id=tid,
        title=text("title"),
        description=str(t.get("description") or ""),
        test_command=text("test_command"),
        acceptance=strings("acceptance", True),
        depends_on=strings("depends_on", False),
        test_files=strings("test_files", True),
    )


def topo_order(tasks: list[Task]) -> list[str]:
    deps = {t.id: set(t.depends_on) for t in tasks}
    order: list[str] = []
    while deps:
        ready = sorted(i for i, d in deps.items() if not d - set(order))
        if not ready:
            raise PlanError(f"цикл в зависимостях: {', '.join(sorted(deps))}")
        order += ready
        for i in ready:
            del deps[i]
    return order
