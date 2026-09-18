from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class EntrypointTests(unittest.TestCase):
    def test_source_cli_entrypoint_resolves_package_from_unrelated_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            codex_home = root / "codex"
            codex_home.mkdir()
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(ROOT / "scripts" / "review_sync.py"),
                    "status",
                    "--cwd",
                    str(root),
                    "--data-root",
                    str(root / "data"),
                    "--codex-home",
                    str(codex_home),
                    "--json",
                ],
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            self.assertEqual(json.loads(result.stdout)["GITHUB"], "NEEDS_SETUP")

    def test_source_hook_entrypoint_returns_valid_stop_contract_from_unrelated_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(ROOT / "scripts" / "review_sync_hook.py"),
                    "Stop",
                    "--data-root",
                    str(root / "data"),
                ],
                cwd=root,
                input=json.dumps({"hook_event_name": "Stop", "cwd": str(root)}).encode(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            self.assertEqual(json.loads(result.stdout), {"continue": True, "suppressOutput": True})

    def test_source_scheduler_entrypoint_writes_probe_from_unrelated_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            codex_home = root / "codex"
            codex_home.mkdir()
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(ROOT / "scripts" / "review_sync_scheduled.py"),
                    "--data-root",
                    str(data),
                    "--codex-home",
                    str(codex_home),
                    "--git",
                    "git",
                    "--gh",
                    "gh",
                    "--installation-id",
                    "entrypoint-fixture",
                ],
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            probe = json.loads((data / "scheduler-probe.json").read_text(encoding="utf-8"))
            self.assertEqual(probe["status"], "tick_completed")
            self.assertEqual(probe["installation_id"], "entrypoint-fixture")
            self.assertEqual(Path(probe["codex_home"]), codex_home.resolve())


if __name__ == "__main__":
    unittest.main()
