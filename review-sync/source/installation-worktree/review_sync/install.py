from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .fs import atomic_write_bytes, atomic_write_json, read_json
from .model import PolicyError


INSTALL_STATE = ".review-sync-install.json"
PROGRAM_NAME = "review-sync"
TASK_NAME = "Codex Review Sync v1"
AGENTS_BEGIN = "<!-- BEGIN GITHUB REVIEW CHECKPOINT SYNC V1 -->"
AGENTS_END = "<!-- END GITHUB REVIEW CHECKPOINT SYNC V1 -->"
AGENTS_SEGMENT = f"""{AGENTS_BEGIN}
## GitHub review checkpoint sync

Review Sync normally runs through lifecycle hooks, queue, the scheduled background sync, and remote SHA verification. Do not perform routine model-side status checks at task start or sync-now at task completion. Intervene only for an explicit sync request, pause/resume, background failure, safety decisions, remote divergence/quarantine, or a clearly necessary immediate sync. Before handling content the user forbids from upload, run and confirm `pause`; natural language alone is not the background hard switch. Report PENDING/UNKNOWN unless remote SHA verification completed. Background failure must not restart reasoning or cause an unbounded retry loop. Preserve project approval and hard-stop rules.
{AGENTS_END}
""".encode("utf-8")


@dataclass(frozen=True)
class SchedulerDefinition:
    task_name: str
    python_executable: Path
    script: Path
    data_root: Path
    codex_home: Path
    launcher: Path
    installation_id: str
    git_executable: Path
    gh_executable: Path
    interval_minutes: int = 10
    run_level: str = "LIMITED"
    user_scope: str = "CURRENT_USER"

    def as_dict(self) -> dict[str, object]:
        return {
            "task_name": self.task_name,
            "python_executable": str(self.python_executable),
            "script": str(self.script),
            "data_root": str(self.data_root),
            "codex_home": str(self.codex_home),
            "launcher": str(self.launcher),
            "installation_id": self.installation_id,
            "git_executable": str(self.git_executable),
            "gh_executable": str(self.gh_executable),
            "interval_minutes": self.interval_minutes,
            "run_level": self.run_level,
            "user_scope": self.user_scope,
        }

    def command_line(self) -> str:
        windowless_python = self.python_executable.with_name("pythonw.exe")
        return subprocess.list2cmdline([str(windowless_python), "-B", str(self.launcher)])


class Scheduler(Protocol):
    def install(self, definition: SchedulerDefinition) -> None: ...
    def remove(self, task_name: str) -> None: ...
    def status(self, task_name: str) -> dict[str, object]: ...


