import json

from conftest import FakeUI
from test_pipeline import PLAN, Scenario

from orch import cli
from orch.config import Config
from orch.pipeline import Orchestrator


def fake_claude(monkeypatch, llm):
    monkeypatch.setattr(cli, "ClaudeCLI", lambda models, timeout: llm)
    return llm


def test_run_with_ready_plan_file(repo, tmp_path, monkeypatch):
    f = tmp_path / "plan.json"
    f.write_text(json.dumps(PLAN, ensure_ascii=False), encoding="utf-8")
    llm = fake_claude(monkeypatch, Scenario().llm())
    assert cli.main(["run", "Калькулятор", "--repo", str(repo), "--plan", str(f), "--yes"]) == 0
    assert llm.of("interview") == [] and llm.of("plan") == []


def test_run_with_missing_or_broken_plan_file(repo, tmp_path, monkeypatch, capsys):
    fake_claude(monkeypatch, Scenario().llm())
    assert cli.main(["run", "x", "--repo", str(repo), "--plan", str(tmp_path / "нет.json"), "--yes"]) == 1
    broken = tmp_path / "broken.json"
    broken.write_text("{не json", encoding="utf-8")
    assert cli.main(["run", "x", "--repo", str(repo), "--plan", str(broken), "--yes"]) == 1
    assert capsys.readouterr().out.count("Ошибка") == 2


def test_status_shows_why_a_task_failed(repo, capsys):
    sc = Scenario(code={"mul": lambda call: "не получилось"})
    state = Orchestrator(repo, sc.llm(), FakeUI(), Config(parallel=2, max_code_attempts=1)).run("Калькулятор")
    capsys.readouterr()
    assert cli.main(["status", state.run_id, "--repo", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "tests_red" in out
    assert "calc_mul" in out  # хвост вывода тестов: чего не хватает


def test_init_command(tmp_path, capsys):
    p = tmp_path / "app"
    assert cli.main(["init", str(p)]) == 0
    assert (p / "orch.toml").exists()
    assert "/orch" in capsys.readouterr().out
    assert cli.main(["init", str(p)]) == 0
    assert "уже" in capsys.readouterr().out


def test_install_skill_command(tmp_path, capsys):
    assert cli.main(["install-skill", "--dir", str(tmp_path)]) == 0
    assert (tmp_path / "orch" / "SKILL.md").exists()
    assert "/orch" in capsys.readouterr().out


def test_status_without_runs(repo, capsys):
    assert cli.main(["status", "--repo", str(repo)]) == 0
    assert "Запусков нет" in capsys.readouterr().out


def test_status_lists_runs(repo, capsys):
    state = Orchestrator(repo, Scenario().llm(), FakeUI(), Config(parallel=2)).run("Калькулятор")
    assert cli.main(["status", "--repo", str(repo)]) == 0
    out = capsys.readouterr().out
    assert state.run_id in out and "Калькулятор" in out


def test_run_uses_model_overrides_and_exit_code(repo, monkeypatch, capsys):
    fake = Scenario().llm()
    got = {}

    def make_llm(models, timeout):
        got.update(models)
        return fake

    monkeypatch.setattr(cli, "ClaudeCLI", make_llm)
    code = cli.main(["run", "Калькулятор", "--repo", str(repo), "--yes", "--no-interview",
                     "--brain", "my-brain", "--worker", "my-worker"])
    assert code == 0
    assert got == {"brain": "my-brain", "worker": "my-worker"}
    assert fake.of("interview") == []
    assert "orch/" in capsys.readouterr().out  # в итоге назван результат-ветка


def test_errors_give_nonzero_exit(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "ClaudeCLI", lambda models, timeout: Scenario().llm())
    assert cli.main(["run", "x", "--repo", str(tmp_path), "--yes"]) == 1
    assert "Ошибка" in capsys.readouterr().out
