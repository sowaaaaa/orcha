import copy

import pytest

from orch.plan import PlanError, parse_plan, topo_order


def task(id, deps=(), **kw):
    t = {
        "id": id,
        "title": f"Задача {id}",
        "description": "описание",
        "depends_on": list(deps),
        "test_files": [f"tests/test_{id}.py"],
        "test_command": f"python -m pytest tests/test_{id}.py",
        "acceptance": ["работает"],
    }
    t.update(kw)
    return t


def plan(*tasks, **kw):
    return {"spec": "Спека", "full_test_command": "python -m pytest", "tasks": list(tasks), **kw}


def test_parses_valid_plan():
    p = parse_plan(plan(task("a"), task("b", deps=["a"])))
    assert p.spec == "Спека"
    assert p.full_test_command == "python -m pytest"
    assert [t.id for t in p.tasks] == ["a", "b"]
    b = p.tasks[1]
    assert b.depends_on == ["a"]
    assert b.test_files == ["tests/test_b.py"]
    assert b.acceptance == ["работает"]


def test_optional_fields_default():
    t = task("a")
    del t["depends_on"]
    p = parse_plan({"spec": "s", "tasks": [t]})
    assert p.tasks[0].depends_on == []
    assert p.full_test_command is None


@pytest.mark.parametrize(
    "data, fragment",
    [
        (plan(), "tasks"),
        ({"spec": "s"}, "tasks"),
        (plan(task("a"), task("a")), "a"),
        (plan(task("a", deps=["zzz"])), "zzz"),
        (plan(task("a", deps=["a"])), "a"),
        (plan(task("a", deps=["b"]), task("b", deps=["a"])), "цикл"),
        (plan(task("a", test_command="")), "test_command"),
        (plan(task("a", test_files=[])), "test_files"),
        (plan(task("a", acceptance=[])), "acceptance"),
        (plan(task("t 1")), "id"),
        (plan(task("../x")), "id"),
        ("не словарь", "JSON"),
    ],
)
def test_rejects_invalid_plan(data, fragment):
    with pytest.raises(PlanError) as e:
        parse_plan(copy.deepcopy(data))
    assert fragment in str(e.value)


def test_topo_order_puts_dependencies_first():
    p = parse_plan(plan(task("c", deps=["b"]), task("b", deps=["a"]), task("a"), task("d")))
    order = topo_order(p.tasks)
    assert sorted(order) == ["a", "b", "c", "d"]
    assert order.index("a") < order.index("b") < order.index("c")
