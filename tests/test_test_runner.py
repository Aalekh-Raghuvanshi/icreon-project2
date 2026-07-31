"""
Tests for `ai_swe.execution.test_runner`.

`detect_test_framework()` is tested purely against marker files on disk.
`run_tests()` is tested against a `FakeSandbox` test double that records the
command it was asked to run and returns a canned `CommandResult`, so these
tests never spawn a real subprocess or need Docker/pytest/npm installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_swe.execution.sandbox import CommandResult, Sandbox
from ai_swe.execution.test_runner import detect_test_framework, run_tests


class FakeSandbox(Sandbox):
    """Records the last command it was asked to run and returns a canned result."""

    def __init__(self, result: CommandResult) -> None:
        self.result = result
        self.last_command: list[str] | None = None
        self.last_cwd: str | Path | None = None

    async def run(self, command: list[str], cwd: str | Path, timeout: float = 300.0) -> CommandResult:
        self.last_command = command
        self.last_cwd = cwd
        return self.result


class TestDetectTestFramework:
    def test_pyproject_toml_detected_as_pytest(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        framework = detect_test_framework(tmp_path)
        assert framework is not None
        assert framework.name == "pytest"
        assert framework.command == ["python", "-m", "pytest"]

    def test_setup_py_detected_as_pytest(self, tmp_path):
        (tmp_path / "setup.py").write_text("", encoding="utf-8")
        framework = detect_test_framework(tmp_path)
        assert framework is not None
        assert framework.name == "pytest"

    def test_package_json_with_test_script_uses_npm(self, tmp_path):
        (tmp_path / "package.json").write_text('{"scripts": {"test": "jest"}}', encoding="utf-8")
        framework = detect_test_framework(tmp_path)
        assert framework is not None
        assert framework.name == "npm"
        assert framework.command == ["npm", "test", "--silent"]

    def test_package_json_without_test_script_falls_back_to_jest(self, tmp_path):
        (tmp_path / "package.json").write_text('{"scripts": {}}', encoding="utf-8")
        framework = detect_test_framework(tmp_path)
        assert framework is not None
        assert framework.name == "jest"

    def test_malformed_package_json_falls_back_to_jest(self, tmp_path):
        (tmp_path / "package.json").write_text("not json", encoding="utf-8")
        framework = detect_test_framework(tmp_path)
        assert framework is not None
        assert framework.name == "jest"

    def test_go_mod_detected_as_go(self, tmp_path):
        (tmp_path / "go.mod").write_text("module example.com/x\n", encoding="utf-8")
        framework = detect_test_framework(tmp_path)
        assert framework is not None
        assert framework.name == "go"
        assert framework.command == ["go", "test", "./..."]

    def test_pom_xml_detected_as_maven(self, tmp_path):
        (tmp_path / "pom.xml").write_text("<project/>", encoding="utf-8")
        framework = detect_test_framework(tmp_path)
        assert framework is not None
        assert framework.name == "maven"

    def test_empty_repo_detects_nothing(self, tmp_path):
        assert detect_test_framework(tmp_path) is None

    def test_pytest_takes_priority_over_package_json(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        (tmp_path / "package.json").write_text('{"scripts": {"test": "jest"}}', encoding="utf-8")
        framework = detect_test_framework(tmp_path)
        assert framework is not None
        assert framework.name == "pytest"


class TestRunTests:
    @pytest.mark.asyncio
    async def test_no_framework_returns_empty_list(self, tmp_path):
        sandbox = FakeSandbox(CommandResult(exit_code=0, stdout="", stderr="", duration=0.0))
        results = await run_tests(tmp_path, sandbox)
        assert results == []
        assert sandbox.last_command is None

    @pytest.mark.asyncio
    async def test_passing_pytest_run_produces_passed_result(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        sandbox = FakeSandbox(
            CommandResult(
                exit_code=0,
                stdout="===== 3 passed in 0.12s =====",
                stderr="",
                duration=0.12,
            )
        )

        results = await run_tests(tmp_path, sandbox)

        assert len(results) == 1
        assert results[0].passed is True
        assert "pytest suite" in results[0].name
        assert "3 passed in 0.12s" in results[0].name
        assert sandbox.last_command == ["python", "-m", "pytest"]

    @pytest.mark.asyncio
    async def test_failing_run_produces_failed_result(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        sandbox = FakeSandbox(
            CommandResult(
                exit_code=1,
                stdout="===== 1 failed, 2 passed in 0.30s =====",
                stderr="",
                duration=0.30,
            )
        )

        results = await run_tests(tmp_path, sandbox)

        assert len(results) == 1
        assert results[0].passed is False

    @pytest.mark.asyncio
    async def test_timed_out_run_is_marked_failed_and_labelled(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        sandbox = FakeSandbox(
            CommandResult(exit_code=-1, stdout="", stderr="Command timed out after 600s", duration=600.0, timed_out=True)
        )

        results = await run_tests(tmp_path, sandbox)

        assert len(results) == 1
        assert results[0].passed is False
        assert "timed out" in results[0].name

    @pytest.mark.asyncio
    async def test_output_is_truncated(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        long_output = "x" * 10_000
        sandbox = FakeSandbox(CommandResult(exit_code=0, stdout=long_output, stderr="", duration=0.1))

        results = await run_tests(tmp_path, sandbox)

        assert results[0].output is not None
        assert len(results[0].output) < 10_000
        assert "truncated" in results[0].output
