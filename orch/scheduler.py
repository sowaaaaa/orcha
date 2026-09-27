"""Запуск подзадач по графу зависимостей с ограничением параллельности."""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Callable

from .plan import Task


def run_dag(tasks: list[Task], work: Callable, finish: Callable, parallel: int,
            done=(), on_start: Callable | None = None) -> dict[str, str]:
    """work(task) выполняется в пуле потоков; on_start и finish(task, result) — в вызывающем потоке.
    finish возвращает True, если задача принята — только тогда запускаются зависимые.
    Итог: {id: "done" | "failed" | "skipped"}."""
    status = {t.id: ("done" if t.id in done else "pending") for t in tasks}
    by_id = {t.id: t for t in tasks}
    running = {}

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        while True:
            _skip_blocked(tasks, status)
            for t in tasks:
                if len(running) >= parallel:
                    break
                if status[t.id] == "pending" and all(status[d] == "done" for d in t.depends_on):
                    status[t.id] = "running"
                    if on_start:
                        on_start(t)
                    running[pool.submit(work, t)] = t.id
            if not running:
                break
            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            for fut in finished:
                t = by_id[running.pop(fut)]
                try:
                    ok = finish(t, fut.result())
                except Exception:
                    ok = False
                status[t.id] = "done" if ok else "failed"
    return status


def _skip_blocked(tasks, status):
    changed = True
    while changed:
        changed = False
        for t in tasks:
            if status[t.id] == "pending" and any(status[d] in ("failed", "skipped") for d in t.depends_on):
                status[t.id] = "skipped"
                changed = True
