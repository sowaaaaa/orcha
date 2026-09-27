"""Git: worktree для каждой подзадачи, коммиты, слияние и откат."""
import os
import shutil
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


def git(cwd, *args, check: bool = True) -> str:
    r = subprocess.run(["git", "-c", "gc.auto=0", *args], cwd=cwd, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()}")
    return r.stdout.strip()


def _ok(cwd, *args) -> bool:
    return subprocess.run(["git", "-c", "gc.auto=0", *args], cwd=cwd, capture_output=True).returncode == 0


def ensure_repo(path) -> Path:
    """Проверяет, что это git-репозиторий с коммитами; возвращает его корень."""
    path = Path(path)
    if not path.is_dir():
        raise GitError(f"папка не найдена: {path}")
    if not _ok(path, "rev-parse", "--is-inside-work-tree"):
        raise GitError(f"{path} — не git-репозиторий. Создайте его: git init && git add -A && git commit -m init")
    if not _ok(path, "rev-parse", "--verify", "-q", "HEAD"):
        raise GitError("в git-репозитории нет ни одного коммита. Сделайте первый: git add -A && git commit -m init")
    return Path(git(path, "rev-parse", "--show-toplevel"))


def head(cwd) -> str:
    return git(cwd, "rev-parse", "HEAD")


def exclude(repo, pattern: str) -> None:
    """Локально игнорировать путь (через .git/info/exclude, без правки .gitignore)."""
    common = Path(git(repo, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = Path(repo) / common
    f = common / "info" / "exclude"
    f.parent.mkdir(parents=True, exist_ok=True)
    text = f.read_text(encoding="utf-8") if f.exists() else ""
    if pattern not in text.splitlines():
        f.write_text(text + ("" if text.endswith("\n") or not text else "\n") + pattern + "\n", encoding="utf-8")


def add_worktree(repo, path, branch: str, base: str | None = None) -> None:
    """base задан — ветка создаётся (или сбрасывается) от base; нет — берётся существующая ветка."""
    path = Path(path)
    remove_worktree(repo, path)  # остатки прошлого сбоя
    path.parent.mkdir(parents=True, exist_ok=True)
    if base is None:
        git(repo, "worktree", "add", str(path), branch)
    else:
        git(repo, "worktree", "add", "-B", branch, str(path), base)


def remove_worktree(repo, path) -> None:
    path = Path(path)
    if path.exists():
        # Сначала убираем ссылки, чтобы удаление копии не прошло по ним в оригинал.
        for child in path.iterdir():
            if _is_link(child):
                _unlink(child)
        if not _ok(repo, "worktree", "remove", "--force", str(path)):
            shutil.rmtree(path, ignore_errors=True)
    git(repo, "worktree", "prune")


def link_dirs(repo, wt, names) -> None:
    """Подключает в worktree неотслеживаемые папки (node_modules, .venv) ссылкой на оригинал."""
    for name in names:
        src, dst = Path(repo) / name, Path(wt) / name
        if not src.is_dir() or dst.exists():
            continue
        if os.name == "nt":  # junction не требует прав администратора
            subprocess.run(["cmd", "/c", "mklink", "/J", str(dst), str(src)], check=True, capture_output=True)
        else:
            os.symlink(src, dst, target_is_directory=True)


def _is_link(p: Path) -> bool:
    return p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction())


def _unlink(p: Path) -> None:
    try:
        os.unlink(p)
    except OSError:
        os.rmdir(p)  # junction на Windows удаляется как пустая папка, цель не трогается


def _identity(cwd) -> list[str]:
    if _ok(cwd, "config", "user.email"):
        return []
    return ["-c", "user.name=orch", "-c", "user.email=orch@localhost"]


def commit_all(cwd, message: str) -> bool:
    git(cwd, "add", "-A")
    if _ok(cwd, "diff", "--cached", "--quiet"):
        return False
    git(cwd, *_identity(cwd), "commit", "-q", "-m", message)
    return True


def merge(cwd, branch: str, message: str) -> bool:
    if _ok(cwd, *_identity(cwd), "merge", "--no-ff", "--no-edit", "-m", message, branch):
        return True
    _ok(cwd, "merge", "--abort")
    return False


def undo_last_merge(cwd) -> None:
    git(cwd, "reset", "--hard", "HEAD~1")


def diff(cwd, base: str) -> str:
    return git(cwd, "diff", base, "HEAD")


def changed_files(cwd, since: str, paths) -> list[str]:
    """Какие из paths изменены, удалены или созданы относительно коммита since (включая незакоммиченное)."""
    paths = list(paths)
    tracked = git(cwd, "diff", "--name-only", since, "--", *paths).splitlines()
    untracked = git(cwd, "ls-files", "--others", "--exclude-standard", "--", *paths).splitlines()
    return sorted({p for p in tracked + untracked if p})


def restore_files(cwd, commit: str, paths) -> None:
    for p in paths:
        if _ok(cwd, "cat-file", "-e", f"{commit}:{p}"):
            git(cwd, "checkout", commit, "--", p)
        elif (Path(cwd) / p).exists():
            (Path(cwd) / p).unlink()


def branch_exists(repo, branch: str) -> bool:
    return _ok(repo, "show-ref", "--verify", "-q", f"refs/heads/{branch}")


def delete_branch(repo, branch: str) -> None:
    git(repo, "branch", "-D", branch)
