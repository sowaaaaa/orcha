"""Цикл одной подзадачи в её worktree: тесты (должны упасть) → код (до зелёного) → ревью мозгом."""
from dataclasses import dataclass
from pathlib import Path

from . import git_ops, prompts
from .gates import run_tests, tail
from .llm import extract_json

REVIEW_DIFF_LIMIT = 30_000


@dataclass
class TaskOutcome:
    ok: bool
    reason: str = ""  # red_gate | tests_red | review | setup | error
    attempts: int = 0
    detail: str = ""


def run_task(task, spec: str, wt: Path, llm, cfg, log=lambda msg: None) -> TaskOutcome:
    wt = Path(wt)
    files = [f.replace("\\", "/") for f in task.test_files]
    base = git_ops.head(wt)
    session = None
    feedback = ""

    # Этап 1: тесты до кода. Принимаем только созданные тестовые файлы, которые падают.
    for attempt in range(1, cfg.max_test_attempts + 1):
        reply = llm.ask("tests", prompts.tests(task, spec, feedback), wt, session)
        session = reply.session_id or session
        if not git_ops.changed_files(wt, base, files):
            feedback = f"Тестовые файлы не созданы и не изменены: {', '.join(files)}. Напиши тесты в них."
            log(f"тесты: файлы не созданы (попытка {attempt})")
            continue
        red = run_tests(task.test_command, wt, cfg.test_timeout)
        if red.passed:
            feedback = ("Тесты прошли без реализации — значит, они ничего не проверяют. "
                        "Перепиши их так, чтобы они падали, пока функциональность не написана.")
            log(f"тесты зелёные до кода — отклонены (попытка {attempt})")
            continue
        break
    else:
        return TaskOutcome(False, "red_gate", attempt, feedback)

    git_ops.commit_all(wt, f"orch({task.id}): тесты")
    tests_commit = git_ops.head(wt)
    log("тесты красные — принято, пишем код")
    feedback = f"Сейчас тесты падают так:\n{tail(red.output, 3000)}"

    # Этап 2: код до зелёного. Слова воркера не учитываются — только результат запуска тестов.
    reason = "tests_red"
    for attempt in range(1, cfg.max_code_attempts + 1):
        reply = llm.ask("code", prompts.code(task, spec, feedback), wt, session)
        session = reply.session_id or session

        touched = git_ops.changed_files(wt, tests_commit, files)
        if touched:
            git_ops.restore_files(wt, tests_commit, touched)
            feedback = (f"Ты изменил тестовые файлы ({', '.join(touched)}) — это запрещено, изменения откатаны. "
                        "Меняй только реализацию.")
            reason = "tests_red"
            log(f"код: воркер правил тесты — откат (попытка {attempt})")
            continue

        result = run_tests(task.test_command, wt, cfg.test_timeout)
        if not result.passed:
            feedback = f"Тесты всё ещё красные (код выхода {result.code}):\n{tail(result.output)}"
            reason = "tests_red"
            log(f"код: тесты красные (попытка {attempt})")
            continue

        git_ops.commit_all(wt, f"orch({task.id}): {task.title} (попытка {attempt})")
        issues = _review(task, spec, wt, base, result.output, llm, log)
        if not issues:
            log(f"зелёные, ревью принято (попытка {attempt})")
            return TaskOutcome(True, "", attempt)
        feedback = "Ревью отклонило результат. Исправь:\n" + "\n".join(f"- {i}" for i in issues)
        reason = "review"
        log(f"ревью отклонило (попытка {attempt})")

    return TaskOutcome(False, reason, cfg.max_code_attempts, feedback)


def _review(task, spec, wt, base, test_output, llm, log) -> list[str]:
    """Пустой список — принято."""
    d = git_ops.diff(wt, base)
    if len(d) > REVIEW_DIFF_LIMIT:
        d = d[:REVIEW_DIFF_LIMIT] + "\n…(diff обрезан)…"
    reply = llm.ask("review", prompts.review(task, spec, d, tail(test_output, 3000)), wt)
    try:
        data = extract_json(reply.text)
    except ValueError:
        log("ревью вернуло не JSON — принимаю по зелёным тестам")
        return []
    if data.get("approved") is False:
        return [str(i) for i in data.get("issues") or []] or ["ревьюер отклонил без пояснений"]
    return []
