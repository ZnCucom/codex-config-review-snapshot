from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from review_sync.git import GitRunner
from review_sync.model import CandidateFile, PolicyError
from review_sync.security import (
    enforce_workflow_gate,
    scan_candidate,
    scan_reachable_history,
    workflow_digest,
)


ROOT = Path(__file__).resolve().parents[1]


def _file(data: bytes, mode: str = "100644") -> CandidateFile:
    return CandidateFile(mode=mode, data=data)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    ).stdout.strip()


class SecurityGateTests(unittest.TestCase):
    def test_reviewed_fixture_bytes_are_allowed_only_at_exact_digest(self) -> None:
        for relative_path in (
            "tests/fixtures/current_rollout_v0154_shapes.jsonl",
            "tests/fixtures/rollout.jsonl",
            "tests/evidence/v1.1-install-idempotent-response.md",
        ):
            with self.subTest(path=relative_path):
                data = (ROOT / relative_path).read_bytes()

                reviewed = scan_candidate({relative_path: _file(data)})
                changed = scan_candidate({relative_path: _file(data + b"\n")})

                self.assertFalse(reviewed.blocked, reviewed.render())
                self.assertTrue(changed.blocked)

    def test_scanner_source_does_not_flag_its_own_pattern_literals(self) -> None:
        source = (ROOT / "review_sync" / "security.py").read_bytes()

        report = scan_candidate({"review_sync/security.py": _file(source)})

        self.assertFalse(report.blocked, report.render())

    def test_lowercase_repository_path_is_not_a_high_entropy_secret(self) -> None:
        report = scan_candidate(
            {
                "metadata.txt": _file(
                    b"docs/superpowers/plans/"
                    b"2026-09-16-github-review-checkpoint-sync-v1.md\n"
                )
            }
        )

        self.assertFalse(report.blocked, report.render())

    def test_private_key_and_github_token_block_without_secret_echo(self) -> None:
        token = b"ghp_" + b"ABCDEFGHIJKLMNOPQR" + b"STUVWXYZ0123456789"
        private = (
            b"-----BEGIN "
            + b"PRIVATE KEY-----\nsecret bytes\n-----END "
            + b"PRIVATE KEY-----\n"
        )

        report = scan_candidate(
            {
                "tracked.pem": _file(private),
                ".review-sync/HANDOFF.md": _file(b"blocker: " + token + b"\n"),
            }
        )

        self.assertTrue(report.blocked)
        self.assertEqual({item.rule_id for item in report.findings}, {"PRIVATE_KEY", "SENSITIVE_PATH", "GITHUB_TOKEN"})
        rendered = report.render()
        self.assertNotIn("BEGIN PRIVATE KEY", rendered)
        self.assertNotIn(token.decode(), rendered)

    def test_sensitive_paths_lfs_and_unapproved_data_are_blocked(self) -> None:
        report = scan_candidate(
            {
                ".env": _file(b"DEBUG=false\n"),
                "cache/data.sqlite": _file(b"SQLite format 3\0"),
                "large.bin": _file(b"version https://git-lfs.github.com/spec/v1\n"),
            }
        )

        self.assertEqual(
            {item.rule_id for item in report.findings},
            {"SENSITIVE_PATH", "UNAPPROVED_DATA", "GIT_LFS"},
        )

    def test_common_hashes_and_source_are_not_entropy_false_positives(self) -> None:
        sha1 = b"0123456789abcdef0123456789abcdef01234567"
        sha256 = b"0123456789abcdef" * 4
        report = scan_candidate(
            {
                "manifest.json": _file(b'{"sha":"' + sha1 + b'","digest":"' + sha256 + b'"}\n'),
                "src/example.py": _file(b"def add(left, right):\n    return left + right\n"),
                ".env.example": _file(b"API_TOKEN=replace-me\n"),
            }
        )

        self.assertFalse(report.blocked, report.render())

    def test_candidate_workflow_requires_exact_reviewed_digest(self) -> None:
        files = {
            "src/main.py": _file(b"print('ok')\n"),
            ".github/workflows/deploy.yml": _file(b"on: [push]\njobs: {}\n"),
        }
        digest = workflow_digest(files)

        with self.assertRaisesRegex(PolicyError, "workflow snapshot is not explicitly reviewed"):
            enforce_workflow_gate(files, None)
        with self.assertRaisesRegex(PolicyError, "workflow snapshot is not explicitly reviewed"):
            enforce_workflow_gate(files, "0" * 64)
        enforce_workflow_gate(files, digest)

        changed = dict(files)
        changed[".github/workflows/deploy.yml"] = _file(b"on: [push]\njobs: {deploy: {}}\n")
        with self.assertRaisesRegex(PolicyError, "workflow snapshot is not explicitly reviewed"):
            enforce_workflow_gate(changed, digest)

    def test_first_history_scan_finds_secret_removed_from_tip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "history"
            repo.mkdir()
            _git(repo, "init")
            _git(repo, "config", "user.email", "<REDACTED_EMAIL>")
            _git(repo, "config", "user.name", "Tests")
            secret = "ghp_" + "ABCDEFGHIJKLMNOPQR" + "STUVWXYZ0123456789"
            (repo / "secret.txt").write_text(secret, encoding="utf-8")
            _git(repo, "add", "secret.txt")
            _git(repo, "commit", "-m", "secret parent")
            (repo / "secret.txt").write_text("removed\n", encoding="utf-8")
            _git(repo, "commit", "-am", "remove secret")
            tip = _git(repo, "rev-parse", "HEAD")

            report = scan_reachable_history(repo / ".git", tip, None, GitRunner("git"))

            self.assertTrue(report.blocked)
            self.assertIn("GITHUB_TOKEN", {item.rule_id for item in report.findings})
            self.assertNotIn(secret, report.render())


if __name__ == "__main__":
    unittest.main()
