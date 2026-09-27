"""Запуск внешних процессов с таймаутом, который убивает всё дерево процессов."""
import os
import signal
import subprocess
from pathlib import Path


def run(cmd, cwd: Path, *, input: str | None = None, timeout: float | None = None,
        env: dict | None = None, shell: bool = False, merge_stderr: bool = False):
    """Возвращает (код, stdout, stderr, timed_out). Вывод декодируется как UTF-8."""
    p = subprocess.Popen(
        cmd, cwd=cwd, env=env, shell=shell,
        stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
        start_new_session=os.name != "nt",
    )
    data = input.encode("utf-8") if input is not None else None
    try:
        out, err = p.communicate(data, timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        _kill_tree(p)
        out, err = p.communicate()
        timed_out = True
    return p.returncode, _decode(out), _decode(err), timed_out


def _decode(b: bytes | None) -> str:
    return b.decode("utf-8", errors="replace") if b else ""


def _kill_tree(p: subprocess.Popen) -> None:
    # При shell=True таймаут убивал бы только оболочку, а внуки держали бы трубу открытой.
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
    else:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
