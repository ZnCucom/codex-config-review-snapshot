from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from .model import GitError, PolicyError


SOURCE_READ_COMMANDS = frozenset({"branch", "diff", "ls-files", "rev-parse", "status"})
REPOSITORY_ENVIRONMENT = frozenset(
    {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_NAMESPACE",
    }
)


Executor = Callable[..., subprocess.CompletedProcess[bytes]]


def no_window_creation_flags() -> int:
    if os.name != "nt":
        return 0
    return int(subprocess.CREATE_NO_WINDOW)


def _sanitized_environment(base: Mapping[str, str]) -> dict[str, str]:
    environment = {name: value for name, value in base.items() if name not in REPOSITORY_ENVIRONMENT}
    environment.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_PAGER": "cat",
            "GIT_EXTERNAL_DIFF": "",
        }
    )
    return environment


class GitRunner:
    def __init__(
        self,
        executable: str | Path,
        *,
        base_env: Mapping[str, str] | None = None,
        executor: Executor | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.executable = str(executable)
        self.base_env = dict(os.environ if base_env is None else base_env)
        self.executor = subprocess.run if executor is None else executor
        self.timeout = timeout

    def _run(
        self,
        command: Sequence[str],
        *,
        environment: Mapping[str, str],
        input_bytes: bytes | None = None,
        timeout: float | None = None,
        allowed_returncodes: frozenset[int] = frozenset({0}),
        cancel_check: Callable[[], bool] | None = None,
    ) -> bytes:
        if cancel_check is not None and self.executor is subprocess.run:
            return self._run_cancellable(
                command,
                environment=environment,
                input_bytes=input_bytes,
                timeout=self.timeout if timeout is None else timeout,
                allowed_returncodes=allowed_returncodes,
                cancel_check=cancel_check,
            )
        try:
            result = self.executor(
                list(command),
                env=dict(environment),
                input=input_bytes,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout if timeout is None else timeout,
                check=False,
                creationflags=no_window_creation_flags(),
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise GitError(f"Git process failed to start or timed out: {command[1] if len(command) > 1 else 'git'}") from error
        if result.returncode not in allowed_returncodes:
            diagnostic = result.stderr.decode("utf-8", errors="replace").strip()
            if len(diagnostic) > 500:
                diagnostic = diagnostic[:500] + "..."
            raise GitError(f"Git command failed with exit {result.returncode}: {diagnostic}")
        return result.stdout

    def _run_cancellable(
        self,
        command: Sequence[str],
        *,
        environment: Mapping[str, str],
        input_bytes: bytes | None,
        timeout: float,
        allowed_returncodes: frozenset[int],
        cancel_check: Callable[[], bool],
    ) -> bytes:
        try:
            process = subprocess.Popen(
                list(command),
                env=dict(environment),
                stdin=subprocess.PIPE if input_bytes is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=no_window_creation_flags(),
            )
        except OSError as error:
            raise GitError(f"Git process failed to start: {command[1] if len(command) > 1 else 'git'}") from error
        if input_bytes is not None:
            assert process.stdin is not None
            process.stdin.write(input_bytes)
            process.stdin.close()
            process.stdin = None
        deadline = time.monotonic() + timeout
        stdout = b""
        stderr = b""
        while True:
            if cancel_check():
                process.terminate()
                try:
                    stdout, stderr = process.communicate(timeout=1.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    stdout, stderr = process.communicate()
                raise GitError("Git network operation was cancelled; remote outcome requires readback")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.terminate()
                try:
                    stdout, stderr = process.communicate(timeout=1.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    stdout, stderr = process.communicate()
                raise GitError(f"Git process failed to start or timed out: {command[1] if len(command) > 1 else 'git'}")
            try:
                stdout, stderr = process.communicate(timeout=min(0.1, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode not in allowed_returncodes:
            diagnostic = stderr.decode("utf-8", errors="replace").strip()
            if len(diagnostic) > 500:
                diagnostic = diagnostic[:500] + "..."
            raise GitError(f"Git command failed with exit {process.returncode}: {diagnostic}")
        return stdout

    def source(self, repo: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
        if (not arguments or arguments[0] not in SOURCE_READ_COMMANDS) and arguments != ("worktree", "list", "--porcelain", "-z"):
            command = arguments[0] if arguments else "<empty>"
            raise PolicyError(f"source Git command is not read-only: {command}")
        environment = _sanitized_environment(self.base_env)
        environment["GIT_OPTIONAL_LOCKS"] = "0"
        return self._run(
            [self.executable, "-C", str(repo), *arguments],
            environment=environment,
            input_bytes=input_bytes,
        )

    def snapshot(
        self,
        git_dir: Path,
        *arguments: str,
        input_bytes: bytes | None = None,
        timeout: float | None = None,
        environment_overrides: Mapping[str, str] | None = None,
    ) -> bytes:
        environment = _sanitized_environment(self.base_env)
        environment.update(
            {
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_ATTR_NOSYSTEM": "1",
            }
        )
        if environment_overrides:
            environment.update(environment_overrides)
        hooks_path = "NUL" if os.name == "nt" else os.devnull
        return self._run(
            [
                self.executable,
                "--git-dir",
                str(git_dir),
                "-c",
                f"core.hooksPath={hooks_path}",
                *arguments,
            ],
            environment=environment,
            input_bytes=input_bytes,
            timeout=timeout,
        )

    def init_bare(self, git_dir: Path) -> None:
        git_dir = git_dir.resolve(strict=False)
        git_dir.parent.mkdir(parents=True, exist_ok=True)
        environment = _sanitized_environment(self.base_env)
        environment.update(
            {
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_ATTR_NOSYSTEM": "1",
            }
        )
        self._run(
            [self.executable, "init", "--bare", str(git_dir)],
            environment=environment,
        )

    def effective_push_url(self, url: str, git_dir: Path | None = None) -> str:
        environment = _sanitized_environment(self.base_env)
        command = [self.executable]
        if git_dir is not None:
            command.extend(["--git-dir", str(git_dir)])
        command.extend(
            [
                "config",
                "--get-regexp",
                r"^url\..*\.(pushInsteadOf|insteadOf)$",
            ]
        )
        output = self._run(
            command,
            environment=environment,
            allowed_returncodes=frozenset({0, 1}),
        )
        push_rules: list[tuple[str, str]] = []
        general_rules: list[tuple[str, str]] = []
        for line in output.decode("utf-8", errors="strict").splitlines():
            key, separator, prefix = line.partition(" ")
            if not separator or not key.lower().startswith("url."):
                continue
            lowered = key.lower()
            if lowered.endswith(".pushinsteadof"):
                replacement = key[4 : -len(".pushInsteadOf")]
                push_rules.append((prefix, replacement))
            elif lowered.endswith(".insteadof"):
                replacement = key[4 : -len(".insteadOf")]
                general_rules.append((prefix, replacement))
        matches = [rule for rule in push_rules if url.startswith(rule[0])]
        if not matches:
            matches = [rule for rule in general_rules if url.startswith(rule[0])]
        if not matches:
            return url
        prefix, replacement = max(matches, key=lambda rule: len(rule[0]))
        return replacement + url[len(prefix) :]

    def network(
        self,
        git_dir: Path,
        *arguments: str,
        input_bytes: bytes | None = None,
        timeout: float | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> bytes:
        environment = _sanitized_environment(self.base_env)
        environment.update({"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"})
        hooks_path = "NUL" if os.name == "nt" else os.devnull
        return self._run(
            [
                self.executable,
                "--git-dir",
                str(git_dir),
                "-c",
                f"core.hooksPath={hooks_path}",
                *arguments,
            ],
            environment=environment,
            input_bytes=input_bytes,
            timeout=timeout,
            cancel_check=cancel_check,
        )
