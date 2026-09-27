from conftest import FakeUI
from test_pipeline import Scenario

from orch import cli
from orch.config import Config
from orch.pipeline import Orchestrator


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
