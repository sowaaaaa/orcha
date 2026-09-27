import threading
import time

from orch.plan import Task
from orch.scheduler import run_dag


def T(id, *deps):
    return Task(id=id, title=id, description="", test_command="x", acceptance=["ok"],
                depends_on=list(deps), test_files=["t"])


def test_dependencies_run_after_their_prerequisites():
    events = []
    lock = threading.Lock()

    def work(t):
        with lock:
            events.append(("start", t.id))
        time.sleep(0.05)
        with lock:
            events.append(("end", t.id))
        return "ok"

    res = run_dag([T("b", "a"), T("a")], work, lambda t, r: True, parallel=4)
    assert res == {"a": "done", "b": "done"}
    assert events.index(("end", "a")) < events.index(("start", "b"))


def test_independent_tasks_run_in_parallel():
    barrier = threading.Barrier(3, timeout=5)  # сломается, если задачи идут не одновременно

    def work(t):
        barrier.wait()
        return "ok"

    res = run_dag([T("a"), T("b"), T("c")], work, lambda t, r: True, parallel=3)
    assert res == {"a": "done", "b": "done", "c": "done"}


def test_parallel_limit_is_respected():
    lock = threading.Lock()
    state = {"now": 0, "max": 0}

    def work(t):
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        time.sleep(0.1)
        with lock:
            state["now"] -= 1

    res = run_dag([T(x) for x in "abcd"], work, lambda t, r: True, parallel=2)
    assert all(s == "done" for s in res.values())
    assert state["max"] == 2


def test_failed_work_skips_dependents_but_not_others():
    def work(t):
        if t.id == "a":
            raise RuntimeError("boom")
        return "ok"

    res = run_dag([T("a"), T("b", "a"), T("c", "b"), T("d")], work, lambda t, r: True, parallel=2)
    assert res == {"a": "failed", "b": "skipped", "c": "skipped", "d": "done"}


def test_finish_returning_false_marks_failed():
    res = run_dag([T("a"), T("b", "a")], lambda t: "ok", lambda t, r: t.id != "a", parallel=2)
    assert res == {"a": "failed", "b": "skipped"}


def test_already_done_tasks_are_not_rerun():
    ran = []

    def work(t):
        ran.append(t.id)

    res = run_dag([T("a"), T("b", "a")], work, lambda t, r: True, parallel=2, done={"a"})
    assert ran == ["b"]
    assert res == {"a": "done", "b": "done"}


def test_finish_and_on_start_run_in_calling_thread():
    main = threading.current_thread()
    seen = []

    def on_start(t):
        seen.append(("start", threading.current_thread() is main))

    def finish(t, r):
        seen.append(("finish", threading.current_thread() is main))
        return True

    run_dag([T("a"), T("b")], lambda t: None, finish, parallel=2, on_start=on_start)
    assert len(seen) == 4 and all(ok for _, ok in seen)


def test_finish_receives_work_result():
    got = {}

    def finish(t, r):
        got[t.id] = r
        return True

    run_dag([T("a")], lambda t: f"result-{t.id}", finish, parallel=1)
    assert got == {"a": "result-a"}
