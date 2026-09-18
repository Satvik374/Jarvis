"""Hermetic regressions for execution status; never launch a shell or load config."""

import queue
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from jarvis.tools import registry, session_exec, system


@pytest.fixture(autouse=True)
def no_processes(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("a test attempted to launch a real process")

    monkeypatch.setattr(system.subprocess, "run", forbidden)
    monkeypatch.setattr(session_exec.subprocess, "Popen", forbidden)
    monkeypatch.setattr(system.os, "environ", {})
    monkeypatch.setattr(session_exec.Path, "home", lambda: session_exec.Path("."))


@pytest.fixture
def session(monkeypatch):
    session = session_exec.InteractiveSession.__new__(session_exec.InteractiveSession)
    session.name = "test"
    session.shell_type = "powershell"
    session.cwd = "."
    session.commands_run = 0
    session.created_at = session.last_used = 0
    session._closed = False
    session._lock = threading.Lock()
    session.proc = Mock(returncode=None)
    session.proc.poll.return_value = None
    session._out_queue = queue.Queue()
    clock = [0.0]
    monkeypatch.setattr(session_exec.time, "time", lambda: clock[0])
    monkeypatch.setattr(session_exec.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(session_exec.uuid, "uuid4", lambda: SimpleNamespace(hex="12345678"))
    original_get = session._out_queue.get

    def get(*args, **kwargs):
        if kwargs.get("block", True):
            clock[0] += 0.25
        return original_get(block=False)

    monkeypatch.setattr(session._out_queue, "get", get)
    manager = session_exec.SessionManager()
    manager._sessions["test"] = session
    monkeypatch.setattr(session_exec, "get_session_manager", lambda: manager)
    return session


def dispatch(action, **args):
    cfg = SimpleNamespace(safety=SimpleNamespace(blocked_command_patterns=("blocked",), allow_paths=()))
    return registry.execute(action, args, obs=None, cfg=cfg)


@pytest.mark.parametrize("output", ["", "partial output\n", "x" * 9000], ids=["empty", "partial", "truncated"])
def test_timeout_is_failure_and_invalidates_session(session, output):
    session.proc.stdin.flush.side_effect = lambda: session._out_queue.put(output) if output else None
    result = dispatch("session_exec", name="test", command="mock command", timeout=1)
    assert result.ok is False
    assert "timed out" in result.message
    assert "successfully" not in result.message
    if output:
        assert output.strip()[:20] in result.message
    assert not session.is_alive()
    assert session.proc.terminate.called or session.proc.kill.called
    session.proc.stdin.reset_mock()
    again = session.execute("must not run")
    assert "terminated" in again
    session.proc.stdin.write.assert_not_called()


@pytest.mark.parametrize("output", ["", "refused: this is ordinary command output\n"])
def test_sentinel_completion_is_success(session, output):
    def flush():
        if output:
            session._out_queue.put(output)
        session._out_queue.put("__JARVIS_DONE_12345678__\n")

    session.proc.stdin.flush.side_effect = flush
    result = dispatch("session_exec", name="test", command="mock command")
    assert result.ok is True
    assert result.needs_observe is False
    assert output.strip() in result.message
    session.proc.terminate.assert_not_called()


@pytest.mark.parametrize("failure", ["eof", "exit", "write", "missing_stdin"])
def test_session_transport_failure_propagates(session, failure):
    if failure == "eof":
        session.proc.stdin.flush.side_effect = lambda: session._out_queue.put(None)
    elif failure == "exit":
        def flush():
            session.proc.returncode = 7
            session.proc.poll.return_value = 7
        session.proc.stdin.flush.side_effect = flush
    elif failure == "write":
        session.proc.stdin.write.side_effect = BrokenPipeError("pipe closed")
    else:
        session.proc.stdin = None
    result = dispatch("session_exec", name="test", command="mock command", timeout=1)
    assert result.ok is False
    assert result.needs_observe is False


def test_already_dead_session_is_failure(session):
    session.proc.returncode = 7
    session.proc.poll.return_value = 7
    result = session.execute("mock command")
    assert result.ok is False
    assert "terminated" in result
    session.proc.stdin.write.assert_not_called()


@pytest.mark.parametrize("args", [{"command": ""}, {"command": "blocked"}, {"op": "invalid"}, {"op": "close", "name": "missing"}])
def test_session_validation_failure_propagates(session, args):
    assert dispatch("session_exec", **args).ok is False
    session.proc.stdin.write.assert_not_called()


@pytest.mark.parametrize("action", ["run_command", "python"])
@pytest.mark.parametrize("returncode", [0, 1, -9])
def test_one_shot_exit_status_propagates(monkeypatch, action, returncode):
    run = Mock(return_value=SimpleNamespace(returncode=returncode, stdout="result", stderr="diagnostic"))
    monkeypatch.setattr(system.subprocess, "run", run)
    result = dispatch(action, command="mock command", code="mock code")
    assert result.ok is (returncode == 0)
    assert f"exit code {returncode}" in result.message
    assert "result" in result.message and "diagnostic" in result.message
    assert result.needs_observe is False
    run.assert_called_once()


@pytest.mark.parametrize("action", ["run_command", "python"])
@pytest.mark.parametrize("error", [system.subprocess.TimeoutExpired("mock", 1), OSError("mock launch failure")])
def test_one_shot_launch_and_timeout_failure(monkeypatch, action, error):
    monkeypatch.setattr(system.subprocess, "run", Mock(side_effect=error))
    assert dispatch(action, command="mock command", code="mock code").ok is False


def test_refused_shell_and_empty_python_do_not_launch():
    assert dispatch("run_command", command="blocked").ok is False
    assert dispatch("python", code="").ok is False


@pytest.mark.parametrize("op", ["start", "exec"])
def test_session_launch_failure_propagates(monkeypatch, op):
    monkeypatch.setattr(session_exec, "get_session_manager", session_exec.SessionManager)
    monkeypatch.setattr(session_exec.subprocess, "Popen", Mock(side_effect=OSError("mock launch failure")))
    result = dispatch("session_exec", op=op, command="mock command")
    assert result.ok is False
    assert "failed to start session" in result.message


def test_timeout_restarts_instead_of_reusing_stream(session, monkeypatch):
    session.proc.terminate.side_effect = OSError("cannot terminate")
    session.proc.kill.side_effect = OSError("cannot kill")
    assert dispatch("session_exec", name="test", command="mock command", timeout=1).ok is False
    assert not session.is_alive()
    replacement = Mock()
    replacement.execute.return_value = system.ExecutionOutput("new session output")
    constructor = Mock(return_value=replacement)
    monkeypatch.setattr(session_exec, "InteractiveSession", constructor)
    session.proc.stdin.reset_mock()
    assert dispatch("session_exec", name="test", command="next command").ok is True
    constructor.assert_called_once()
    replacement.execute.assert_called_once_with("next command", timeout=30.0)
    session.proc.stdin.write.assert_not_called()


def test_session_lifecycle_uses_mock_process_only(monkeypatch):
    process = Mock(returncode=None, pid=123)
    process.poll.return_value = None
    popen = Mock(return_value=process)
    monkeypatch.setattr(session_exec.subprocess, "Popen", popen)
    monkeypatch.setattr(session_exec.threading, "Thread", Mock())
    manager = session_exec.SessionManager()
    monkeypatch.setattr(session_exec, "get_session_manager", lambda: manager)
    assert dispatch("session_exec", op="start", name="test", cwd=".").ok is True
    assert dispatch("session_exec", op="start", name="test", cwd=".").ok is True
    assert "test" in dispatch("session_exec", op="list").message
    assert dispatch("session_exec", op="close", name="test").ok is True
    assert "no active" in dispatch("session_exec", op="list").message
    assert dispatch("session_exec", op="close", name="").ok is True
    popen.assert_called_once()
    process.terminate.assert_called_once()


@pytest.mark.parametrize("function,args", [(system.run_command, ("mock",)), (system.run_python, ("mock",))])
def test_direct_callers_keep_string_compatibility(monkeypatch, function, args):
    monkeypatch.setattr(system.subprocess, "run", Mock(return_value=SimpleNamespace(returncode=0, stdout="ok", stderr="")))
    result = function(*args)
    assert isinstance(result, str)
    assert result.startswith("exit code 0")