class WindowsTaskScheduler:
    def __init__(self, executable: str | Path = "schtasks.exe") -> None:
        self.executable = str(executable)

    def _run(self, *arguments: str, allowed: set[int] = {0}) -> subprocess.CompletedProcess[bytes]:
        result = subprocess.run(
            [self.executable, *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode not in allowed:
            raise PolicyError(f"Windows Task Scheduler command failed with exit {result.returncode}")
        return result

    def install(self, definition: SchedulerDefinition) -> None:
        if os.name != "nt":
            raise PolicyError("Windows Task Scheduler is unavailable on this platform")
        self._run(
            "/Create",
            "/TN",
            definition.task_name,
            "/TR",
            definition.command_line(),
            "/SC",
            "MINUTE",
            "/MO",
            str(definition.interval_minutes),
            "/RL",
            definition.run_level,
            "/F",
        )

    def remove(self, task_name: str) -> None:
        self._run("/Delete", "/TN", task_name, "/F", allowed={0, 1})

    def run(self, task_name: str) -> None:
        self._run("/Run", "/TN", task_name)

    def status(self, task_name: str) -> dict[str, object]:
        result = self._run("/Query", "/TN", task_name, allowed={0, 1})
        return {"installed": result.returncode == 0, "last_run": None, "last_result": None}


def _tree_files(source_root: Path) -> list[tuple[Path, Path]]:
    selected: list[tuple[Path, Path]] = []
    package = source_root / "review_sync"
    for source in sorted(package.glob("*.py")):
        if source.is_symlink() or not source.is_file():
            raise PolicyError(f"program source is not a regular file: {source}")
        selected.append((source, Path("review_sync") / source.name))
    for name in ("review_sync.py", "review_sync_hook.py", "review_sync_scheduled.py"):
        source = source_root / "scripts" / name
        if source.exists():
            if source.is_symlink() or not source.is_file():
                raise PolicyError(f"program source is not a regular file: {source}")
            selected.append((source, Path(name)))
    required = {Path("review_sync") / "__init__.py", Path("review_sync_hook.py"), Path("review_sync_scheduled.py")}
    if not required.issubset({relative for _source, relative in selected}):
        raise PolicyError("review-sync program source is incomplete")
    return selected


def _copy_program(source_root: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for source, relative in _tree_files(source_root):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def _program_digest(program: Path) -> str:
    digest = hashlib.sha256()
    if not program.is_dir() or program.is_symlink():
        raise PolicyError("managed program tree is missing or unsafe")
    for path in sorted(program.rglob("*")):
        if path.is_dir():
            continue
        if path.is_symlink() or not path.is_file():
            raise PolicyError("managed program tree contains a non-regular file")
        relative = path.relative_to(program).as_posix()
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _scheduled_launcher(
    *,
    script: Path,
    data_root: Path,
    codex_home: Path,
    git_executable: Path,
    gh_executable: Path,
    installation_id: str,
) -> bytes:
    arguments = [
        str(script),
        "--data-root",
        str(data_root),
        "--codex-home",
        str(codex_home),
        "--git",
        str(git_executable),
        "--gh",
        str(gh_executable),
        "--installation-id",
        installation_id,
    ]
    source = (
        "import runpy\n"
        "import sys\n\n"
        f"sys.argv = {arguments!r}\n"
        "runpy.run_path(sys.argv[0], run_name='__main__')\n"
    )
    return source.encode("utf-8")


def _active_agents(codex_home: Path) -> Path:
    override = codex_home / "AGENTS.override.md"
    if override.exists() and override.read_bytes().strip():
        return override
    return codex_home / "AGENTS.md"


def _append_segment(original: bytes) -> bytes:
    if AGENTS_BEGIN.encode() in original or AGENTS_END.encode() in original:
        raise PolicyError("review-sync AGENTS marker already exists without matching install state")
    separator = b"" if not original else (b"" if original.endswith((b"\n", b"\r")) else b"\n")
    if original and not original.endswith((b"\n\n", b"\r\n\r\n")):
        separator += b"\n"
    return original + separator + AGENTS_SEGMENT


def _quote(value: Path | str) -> str:
    return f'"{value}"'


def _hook_groups(program: Path, python: Path, data_root: Path) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    script = program / "review_sync_hook.py"
    for event in ("Stop", "Interrupt", "SessionEnd"):
        command = f"{_quote(python)} -B {_quote(script)} {event} --data-root {_quote(data_root)}"
        result[event] = {
            "hooks": [
                {
                    "type": "command",
                    "command": command,
                    "commandWindows": command,
                    "timeout": 3,
                }
            ]
        }
    return result


def _merge_hooks(original: bytes, managed: dict[str, dict[str, object]]) -> bytes:
    if original.strip():
        try:
            payload = json.loads(original.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PolicyError("existing hooks.json is not valid UTF-8 JSON") from error
    else:
        payload = {"description": "User lifecycle hooks.", "hooks": {}}
    hooks = payload.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise PolicyError("existing hooks.json has an invalid hooks object")
    for event, group in managed.items():
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise PolicyError(f"existing hooks.json event is not a list: {event}")
        if group not in groups:
            groups.append(group)
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _remove_agents_segment(current: bytes, original: bytes) -> bytes:
    begin = AGENTS_BEGIN.encode("utf-8")
    end = AGENTS_END.encode("utf-8")
    if current.count(begin) != 1 or current.count(end) != 1:
        raise PolicyError("managed AGENTS markers are missing or ambiguous")
    start = current.index(begin)
    stop = current.index(end, start) + len(end)
    if current[stop : stop + 2] == b"\r\n":
        stop += 2
    elif current[stop : stop + 1] == b"\n":
        stop += 1
    prefix = current[:start]
    if prefix.startswith(original) and not prefix[len(original) :].strip():
        prefix = original
    return prefix + current[stop:]


def _remove_managed_hooks(
    current: bytes,
    managed: dict[str, dict[str, object]],
    original: bytes,
) -> bytes:
    try:
        payload = json.loads(current.decode("utf-8"))
        hooks = payload["hooks"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise PolicyError("current hooks.json cannot be safely edited") from error
    if not isinstance(hooks, dict):
        raise PolicyError("current hooks.json has an invalid hooks object")
    for event, group in managed.items():
        groups = hooks.get(event)
        if not isinstance(groups, list) or groups.count(group) != 1:
            raise PolicyError(f"managed hook group is missing or ambiguous: {event}")
        groups.remove(group)
        if not groups:
            del hooks[event]
    if original.strip():
        try:
            if payload == json.loads(original.decode("utf-8")):
                return original
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"), validate=True)


def _update_managed_program(
    *,
    source_root: Path,
    codex_home: Path,
    data_root: Path,
    python_executable: Path,
    git_executable: Path,
    gh_executable: Path,
    program: Path,
    state_path: Path,
    scheduler_backend: Scheduler,
) -> str:
    state = read_json(state_path)
    scheduler_state = state["scheduler"]
    expected_paths = {
        "python_executable": python_executable.resolve(strict=False),
        "git_executable": git_executable.resolve(strict=False),
        "gh_executable": gh_executable.resolve(strict=False),
        "codex_home": codex_home,
        "data_root": data_root,
    }
    for name, expected in expected_paths.items():
        if Path(scheduler_state[name] if name in scheduler_state else state[name]).resolve(strict=False) != expected:
            raise PolicyError("managed update runtime differs from the installed integration")
    if Path(state["program"]).resolve(strict=False) != program.resolve(strict=False):
        raise PolicyError("managed update program path differs from the installed integration")

    agents = _active_agents(codex_home)
    original_agents = agents.read_bytes()
    begin, end = AGENTS_BEGIN.encode(), AGENTS_END.encode()
    if original_agents.count(begin) != 1 or original_agents.count(end) != 1:
        raise PolicyError("managed AGENTS segment is missing or ambiguous")
    start = original_agents.index(begin)
    stop = original_agents.index(end) + len(end)
    if stop <= start:
        raise PolicyError("managed AGENTS markers are out of order")
    desired_agents = original_agents[:start] + AGENTS_SEGMENT.rstrip(b"\n") + original_agents[stop:]
    original_receipt = state_path.read_bytes()

    temporary = Path(tempfile.mkdtemp(prefix=".review-sync.update.", dir=codex_home))
    backup = codex_home / f".{PROGRAM_NAME}.backup.{uuid.uuid4().hex}"
    replaced = False
    scheduler_refreshed = False
    try:
        _copy_program(source_root, temporary)
        final_launcher = (program / "scheduled-task.pyw").resolve(strict=False)
        atomic_write_bytes(
            temporary / "scheduled-task.pyw",
            _scheduled_launcher(
                script=Path(scheduler_state["script"]),
                data_root=Path(scheduler_state["data_root"]),
                codex_home=Path(scheduler_state["codex_home"]),
                git_executable=Path(scheduler_state["git_executable"]),
                gh_executable=Path(scheduler_state["gh_executable"]),
                installation_id=scheduler_state["installation_id"],
            ),
        )
        desired_digest = _program_digest(temporary)
        if desired_digest == state["program_digest"] and desired_agents == original_agents:
            return "already_installed"

        os.replace(program, backup)
        try:
            os.replace(temporary, program)
            replaced = True
            definition = SchedulerDefinition(
                task_name=scheduler_state["task_name"],
                python_executable=Path(scheduler_state["python_executable"]),
                script=Path(scheduler_state["script"]),
                data_root=Path(scheduler_state["data_root"]),
                codex_home=Path(scheduler_state["codex_home"]),
                launcher=final_launcher,
                installation_id=scheduler_state["installation_id"],
                git_executable=Path(scheduler_state["git_executable"]),
                gh_executable=Path(scheduler_state["gh_executable"]),
                interval_minutes=int(scheduler_state["interval_minutes"]),
                run_level=str(scheduler_state["run_level"]),
                user_scope=str(scheduler_state["user_scope"]),
            )
            scheduler_backend.install(definition)
            scheduler_refreshed = True
            updated_state = dict(state)
            updated_state["program_digest"] = desired_digest
            updated_state["scheduler"] = definition.as_dict()
            updated_state["installed_agents"] = _b64(desired_agents)
            atomic_write_bytes(agents, desired_agents)
            atomic_write_json(state_path, updated_state)
        except Exception:
            atomic_write_bytes(agents, original_agents)
            atomic_write_bytes(state_path, original_receipt)
            if replaced and program.exists():
                shutil.rmtree(program)
            os.replace(backup, program)
            replaced = False
            if scheduler_refreshed:
                try:
                    scheduler_backend.install(
                        SchedulerDefinition(
                            task_name=scheduler_state["task_name"],
                            python_executable=Path(scheduler_state["python_executable"]),
                            script=Path(scheduler_state["script"]),
                            data_root=Path(scheduler_state["data_root"]),
                            codex_home=Path(scheduler_state["codex_home"]),
                            launcher=Path(scheduler_state["launcher"]),
                            installation_id=scheduler_state["installation_id"],
                            git_executable=Path(scheduler_state["git_executable"]),
                            gh_executable=Path(scheduler_state["gh_executable"]),
                            interval_minutes=int(scheduler_state["interval_minutes"]),
                            run_level=str(scheduler_state["run_level"]),
                            user_scope=str(scheduler_state["user_scope"]),
                        )
                    )
                except Exception:
                    pass
            raise
        try:
            shutil.rmtree(backup)
        except OSError:
            pass
        return "updated"
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def install_user(
    *,
    source_root: Path,
    codex_home: Path,
    data_root: Path,
    python_executable: Path,
    git_executable: Path,
    gh_executable: Path,
    scheduler: Scheduler,
) -> str:
    source_root = source_root.resolve(strict=True)
    codex_home = codex_home.resolve(strict=True)
    data_root = data_root.resolve(strict=False)
    program = codex_home / PROGRAM_NAME
    state_path = codex_home / INSTALL_STATE
    if state_path.exists():
        status = verify_installation(codex_home, data_root, scheduler)
        if status["program"] == "VERIFIED" and status["protocol"] == "VERIFIED" and status["hooks"] == "INSTALLED":
            return _update_managed_program(
                source_root=source_root,
                codex_home=codex_home,
                data_root=data_root,
                python_executable=python_executable,
                git_executable=git_executable,
                gh_executable=gh_executable,
                program=program,
                state_path=state_path,
                scheduler_backend=scheduler,
            )
        raise PolicyError("existing review-sync installation is not intact")
    if program.exists():
        raise PolicyError("unmanaged review-sync program path already exists")
    agents = _active_agents(codex_home)
    hooks = codex_home / "hooks.json"
    agents_existed = agents.exists()
    hooks_existed = hooks.exists()
    original_agents = agents.read_bytes() if agents_existed else b""
    original_hooks = hooks.read_bytes() if hooks_existed else b""
    installed_agents = _append_segment(original_agents)
    temporary = Path(tempfile.mkdtemp(prefix=".review-sync.", dir=codex_home))
    installed_program = False
    scheduler_installed = False
    try:
        _copy_program(source_root, temporary)
        installation_id = uuid.uuid4().hex
        final_script = (program / "review_sync_scheduled.py").resolve(strict=False)
        final_launcher = (program / "scheduled-task.pyw").resolve(strict=False)
        atomic_write_bytes(
            temporary / "scheduled-task.pyw",
            _scheduled_launcher(
                script=final_script,
                data_root=data_root,
                codex_home=codex_home,
                git_executable=git_executable.resolve(strict=False),
                gh_executable=gh_executable.resolve(strict=False),
                installation_id=installation_id,
            ),
        )
        program_digest = _program_digest(temporary)
        managed_hooks = _hook_groups(program, python_executable, data_root)
        installed_hooks = _merge_hooks(original_hooks, managed_hooks)
        atomic_write_bytes(agents, installed_agents)
        atomic_write_bytes(hooks, installed_hooks)
        os.replace(temporary, program)
        installed_program = True
        definition = SchedulerDefinition(
            task_name=TASK_NAME,
            python_executable=python_executable.resolve(strict=False),
            script=final_script,
            data_root=data_root,
            codex_home=codex_home,
            launcher=final_launcher,
            installation_id=installation_id,
            git_executable=git_executable.resolve(strict=False),
            gh_executable=gh_executable.resolve(strict=False),
        )
        scheduler.install(definition)
        scheduler_installed = True
        atomic_write_json(
            state_path,
            {
                "schema_version": 1,
                "program": str(program),
                "program_digest": program_digest,
                "agents_path": str(agents),
                "agents_existed": agents_existed,
                "original_agents": _b64(original_agents),
                "installed_agents": _b64(installed_agents),
                "hooks_path": str(hooks),
                "hooks_existed": hooks_existed,
                "original_hooks": _b64(original_hooks),
                "installed_hooks": _b64(installed_hooks),
                "managed_hooks": managed_hooks,
                "scheduler": definition.as_dict(),
                "data_root": str(data_root),
            },
        )
        return "installed"
    except Exception:
        if scheduler_installed:
            try:
                scheduler.remove(TASK_NAME)
            except Exception:
                pass
        if installed_program and program.exists():
            shutil.rmtree(program)
        if agents_existed:
            atomic_write_bytes(agents, original_agents)
        elif agents.exists():
            agents.unlink()
        if hooks_existed:
            atomic_write_bytes(hooks, original_hooks)
        elif hooks.exists():
            hooks.unlink()
        if state_path.exists():
            state_path.unlink()
        raise
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def verify_installation(
    codex_home: Path,
    data_root: Path,
    scheduler: Scheduler,
) -> dict[str, object]:
    state_path = codex_home / INSTALL_STATE
    if not state_path.exists():
        return {
            "program": "NOT_INSTALLED",
            "protocol": "NOT_INSTALLED",
            "hooks": "NOT_INSTALLED",
            "hook_trust": {"app": "UNKNOWN", "cli": "UNKNOWN"},
            "hook_triggers": {
                host: {event: "NOT_VERIFIED" for event in ("Stop", "Interrupt", "SessionEnd")}
                for host in ("app", "cli")
            },
            "scheduler": "NOT_INSTALLED",
        }
    state = read_json(state_path)
    program = Path(state["program"])
    program_status = "VERIFIED" if _program_digest(program) == state["program_digest"] else "MODIFIED"
    agents = Path(state["agents_path"])
    protocol = "VERIFIED" if agents.exists() and AGENTS_BEGIN.encode() in agents.read_bytes() else "MISSING"
    hooks = Path(state["hooks_path"])
    hooks_status = "MISSING"
    if hooks.exists():
        try:
            payload = json.loads(hooks.read_text(encoding="utf-8"))
            if all(group in payload.get("hooks", {}).get(event, []) for event, group in state["managed_hooks"].items()):
                hooks_status = "INSTALLED"
        except (OSError, json.JSONDecodeError):
            hooks_status = "INVALID"
    scheduler_state = scheduler.status(TASK_NAME)
    scheduler_probe: dict[str, object] | None = None
    probe_path = data_root / "scheduler-probe.json"
    if probe_path.exists():
        try:
            probe = read_json(probe_path)
            expected = state["scheduler"]
            context_matches = (
                probe.get("schema_version") == 1
                and probe.get("status") == "tick_completed"
                and Path(str(probe.get("python"))).resolve(strict=False)
                == Path(expected["python_executable"]).with_name("pythonw.exe").resolve(strict=False)
                and Path(str(probe.get("git"))).resolve(strict=False)
                == Path(expected["git_executable"]).resolve(strict=False)
                and Path(str(probe.get("gh"))).resolve(strict=False)
                == Path(expected["gh_executable"]).resolve(strict=False)
                and Path(str(probe.get("codex_home"))).resolve(strict=False)
                == codex_home.resolve(strict=False)
                and probe.get("installation_id") == expected["installation_id"]
                and isinstance(probe.get("user"), str)
                and bool(probe.get("user"))
            )
            if context_matches:
                scheduler_probe = probe
        except (OSError, KeyError, TypeError, ValueError):
            scheduler_probe = None
    if not scheduler_state.get("installed"):
        scheduler_status = "MISSING"
    elif scheduler_probe is not None:
        scheduler_status = "VERIFIED_RUN"
    elif scheduler_state.get("last_run") is None:
        scheduler_status = "DEFINED_NOT_RUN"
    elif scheduler_state.get("last_result") == 0:
        scheduler_status = "VERIFIED_RUN"
    else:
        scheduler_status = "RUN_FAILED"
    return {
        "program": program_status,
        "protocol": protocol,
        "hooks": hooks_status,
        "hook_trust": {"app": "UNKNOWN", "cli": "UNKNOWN"},
        "hook_triggers": {
            host: {event: "NOT_VERIFIED" for event in ("Stop", "Interrupt", "SessionEnd")}
            for host in ("app", "cli")
        },
        "scheduler": scheduler_status,
        "scheduler_probe": scheduler_probe,
        "data_root": str(data_root),
    }


def uninstall_user(codex_home: Path, data_root: Path, scheduler: Scheduler) -> str:
    state_path = codex_home / INSTALL_STATE
    if not state_path.exists():
        raise PolicyError("review-sync is not installed")
    state = read_json(state_path)
    program = Path(state["program"])
    if _program_digest(program) != state["program_digest"]:
        raise PolicyError("managed program tree changed; refusing uninstall")
    agents = Path(state["agents_path"])
    hooks = Path(state["hooks_path"])
    updated_agents = _remove_agents_segment(agents.read_bytes(), _unb64(state["original_agents"]))
    original_hooks = _unb64(state["original_hooks"])
    updated_hooks = _remove_managed_hooks(hooks.read_bytes(), state["managed_hooks"], original_hooks)
    scheduler.remove(TASK_NAME)
    if not state["agents_existed"] and not updated_agents.strip():
        agents.unlink()
    else:
        atomic_write_bytes(agents, updated_agents)
    if not state["hooks_existed"] and json.loads(updated_hooks.decode("utf-8")) == {
        "description": "User lifecycle hooks.",
        "hooks": {},
    }:
        hooks.unlink()
    else:
        atomic_write_bytes(hooks, updated_hooks)
    shutil.rmtree(program)
    state_path.unlink()
    return "uninstalled"
