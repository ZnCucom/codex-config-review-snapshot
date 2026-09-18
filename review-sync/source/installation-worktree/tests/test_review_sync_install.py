from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from review_sync.install import (
    AGENTS_BEGIN,
    AGENTS_SEGMENT,
    INSTALL_STATE,
    SchedulerDefinition,
    install_user,
    uninstall_user,
    verify_installation,
)
from review_sync.model import PolicyError


class FakeScheduler:
    def __init__(self, fail_install: bool = False) -> None:
        self.fail_install = fail_install
        self.definition: SchedulerDefinition | None = None
        self.removed = False
        self.install_calls = 0

    def install(self, definition: SchedulerDefinition) -> None:
        self.install_calls += 1
        if self.fail_install:
            raise RuntimeError("injected scheduler failure")
        self.definition = definition

    def remove(self, task_name: str) -> None:
        self.removed = True
        self.definition = None

    def status(self, task_name: str) -> dict[str, object]:
        return {"installed": self.definition is not None, "last_run": None, "last_result": None}


class InstallTests(unittest.TestCase):
    def _paths(self, root: Path):
        source = Path(__file__).resolve().parents[1]
        codex_home = root / "codex home"
        data = root / "data root"
        codex_home.mkdir()
        agents = codex_home / "AGENTS.md"
        agents.write_bytes(b"# Existing\r\n\r\nKeep this.\r\n")
        hooks = codex_home / "hooks.json"
        hooks.write_bytes(
            b'{\r\n  "description": "existing",\r\n  "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "existing"}]}]}\r\n}\r\n'
        )
        return source, codex_home, data, agents, hooks

    def test_install_is_idempotent_and_uninstall_restores_original_files_and_keeps_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, codex_home, data, agents, hooks = self._paths(Path(temporary))
            original_agents = agents.read_bytes()
            original_hooks = hooks.read_bytes()
            scheduler = FakeScheduler()
            kwargs = dict(
                source_root=source,
                codex_home=codex_home,
                data_root=data,
                python_executable=Path("C:/runtime/python.exe"),
                git_executable=Path("C:/runtime/git.exe"),
                gh_executable=Path("C:/runtime/gh.exe"),
                scheduler=scheduler,
            )

            first = install_user(**kwargs)
            second = install_user(**kwargs)

            self.assertEqual(first, "installed")
            self.assertEqual(second, "already_installed")
            self.assertIn(AGENTS_BEGIN.encode(), agents.read_bytes())
            self.assertNotIn(b"status once at task start", agents.read_bytes())
            self.assertNotIn(b"request a bounded", agents.read_bytes())
            self.assertIn(b"scheduled background sync", agents.read_bytes())
            hooks_payload = json.loads(hooks.read_text(encoding="utf-8"))
            for event in ("Stop", "Interrupt", "SessionEnd"):
                managed = [group for group in hooks_payload["hooks"][event] if "review_sync_hook.py" in str(group)]
                self.assertEqual(len(managed), 1)
                self.assertLessEqual(managed[0]["hooks"][0]["timeout"], 3)
                self.assertIn(" -B ", managed[0]["hooks"][0]["command"])
            self.assertIsNotNone(scheduler.definition)
            self.assertEqual(scheduler.definition.interval_minutes, 10)
            self.assertEqual(scheduler.definition.run_level, "LIMITED")
            self.assertTrue(scheduler.definition.python_executable.is_absolute())
            self.assertTrue(scheduler.definition.script.is_absolute())
            self.assertTrue(scheduler.definition.launcher.is_absolute())
            self.assertEqual(scheduler.definition.launcher.suffix, ".pyw")
            command_line = scheduler.definition.command_line()
            self.assertLess(len(command_line), 261)
            self.assertIn("pythonw.exe", command_line.lower())
            self.assertIn(str(scheduler.definition.launcher), command_line)
            launcher = scheduler.definition.launcher.read_text(encoding="utf-8")
            self.assertIn("import runpy", launcher)
            self.assertIn("sys.argv", launcher)
            self.assertIn("runpy.run_path", launcher)
            self.assertIn(repr(str(scheduler.definition.git_executable)), launcher)
            self.assertIn(repr(str(scheduler.definition.gh_executable)), launcher)
            self.assertIn(repr(str(scheduler.definition.codex_home)), launcher)
            self.assertIn(repr(scheduler.definition.installation_id), launcher)
            self.assertEqual(scheduler.definition.codex_home, codex_home.resolve())
            self.assertIn("--codex-home", launcher)
            status = verify_installation(codex_home, data, scheduler)
            self.assertEqual(status["program"], "VERIFIED")
            self.assertEqual(status["hooks"], "INSTALLED")
            self.assertEqual(status["hook_trust"], {"app": "UNKNOWN", "cli": "UNKNOWN"})
            self.assertEqual(
                status["hook_triggers"],
                {
                    "app": {
                        "Stop": "NOT_VERIFIED",
                        "Interrupt": "NOT_VERIFIED",
                        "SessionEnd": "NOT_VERIFIED",
                    },
                    "cli": {
                        "Stop": "NOT_VERIFIED",
                        "Interrupt": "NOT_VERIFIED",
                        "SessionEnd": "NOT_VERIFIED",
                    },
                },
            )
            self.assertEqual(status["scheduler"], "DEFINED_NOT_RUN")
            marker = data / "preserve.txt"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("keep", encoding="utf-8")

            self.assertEqual(uninstall_user(codex_home, data, scheduler), "uninstalled")

            self.assertEqual(agents.read_bytes(), original_agents)
            self.assertEqual(hooks.read_bytes(), original_hooks)
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
            self.assertTrue(scheduler.removed)

    def test_repair_replaces_only_segment_and_rolls_back_on_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, home, data, agents, hooks = self._paths(Path(temporary))
            scheduler = FakeScheduler()
            kwargs = dict(source_root=source, codex_home=home, data_root=data,
                          python_executable=Path("C:/runtime/python.exe"),
                          git_executable=Path("C:/runtime/git.exe"),
                          gh_executable=Path("C:/runtime/gh.exe"), scheduler=scheduler)
            install_user(**kwargs)
            old = AGENTS_SEGMENT.replace(b"scheduled background sync", b"status once at task start")
            agents.write_bytes(agents.read_bytes().replace(AGENTS_SEGMENT, old) + b"\nUser addition\n")
            original = agents.read_bytes()
            receipt = (home / INSTALL_STATE).read_bytes()
            hooks_before = hooks.read_bytes()
            scheduler.fail_install = True
            with self.assertRaises(RuntimeError):
                install_user(**kwargs)
            self.assertEqual(agents.read_bytes(), original)
            self.assertEqual((home / INSTALL_STATE).read_bytes(), receipt)
            scheduler.fail_install = False
            self.assertEqual(install_user(**kwargs), "updated")
            self.assertNotIn(b"status once at task start", agents.read_bytes())
            self.assertTrue(agents.read_bytes().endswith(b"User addition\n"))
            self.assertEqual(hooks.read_bytes(), hooks_before)
            self.assertEqual(install_user(**kwargs), "already_installed")

    def test_install_updates_managed_program_and_refreshes_scheduler_action(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, codex_home, data, agents, hooks = self._paths(root)
            source_copy = root / "source"
            shutil.copytree(source / "review_sync", source_copy / "review_sync")
            (source_copy / "scripts").mkdir()
            for name in ("review_sync.py", "review_sync_hook.py", "review_sync_scheduled.py"):
                shutil.copyfile(source / "scripts" / name, source_copy / "scripts" / name)
            scheduler = FakeScheduler()
            kwargs = dict(
                source_root=source_copy,
                codex_home=codex_home,
                data_root=data,
                python_executable=Path("C:/runtime/python.exe"),
                git_executable=Path("C:/runtime/git.exe"),
                gh_executable=Path("C:/runtime/gh.exe"),
                scheduler=scheduler,
            )
            self.assertEqual(install_user(**kwargs), "installed")
            state_path = codex_home / ".review-sync-install.json"
            initial_state = json.loads(state_path.read_text(encoding="utf-8"))
            initial_agents = agents.read_bytes()
            initial_hooks = hooks.read_bytes()
            marker = data / "preserve.txt"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("keep", encoding="utf-8")
            changed_source = source_copy / "review_sync" / "security.py"
            changed_source.write_bytes(changed_source.read_bytes() + b"\n# managed update fixture\n")

            result = install_user(**kwargs)

            updated_state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(result, "updated")
            self.assertEqual(scheduler.install_calls, 2)
            self.assertEqual(scheduler.definition.launcher.suffix, ".pyw")
            self.assertEqual(
                updated_state["scheduler"]["installation_id"],
                initial_state["scheduler"]["installation_id"],
            )
            self.assertNotEqual(updated_state["program_digest"], initial_state["program_digest"])
            self.assertEqual(
                (codex_home / "review-sync" / "review_sync" / "security.py").read_bytes(),
                changed_source.read_bytes(),
            )
            self.assertEqual(agents.read_bytes(), initial_agents)
            self.assertEqual(hooks.read_bytes(), initial_hooks)
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
            self.assertEqual(verify_installation(codex_home, data, scheduler)["program"], "VERIFIED")

    def test_scheduler_failure_rolls_back_every_managed_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, codex_home, data, agents, hooks = self._paths(Path(temporary))
            original_agents = agents.read_bytes()
            original_hooks = hooks.read_bytes()

            with self.assertRaisesRegex(RuntimeError, "injected scheduler failure"):
                install_user(
                    source_root=source,
                    codex_home=codex_home,
                    data_root=data,
                    python_executable=Path("C:/runtime/python.exe"),
                    git_executable=Path("C:/runtime/git.exe"),
                    gh_executable=Path("C:/runtime/gh.exe"),
                    scheduler=FakeScheduler(fail_install=True),
                )

            self.assertEqual(agents.read_bytes(), original_agents)
            self.assertEqual(hooks.read_bytes(), original_hooks)
            self.assertFalse((codex_home / "review-sync").exists())
            self.assertFalse((codex_home / ".review-sync-install.json").exists())

    def test_modified_managed_program_blocks_uninstall(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, codex_home, data, _agents, _hooks = self._paths(Path(temporary))
            scheduler = FakeScheduler()
            install_user(
                source_root=source,
                codex_home=codex_home,
                data_root=data,
                python_executable=Path("C:/runtime/python.exe"),
                git_executable=Path("C:/runtime/git.exe"),
                gh_executable=Path("C:/runtime/gh.exe"),
                scheduler=scheduler,
            )
            (codex_home / "review-sync" / "review_sync" / "model.py").write_text("modified", encoding="utf-8")

            with self.assertRaisesRegex(PolicyError, "managed program tree changed"):
                uninstall_user(codex_home, data, scheduler)

    def test_uninstall_removes_only_owned_protocol_and_hooks_after_unrelated_edits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, codex_home, data, agents, hooks = self._paths(Path(temporary))
            scheduler = FakeScheduler()
            install_user(
                source_root=source,
                codex_home=codex_home,
                data_root=data,
                python_executable=Path("C:/runtime/python.exe"),
                git_executable=Path("C:/runtime/git.exe"),
                gh_executable=Path("C:/runtime/gh.exe"),
                scheduler=scheduler,
            )
            agents.write_bytes(agents.read_bytes() + b"\n# Later user rule\nKeep later.\n")
            hooks_payload = json.loads(hooks.read_text(encoding="utf-8"))
            hooks_payload["hooks"]["Stop"].append(
                {"hooks": [{"type": "command", "command": "later-user-hook"}]}
            )
            hooks.write_text(json.dumps(hooks_payload, indent=2) + "\n", encoding="utf-8")

            self.assertEqual(uninstall_user(codex_home, data, scheduler), "uninstalled")

            self.assertNotIn(AGENTS_BEGIN.encode(), agents.read_bytes())
            self.assertIn(b"# Existing", agents.read_bytes())
            self.assertIn(b"# Later user rule", agents.read_bytes())
            remaining = json.loads(hooks.read_text(encoding="utf-8"))
            self.assertTrue(any("existing" in str(group) for group in remaining["hooks"]["Stop"]))
            self.assertTrue(any("later-user-hook" in str(group) for group in remaining["hooks"]["Stop"]))
            self.assertFalse(any("review_sync_hook.py" in str(group) for group in remaining["hooks"]["Stop"]))

    def test_scheduler_definition_contains_no_password_and_uses_current_user_scope(self) -> None:
        definition = SchedulerDefinition(
            task_name="Codex Review Sync v1",
            python_executable=Path("C:/runtime/python.exe"),
            script=Path("C:/codex/review-sync/review_sync_scheduled.py"),
            data_root=Path("C:/data"),
            codex_home=Path("C:/codex"),
            launcher=Path("C:/codex/review-sync/scheduled-task.cmd"),
            installation_id="installation-fixture",
            git_executable=Path("C:/runtime/git.exe"),
            gh_executable=Path("C:/runtime/gh.exe"),
        )
        payload = definition.as_dict()
        self.assertEqual(payload["user_scope"], "CURRENT_USER")
        self.assertEqual(payload["run_level"], "LIMITED")
        self.assertNotIn("password", json.dumps(payload).lower())
        self.assertEqual(payload["interval_minutes"], 10)
        self.assertEqual(payload["codex_home"], "C:\\codex")

    def test_scheduler_uses_windowless_python_instead_of_a_batch_file(self) -> None:
        definition = SchedulerDefinition(
            task_name="Codex Review Sync v1",
            python_executable=Path("C:/runtime/python.exe"),
            script=Path("C:/codex/review-sync/review_sync_scheduled.py"),
            data_root=Path("C:/data"),
            codex_home=Path("C:/codex"),
            launcher=Path("C:/codex/review-sync/scheduled-task.pyw"),
            installation_id="installation-fixture",
            git_executable=Path("C:/runtime/git.exe"),
            gh_executable=Path("C:/runtime/gh.exe"),
        )

        command = definition.command_line().lower()

        self.assertIn("pythonw.exe", command)
        self.assertIn("scheduled-task.pyw", command)
        self.assertIn(" -b ", command)
        self.assertNotIn(".cmd", command)

    def test_verified_scheduler_probe_reports_explicit_noninteractive_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, codex_home, data, _agents, _hooks = self._paths(Path(temporary))
            scheduler = FakeScheduler()
            install_user(
                source_root=source,
                codex_home=codex_home,
                data_root=data,
                python_executable=Path("C:/runtime/python.exe"),
                git_executable=Path("C:/runtime/git.exe"),
                gh_executable=Path("C:/runtime/gh.exe"),
                scheduler=scheduler,
            )
            old_probe = {
                "schema_version": 1,
                "status": "tick_completed",
                "python": "C:/runtime/pythonw.exe",
                "git": "C:/runtime/git.exe",
                "gh": "C:/runtime/gh.exe",
                "codex_home": str(codex_home.resolve()),
                "user": "fixture-user",
            }
            data.mkdir(parents=True, exist_ok=True)
            (data / "scheduler-probe.json").write_text(
                json.dumps(old_probe),
                encoding="utf-8",
            )

            self.assertEqual(verify_installation(codex_home, data, scheduler)["scheduler"], "DEFINED_NOT_RUN")
            install_state = json.loads((codex_home / ".review-sync-install.json").read_text(encoding="utf-8"))
            old_probe["installation_id"] = install_state["scheduler"]["installation_id"]
            (data / "scheduler-probe.json").write_text(json.dumps(old_probe), encoding="utf-8")

            status = verify_installation(codex_home, data, scheduler)

            self.assertEqual(status["scheduler"], "VERIFIED_RUN")
            self.assertEqual(status["scheduler_probe"]["codex_home"], str(codex_home.resolve()))


if __name__ == "__main__":
    unittest.main()
