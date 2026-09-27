import json
import os

import pytest

from conftest import write
from orch.config import ConfigError, load_config


def test_defaults(tmp_path):
    c = load_config(tmp_path)
    assert c.brain_model == "claude-fable-5-1"
    assert c.worker_model == "claude-opus-5-5"
    assert c.parallel == 3
    assert c.max_test_attempts == 2
    assert c.max_code_attempts == 3
    assert c.full_test_command is None
    assert c.link == []
    assert c.setup_command is None


def test_toml_overrides(tmp_path):
    write(tmp_path, "orch.toml", """
[models]
brain = "b"
worker = "w"
[run]
parallel = 5
max_test_attempts = 1
max_code_attempts = 4
[tests]
full = "make test"
timeout = 60
[worktree]
link = ["deps"]
setup = "make deps"
""")
    c = load_config(tmp_path)
    assert (c.brain_model, c.worker_model) == ("b", "w")
    assert (c.parallel, c.max_test_attempts, c.max_code_attempts) == (5, 1, 4)
    assert c.full_test_command == "make test"
    assert c.test_timeout == 60
    assert c.link == ["deps"]
    assert c.setup_command == "make deps"


def test_invalid_values_are_rejected(tmp_path):
    write(tmp_path, "orch.toml", "[run]\nparallel = 0\n")
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_detects_npm_test(tmp_path):
    write(tmp_path, "package.json", json.dumps({"scripts": {"test": "vitest run"}}))
    assert load_config(tmp_path).full_test_command == "npm test"


def test_ignores_npm_placeholder_script(tmp_path):
    placeholder = 'echo "Error: no test specified" && exit 1'
    write(tmp_path, "package.json", json.dumps({"scripts": {"test": placeholder}}))
    assert load_config(tmp_path).full_test_command is None


def test_detects_pytest_for_python_project(tmp_path):
    write(tmp_path, "pyproject.toml", "[project]\nname = 'x'\n")
    assert "-m pytest" in load_config(tmp_path).full_test_command


def test_prefers_project_venv_python(tmp_path):
    exe = ".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python"
    write(tmp_path, exe, "")
    write(tmp_path, "requirements.txt", "pytest\n")
    cmd = load_config(tmp_path).full_test_command
    assert str(tmp_path / exe) not in cmd  # путь относительный — worktree получит свою ссылку на .venv
    assert ".venv" in cmd and "-m pytest" in cmd


def test_explicit_command_wins_over_detection(tmp_path):
    write(tmp_path, "package.json", json.dumps({"scripts": {"test": "jest"}}))
    write(tmp_path, "orch.toml", '[tests]\nfull = "npx jest --ci"\n')
    assert load_config(tmp_path).full_test_command == "npx jest --ci"


def test_auto_links_existing_dependency_dirs(tmp_path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / ".venv").mkdir()
    assert sorted(load_config(tmp_path).link) == [".venv", "node_modules"]
