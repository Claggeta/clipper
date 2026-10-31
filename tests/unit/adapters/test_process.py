import logging
import subprocess
from pathlib import Path

import pytest

from clipper.adapters.media._process import format_failure, run_tool
from clipper.domain.errors import AudioExtractionError

_log = logging.getLogger("test_process")


def _completed(code: int, stderr: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["tool"], code, "", stderr)


def test_missing_executable_raises_given_error_type() -> None:
    with pytest.raises(AudioExtractionError, match="'no-such-tool' not found on PATH"):
        run_tool("no-such-tool", [], error_type=AudioExtractionError, log=_log)


def test_success_returns_result_and_passes_args(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    captured: list[list[str]] = []

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        captured.append(command)
        return subprocess.CompletedProcess(command, 0, "out", "")

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/tool")
    monkeypatch.setattr(subprocess, "run", fake_run)

    with caplog.at_level(logging.INFO, logger=_log.name):
        result = run_tool(
            "tool", ["-a", "b"], error_type=AudioExtractionError, log=_log
        )

    assert captured == [["tool", "-a", "b"]]
    assert result.returncode == 0
    assert caplog.records == []


def test_nonzero_exit_does_not_raise_and_logs_full_stderr(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    stderr = "\n".join(f"line {i}" for i in range(30))

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", stderr)

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/tool")
    monkeypatch.setattr(subprocess, "run", fake_run)

    with caplog.at_level(logging.INFO, logger=_log.name):
        result = run_tool("tool", [], error_type=AudioExtractionError, log=_log)

    assert result.returncode == 1
    assert len(caplog.records) == 1
    assert caplog.records[0].name == _log.name
    assert caplog.records[0].getMessage().startswith("tool stderr:")
    assert "line 0" in caplog.records[0].getMessage()
    assert "line 29" in caplog.records[0].getMessage()


def test_format_failure_keeps_last_ten_nonempty_lines() -> None:
    stderr = "\n".join(f"line {i}\n" for i in range(30))

    message = format_failure("tool", _completed(7, stderr))

    assert message.startswith("tool failed (exit 7): ")
    assert "line 29" in message
    assert "line 20" in message
    assert "line 19" not in message
    assert "\n\n" not in message


def test_format_failure_with_empty_stderr() -> None:
    assert format_failure("tool", _completed(1, "  \n")) == "tool failed (exit 1): "


def test_cwd_is_passed_to_the_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[object] = []

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        seen.append(kwargs.get("cwd"))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/tool")
    monkeypatch.setattr(subprocess, "run", fake_run)

    run_tool("tool", [], error_type=AudioExtractionError, log=_log)
    run_tool("tool", [], error_type=AudioExtractionError, log=_log, cwd=tmp_path)

    assert seen == [None, tmp_path]
