"""orch init (подготовка проекта) и навык /orch для Claude Code."""
import re
import shlex
import sysconfig
from pathlib import Path

import pytest

from conftest import git, write
from orch import git_ops as g
from orch.bootstrap import SKILL_SOURCE, init_project, install_skill
from orch.cli import build_parser
from orch.config import Config, load_config


def test_init_creates_new_project(tmp_path):
    p = tmp_path / "new-app"
    actions = init_project(p)
    assert actions
    assert g.branch_exists(p, git(p, "branch", "--show-current"))
    gitignore = (p / ".gitignore").read_text(encoding="utf-8")
    assert "node_modules/" in gitignore and ".env" in gitignore
    cfg = load_config(p)
    assert (cfg.brain_model, cfg.worker_model) == (Config().brain_model, Config().worker_model)
    assert git(p, "rev-list", "--count", "HEAD") == "1"
    write(p, ".orch/plans/draft.json", "{}")  # черновики плана не мешают git
    assert git(p, "status", "--porcelain") == ""


def test_init_commits_existing_files_but_not_secrets(tmp_path):
    p = tmp_path / "app"
    write(p, "main.py", "print(1)\n")
    write(p, ".env", "TOKEN=secret\n")
    init_project(p)
    files = git(p, "ls-files").splitlines()
    assert {"main.py", ".gitignore", "orch.toml"} <= set(files)
    assert ".env" not in files


def test_init_keeps_existing_repo_and_files(repo):
    write(repo, ".gitignore", "custom\n")
    g.commit_all(repo, "gitignore")
    head = g.head(repo)
    actions = init_project(repo)
    assert g.head(repo) == head  # чужую историю не трогаем, сами не коммитим
    assert (repo / ".gitignore").read_text(encoding="utf-8") == "custom\n"
    assert (repo / "orch.toml").exists()
    assert any("orch.toml" in a for a in actions)


def test_init_is_idempotent(tmp_path):
    p = tmp_path / "app"
    init_project(p)
    head = g.head(p)
    toml = (p / "orch.toml").read_text(encoding="utf-8")
    assert init_project(p) == []
    assert g.head(p) == head
    assert (p / "orch.toml").read_text(encoding="utf-8") == toml


def test_install_skill_copies_and_updates(tmp_path):
    dst = install_skill(tmp_path / "skills")
    assert dst == tmp_path / "skills" / "orch" / "SKILL.md"
    source = SKILL_SOURCE.read_text(encoding="utf-8")
    assert dst.read_text(encoding="utf-8").startswith(source)
    dst.write_text("устаревшая версия", encoding="utf-8")
    install_skill(tmp_path / "skills")
    assert dst.read_text(encoding="utf-8").startswith(source)


def test_installed_skill_knows_where_orch_is(tmp_path):
    # Если orch не в PATH, Claude в чате всё равно найдёт его по полному пути.
    text = install_skill(tmp_path).read_text(encoding="utf-8")
    scripts = Path(sysconfig.get_path("scripts")).as_posix()
    assert scripts in text


def test_skill_is_manual_only_and_takes_arguments():
    text = SKILL_SOURCE.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, "нет frontmatter"
    fm = m.group(1)
    assert re.search(r"^name: orch$", fm, re.M)
    assert re.search(r"^description: .+", fm, re.M)
    assert re.search(r"^disable-model-invocation: true$", fm, re.M)
    assert "$ARGUMENTS" in text


def test_skill_documents_the_plan_format():
    text = SKILL_SOURCE.read_text(encoding="utf-8")
    for key in ("spec", "full_test_command", "tasks", "depends_on", "test_files", "test_command", "acceptance"):
        assert f'"{key}"' in text


def test_commands_in_skill_match_the_cli():
    # Каждая команда `orch ...` из навыка должна разбираться нашим CLI — документация не разъедется с кодом.
    cmds = re.findall(r"`(orch [^`]+)`", SKILL_SOURCE.read_text(encoding="utf-8"))
    assert len(cmds) >= 4
    parser = build_parser()
    for c in cmds:
        try:
            parser.parse_args(shlex.split(c)[1:])
        except SystemExit as e:
            pytest.fail(f"команда из навыка не разбирается CLI: {c} (код {e.code})")
