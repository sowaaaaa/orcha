import pytest

from conftest import git, write
from orch import git_ops as g


def wt_path(repo, name):
    return repo / ".orch" / "wt" / name


def test_ensure_repo_rejects_non_git_and_empty_repos(tmp_path):
    with pytest.raises(g.GitError):
        g.ensure_repo(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    git(empty, "init", "-q")
    with pytest.raises(g.GitError):
        g.ensure_repo(empty)


def test_ensure_repo_accepts_repo_with_commit(repo):
    g.ensure_repo(repo)


def test_worktree_is_created_on_its_own_branch(repo):
    wt = wt_path(repo, "a")
    g.add_worktree(repo, wt, "orch/r1/a", base=g.head(repo))
    assert (wt / "README.md").read_text(encoding="utf-8") == "# proj\n"
    assert git(wt, "rev-parse", "--abbrev-ref", "HEAD") == "orch/r1/a"


def test_worktrees_are_isolated(repo):
    g.exclude(repo, ".orch/")
    a, b = wt_path(repo, "a"), wt_path(repo, "b")
    g.add_worktree(repo, a, "orch/r1/a", base=g.head(repo))
    g.add_worktree(repo, b, "orch/r1/b", base=g.head(repo))
    write(a, "new.py", "x = 1\n")
    write(a, "README.md", "changed\n")
    assert not (b / "new.py").exists()
    assert not (repo / "new.py").exists()
    assert (b / "README.md").read_text(encoding="utf-8") == "# proj\n"
    assert (repo / "README.md").read_text(encoding="utf-8") == "# proj\n"
    assert git(repo, "status", "--porcelain") == ""  # .orch/ не мешает основной папке


def test_exclude_is_idempotent(repo):
    g.exclude(repo, ".orch/")
    g.exclude(repo, ".orch/")
    text = (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert text.count(".orch/") == 1


def test_commit_all_reports_whether_something_was_committed(repo):
    assert g.commit_all(repo, "nothing") is False
    write(repo, "f.txt", "1")
    assert g.commit_all(repo, "add f") is True
    assert git(repo, "log", "-1", "--format=%s") == "add f"


def test_merge_brings_branch_changes(repo):
    base = g.head(repo)
    main, a = wt_path(repo, "main"), wt_path(repo, "a")
    g.add_worktree(repo, main, "orch/r1/main", base=base)
    g.add_worktree(repo, a, "orch/r1/a", base=base)
    write(a, "feature.py", "ok = True\n")
    g.commit_all(a, "feature")
    assert g.merge(main, "orch/r1/a", "merge a") is True
    assert (main / "feature.py").exists()


def test_merge_conflict_is_aborted_cleanly(repo):
    base = g.head(repo)
    main, a, b = wt_path(repo, "main"), wt_path(repo, "a"), wt_path(repo, "b")
    for p, br in ((main, "main"), (a, "a"), (b, "b")):
        g.add_worktree(repo, p, f"orch/r1/{br}", base=base)
    write(a, "README.md", "версия A\n")
    g.commit_all(a, "a")
    write(b, "README.md", "версия B\n")
    g.commit_all(b, "b")
    assert g.merge(main, "orch/r1/a", "merge a") is True
    before = g.head(main)
    assert g.merge(main, "orch/r1/b", "merge b") is False
    assert g.head(main) == before
    assert git(main, "status", "--porcelain") == ""
    assert (main / "README.md").read_text(encoding="utf-8") == "версия A\n"


def test_undo_last_merge(repo):
    base = g.head(repo)
    main, a = wt_path(repo, "main"), wt_path(repo, "a")
    g.add_worktree(repo, main, "orch/r1/main", base=base)
    g.add_worktree(repo, a, "orch/r1/a", base=base)
    write(a, "feature.py", "ok = True\n")
    g.commit_all(a, "feature")
    g.merge(main, "orch/r1/a", "merge a")
    g.undo_last_merge(main)
    assert g.head(main) == base
    assert not (main / "feature.py").exists()


def test_add_worktree_for_existing_branch(repo):
    wt = wt_path(repo, "main")
    g.add_worktree(repo, wt, "orch/r1/main", base=g.head(repo))
    write(wt, "x.txt", "x")
    g.commit_all(wt, "x")
    tip = g.head(wt)
    g.remove_worktree(repo, wt)
    assert not wt.exists()
    assert g.branch_exists(repo, "orch/r1/main")
    g.add_worktree(repo, wt, "orch/r1/main")  # без base — берём существующую ветку как есть
    assert g.head(wt) == tip


def test_add_worktree_recovers_from_stale_leftovers(repo):
    wt = wt_path(repo, "a")
    g.add_worktree(repo, wt, "orch/r1/a", base=g.head(repo))
    write(wt, "junk.txt", "после сбоя")
    g.commit_all(wt, "junk")
    # «сбой»: worktree не удалён, ветка осталась
    g.add_worktree(repo, wt, "orch/r1/a", base=g.head(repo))
    assert not (wt / "junk.txt").exists()
    assert g.head(wt) == g.head(repo)


def test_linked_dirs_are_visible_and_survive_removal(repo):
    write(repo, "node_modules/pkg/index.js", "module.exports = 1\n")
    g.exclude(repo, "node_modules/")
    wt = wt_path(repo, "a")
    g.add_worktree(repo, wt, "orch/r1/a", base=g.head(repo))
    g.link_dirs(repo, wt, ["node_modules", "missing_dir"])
    assert (wt / "node_modules" / "pkg" / "index.js").read_text(encoding="utf-8") == "module.exports = 1\n"
    assert not (wt / "missing_dir").exists()
    g.remove_worktree(repo, wt)
    assert not wt.exists()
    # удаление копии не должно задеть оригинал
    assert (repo / "node_modules" / "pkg" / "index.js").exists()


def test_delete_branch(repo):
    wt = wt_path(repo, "a")
    g.add_worktree(repo, wt, "orch/r1/a", base=g.head(repo))
    g.remove_worktree(repo, wt)
    g.delete_branch(repo, "orch/r1/a")
    assert not g.branch_exists(repo, "orch/r1/a")


def test_changed_files_sees_modified_deleted_and_new(repo):
    write(repo, "t1.py", "a\n")
    write(repo, "t2.py", "b\n")
    g.commit_all(repo, "tests")
    since = g.head(repo)
    assert g.changed_files(repo, since, ["t1.py", "t2.py", "t3.py"]) == []
    write(repo, "t1.py", "hacked\n")
    (repo / "t2.py").unlink()
    write(repo, "t3.py", "new\n")
    write(repo, "other.py", "not watched\n")
    assert sorted(g.changed_files(repo, since, ["t1.py", "t2.py", "t3.py"])) == ["t1.py", "t2.py", "t3.py"]


def test_restore_files(repo):
    write(repo, "t1.py", "original\n")
    g.commit_all(repo, "tests")
    since = g.head(repo)
    write(repo, "t1.py", "hacked\n")
    g.restore_files(repo, since, ["t1.py"])
    assert (repo / "t1.py").read_text(encoding="utf-8") == "original\n"


def test_diff_against_base(repo):
    base = g.head(repo)
    write(repo, "calc.py", "def add(a, b):\n    return a + b\n")
    g.commit_all(repo, "calc")
    d = g.diff(repo, base)
    assert "calc.py" in d and "return a + b" in d
