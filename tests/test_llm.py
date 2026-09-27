import json

import pytest

from orch.llm import ROLES, ClaudeCLI, LLMError, extract_json

MODELS = {"brain": "brain-model", "worker": "worker-model"}


class FakeRunner:
    def __init__(self, code=0, out="", err=""):
        self.code, self.out, self.err = code, out, err
        self.calls = []

    def __call__(self, cmd, cwd, input, timeout):
        self.calls.append({"cmd": cmd, "cwd": cwd, "input": input, "timeout": timeout})
        return self.code, self.out, self.err


def ok_json(result="ответ", **extra):
    return json.dumps({"type": "result", "is_error": False, "result": result,
                       "session_id": "sess-1", "total_cost_usd": 0.12, **extra})


def arg_after(cmd, flag):
    return cmd[cmd.index(flag) + 1]


def test_roles_map_kinds_to_models():
    assert ROLES == {"interview": "brain", "plan": "brain", "review": "brain",
                     "tests": "worker", "code": "worker"}


@pytest.mark.parametrize("kind", ["interview", "plan", "review"])
def test_brain_calls_are_read_only_and_use_brain_model(kind):
    cmd = ClaudeCLI(MODELS, exe="claude").command(kind)
    assert "-p" in cmd and "--restricted" in cmd
    assert arg_after(cmd, "--output-format") == "json"
    assert arg_after(cmd, "--model") == "brain-model"
    tools = arg_after(cmd, "--tools")
    assert "Read" in tools and "Edit" not in tools and "Write" not in tools and "Bash" not in tools
    assert "acceptEdits" not in cmd


@pytest.mark.parametrize("kind", ["tests", "code"])
def test_worker_calls_can_edit_and_use_worker_model(kind):
    cmd = ClaudeCLI(MODELS, exe="claude").command(kind)
    assert "--restricted" in cmd
    assert arg_after(cmd, "--model") == "worker-model"
    assert arg_after(cmd, "--permission-mode") == "acceptEdits"
    tools = arg_after(cmd, "--tools")
    assert "Edit" in tools and "Write" in tools and "Bash" not in tools


def test_session_is_resumed():
    cmd = ClaudeCLI(MODELS, exe="claude").command("code", session="abc")
    assert arg_after(cmd, "--resume") == "abc"
    assert "--resume" not in ClaudeCLI(MODELS, exe="claude").command("code")


def test_unknown_kind_is_rejected():
    with pytest.raises(ValueError):
        ClaudeCLI(MODELS, exe="claude").command("dance")


def test_ask_sends_prompt_via_stdin_and_parses_reply(tmp_path):
    run = FakeRunner(out=ok_json("готово"))
    llm = ClaudeCLI(MODELS, exe="claude", timeout=42, runner=run)
    reply = llm.ask("plan", "Составь план ✓", tmp_path)
    assert reply.text == "готово"
    assert reply.session_id == "sess-1"
    assert reply.cost_usd == pytest.approx(0.12)
    call = run.calls[0]
    assert call["input"] == "Составь план ✓"
    assert "Составь план ✓" not in call["cmd"]  # промпт не в аргументах командной строки
    assert call["cwd"] == tmp_path
    assert call["timeout"] == 42


@pytest.mark.parametrize(
    "runner, fragment",
    [
        (FakeRunner(code=1, out="", err="auth failed"), "auth failed"),
        (FakeRunner(out="это не json"), "JSON"),
        (FakeRunner(out=json.dumps({"is_error": True, "result": "limit reached"})), "limit reached"),
    ],
)
def test_ask_errors_are_reported(tmp_path, runner, fragment):
    llm = ClaudeCLI(MODELS, exe="claude", runner=runner)
    with pytest.raises(LLMError) as e:
        llm.ask("code", "x", tmp_path)
    assert fragment in str(e.value)


def test_missing_cli_gives_clear_error(monkeypatch):
    monkeypatch.setattr("orch.llm.shutil.which", lambda name: None)
    with pytest.raises(LLMError) as e:
        ClaudeCLI(MODELS)
    assert "Claude Code" in str(e.value)


@pytest.mark.parametrize(
    "text",
    [
        '{"a": 1, "b": [1, 2]}',
        'Вот план:\n```json\n{"a": 1, "b": [1, 2]}\n```\nГотово.',
        'Ответ: {"a": 1, "b": [1, 2]} — всё.',
    ],
)
def test_extract_json(text):
    assert extract_json(text) == {"a": 1, "b": [1, 2]}


@pytest.mark.parametrize("text", ["", "нет тут json", "{сломано", "[1, 2]"])
def test_extract_json_rejects_garbage(text):
    with pytest.raises(ValueError):
        extract_json(text)
