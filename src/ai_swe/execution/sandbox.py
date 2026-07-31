"""
Command execution sandbox for the Execution agent.

Two implementations of a common `Sandbox` interface:

  * `DockerSandbox` -- runs commands inside a throwaway `docker run --rm`
    container, with the repository bind-mounted at `/work`. This isolates
    whatever a test suite / build script does (arbitrary code from an LLM-
    authored patch) from the host running the agent.
  * `LocalSandbox` -- runs commands directly as a subprocess on the host,
    with the same timeout semantics. Used when Docker isn't available (e.g.
    local development, or CI without docker-in-docker).

`get_sandbox()` is the entry point callers should use: it probes for a
reachable Docker daemon and falls back to `LocalSandbox` automatically, so
the Execution agent gets isolation wherever Docker is available without
hard-requiring it everywhere.
"""

from __future__ import annotations

import asyncio
import shutil
import time
from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import BaseModel, Field

from ai_swe.indexer.models import Language
from ai_swe.logging_config import get_logger

logger = get_logger(__name__)

# Timeout (seconds) applied to a command when the caller doesn't specify one.
DEFAULT_TIMEOUT = 300.0

# Base image chosen per detected language. Anything unrecognised (or
# `language=None`) falls back to a plain Debian image with no language
# runtime -- good enough for shell commands but not for running tests.
_LANGUAGE_IMAGES: dict[Language, str] = {
    Language.PYTHON: "python:3.12-slim",
    Language.JAVASCRIPT: "node:20-slim",
    Language.TYPESCRIPT: "node:20-slim",
    Language.GO: "golang:1.22-slim",
    Language.JAVA: "eclipse-temurin:21-jdk-jammy",
    Language.CPP: "gcc:13",
}
DEFAULT_IMAGE = "debian:bookworm-slim"


def image_for_language(language: Language | None) -> str:
    """Pick a base Docker image for `language`, or a generic fallback."""
    if language is None:
        return DEFAULT_IMAGE
    return _LANGUAGE_IMAGES.get(language, DEFAULT_IMAGE)


class CommandResult(BaseModel):
    """The outcome of running a single command in a `Sandbox`."""

    exit_code: int = Field(description="Process exit code, or -1 if killed for timing out.")
    stdout: str
    stderr: str
    duration: float = Field(description="Wall-clock seconds the command took to run.")
    timed_out: bool = Field(default=False, description="Whether the command exceeded its timeout and was killed.")

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class Sandbox(ABC):
    """Common interface for running shell commands against a repo checkout."""

    @abstractmethod
    async def run(
        self,
        command: list[str],
        cwd: str | Path,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> CommandResult:
        """
        Run `command` with working directory `cwd`, killing it if it runs
        longer than `timeout` seconds.

        Never raises for a non-zero exit code or a timeout -- both are
        reported via the returned `CommandResult` so callers can inspect
        every outcome uniformly.
        """
        raise NotImplementedError


class LocalSandbox(Sandbox):
    """Runs commands directly on the host via `asyncio.create_subprocess_exec`."""

    async def run(
        self,
        command: list[str],
        cwd: str | Path,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> CommandResult:
        start = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            duration = time.monotonic() - start
            logger.warning("Command timed out after %.1fs: %s", timeout, " ".join(command))
            return CommandResult(
                exit_code=-1,
                stdout="",
                stderr=f"Command timed out after {timeout:.0f}s: {' '.join(command)}",
                duration=duration,
                timed_out=True,
            )

        duration = time.monotonic() - start
        return CommandResult(
            exit_code=proc.returncode if proc.returncode is not None else -1,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            duration=duration,
        )


class DockerSandbox(Sandbox):
    """
    Runs commands inside an ephemeral `docker run --rm` container.

    The repository at `cwd` is bind-mounted read-write at `/work` inside the
    container (test runs commonly need to write `__pycache__`,
    `node_modules`, coverage files, etc.), which is also used as the
    container's working directory. The base image is either given explicitly
    or inferred from `language` via `image_for_language()`.
    """

    def __init__(self, image: str | None = None, *, language: Language | None = None) -> None:
        self.image = image or image_for_language(language)
        self._local = LocalSandbox()

    async def run(
        self,
        command: list[str],
        cwd: str | Path,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> CommandResult:
        repo_path = Path(cwd).expanduser().resolve()
        docker_command = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{repo_path}:/work",
            "-w",
            "/work",
            self.image,
            *command,
        ]
        # `docker run --rm` tearing down the container is what actually kills
        # `command` on timeout; the outer `timeout` here bounds the whole
        # `docker run` invocation (image pull/start included).
        return await self._local.run(docker_command, cwd=repo_path, timeout=timeout)


async def _docker_available() -> bool:
    """Probe for a usable Docker daemon by running `docker info`."""
    if shutil.which("docker") is None:
        return False
    try:
        result = await LocalSandbox().run(["docker", "info"], cwd=Path.cwd(), timeout=10.0)
    except OSError:
        return False
    return result.success


async def get_sandbox(*, language: Language | None = None, image: str | None = None) -> Sandbox:
    """
    Return the best available `Sandbox` implementation.

    Prefers `DockerSandbox` when a Docker daemon is reachable; falls back to
    `LocalSandbox` otherwise. This means the Execution agent gets container
    isolation wherever Docker is available (CI, production) without
    requiring it for local development.
    """
    if await _docker_available():
        chosen_image = image or image_for_language(language)
        logger.info("Docker available; using DockerSandbox (image=%s)", chosen_image)
        return DockerSandbox(image=image, language=language)

    logger.info("Docker not available; falling back to LocalSandbox")
    return LocalSandbox()
