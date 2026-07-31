"""
Tests for `ai_swe.execution.sandbox`.

`LocalSandbox` is exercised directly against real subprocesses (fast,
built-in commands like `python -c ...`) -- no Docker required. `DockerSandbox`
and `get_sandbox()`'s daemon probe are tested by mocking `LocalSandbox.run`
so these tests never actually need Docker installed.
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, patch

import pytest

from ai_swe.execution.sandbox import (
    CommandResult,
    DockerSandbox,
    LocalSandbox,
    get_sandbox,
    image_for_language,
)
from ai_swe.indexer.models import Language


class TestCommandResult:
    def test_success_true_on_zero_exit_and_no_timeout(self):
        result = CommandResult(exit_code=0, stdout="", stderr="", duration=0.1)
        assert result.success is True

    def test_success_false_on_nonzero_exit(self):
        result = CommandResult(exit_code=1, stdout="", stderr="", duration=0.1)
        assert result.success is False

    def test_success_false_when_timed_out_even_with_zero_exit(self):
        result = CommandResult(exit_code=0, stdout="", stderr="", duration=0.1, timed_out=True)
        assert result.success is False


class TestLocalSandbox:
    @pytest.mark.asyncio
    async def test_captures_stdout_and_exit_code(self, tmp_path):
        sandbox = LocalSandbox()
        result = await sandbox.run(
            [sys.executable, "-c", "print('hello')"], cwd=tmp_path, timeout=10.0
        )

        assert result.exit_code == 0
        assert "hello" in result.stdout
        assert result.timed_out is False

    @pytest.mark.asyncio
    async def test_captures_nonzero_exit_and_stderr(self, tmp_path):
        sandbox = LocalSandbox()
        result = await sandbox.run(
            [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"],
            cwd=tmp_path,
            timeout=10.0,
        )

        assert result.exit_code == 3
        assert "boom" in result.stderr
        assert result.success is False

    @pytest.mark.asyncio
    async def test_enforces_timeout(self, tmp_path):
        sandbox = LocalSandbox()
        result = await sandbox.run(
            [sys.executable, "-c", "import time; time.sleep(5)"], cwd=tmp_path, timeout=0.2
        )

        assert result.timed_out is True
        assert result.exit_code == -1
        assert "timed out" in result.stderr

    @pytest.mark.asyncio
    async def test_runs_in_given_cwd(self, tmp_path):
        (tmp_path / "marker.txt").write_text("here", encoding="utf-8")
        sandbox = LocalSandbox()
        result = await sandbox.run(
            [sys.executable, "-c", "import pathlib; print(pathlib.Path('marker.txt').read_text())"],
            cwd=tmp_path,
            timeout=10.0,
        )

        assert "here" in result.stdout


class TestImageForLanguage:
    def test_known_language_maps_to_expected_image(self):
        assert image_for_language(Language.PYTHON) == "python:3.12-slim"
        assert image_for_language(Language.JAVASCRIPT) == "node:20-slim"

    def test_none_falls_back_to_default_image(self):
        assert image_for_language(None) == "debian:bookworm-slim"


class TestDockerSandbox:
    @pytest.mark.asyncio
    async def test_builds_expected_docker_run_command(self, tmp_path):
        sandbox = DockerSandbox(language=Language.PYTHON)

        with patch.object(
            LocalSandbox,
            "run",
            new=AsyncMock(return_value=CommandResult(exit_code=0, stdout="ok", stderr="", duration=0.1)),
        ) as mock_run:
            result = await sandbox.run(["pytest"], cwd=tmp_path, timeout=30.0)

        assert result.stdout == "ok"
        called_command = mock_run.call_args.args[0]
        assert called_command[:3] == ["docker", "run", "--rm"]
        assert f"{tmp_path.resolve()}:/work" in called_command
        assert "-w" in called_command and "/work" in called_command
        assert called_command[-2:] == ["python:3.12-slim", "pytest"]


class TestGetSandbox:
    @pytest.mark.asyncio
    async def test_falls_back_to_local_when_docker_binary_missing(self):
        with patch("ai_swe.execution.sandbox.shutil.which", return_value=None):
            sandbox = await get_sandbox()
        assert isinstance(sandbox, LocalSandbox)

    @pytest.mark.asyncio
    async def test_falls_back_to_local_when_docker_info_fails(self):
        with (
            patch("ai_swe.execution.sandbox.shutil.which", return_value="/usr/bin/docker"),
            patch.object(
                LocalSandbox,
                "run",
                new=AsyncMock(
                    return_value=CommandResult(exit_code=1, stdout="", stderr="daemon not running", duration=0.1)
                ),
            ),
        ):
            sandbox = await get_sandbox()
        assert isinstance(sandbox, LocalSandbox)

    @pytest.mark.asyncio
    async def test_uses_docker_when_daemon_reachable(self):
        with (
            patch("ai_swe.execution.sandbox.shutil.which", return_value="/usr/bin/docker"),
            patch.object(
                LocalSandbox,
                "run",
                new=AsyncMock(return_value=CommandResult(exit_code=0, stdout="", stderr="", duration=0.1)),
            ),
        ):
            sandbox = await get_sandbox(language=Language.PYTHON)
        assert isinstance(sandbox, DockerSandbox)
        assert sandbox.image == "python:3.12-slim"
