from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from collections.abc import Mapping
from pathlib import PurePosixPath

from .git import GitRunner
from .model import CandidateFile, PolicyError, ScanFinding, ScanReport


PRIVATE_KEY_MARKERS = (
    b"-----BEGIN " b"PRIVATE KEY-----",
    b"-----BEGIN RSA " b"PRIVATE KEY-----",
    b"-----BEGIN EC " b"PRIVATE KEY-----",
    b"-----BEGIN OPENSSH " b"PRIVATE KEY-----",
)

KNOWN_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    ("GITHUB_TOKEN", re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("GITHUB_TOKEN", re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("AWS_ACCESS_KEY", re.compile(rb"\bAKIA[0-9A-Z]{16}\b")),
    (
        "ASSIGNED_SECRET",
        re.compile(
            rb"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|secret)\b\s*[:=]\s*[\"']?[A-Za-z0-9+/=_-]{16,}"
        ),
    ),
)

ENTROPY_TOKEN = re.compile(rb"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/=_-]{32,128}(?![A-Za-z0-9+/=_-])")
HEX_TOKEN = re.compile(rb"[0-9a-fA-F]+")
LOWERCASE_REPOSITORY_PATH = re.compile(rb"(?:[a-z0-9_-]+/){2,}[a-z0-9_-]+")
REVIEWED_FIXTURE_DIGESTS = {
    "tests/fixtures/rollout.jsonl": "8ef317ccf3dd8b87fc598b84fd7f429196ed2e94f994fc9d49d2287b8543044e",
    "tests/evidence/v1.1-install-idempotent-response.md": "cc6663f7027bd7eb90ff64f50f262c3214786029ca06998b6c30234b0a315836",
}
DATA_SUFFIXES = {".bin", ".csv", ".db", ".parquet", ".sqlite", ".sqlite3", ".tsv"}
SENSITIVE_NAMES = {
    ".env",
    "credentials.json",
    "id_dsa",
    "id_ed25519",
    "id_ecdsa",
    "id_rsa",
}
SENSITIVE_SUFFIXES = {".key", ".p12", ".pfx", ".pem"}
EXCLUDED_PARTS = {
    ".cache",
    ".codex",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}


def _finding(rule_id: str, path: str, message: str) -> ScanFinding:
    return ScanFinding(rule_id=rule_id, path=path, message=message)


def _normalized_path(path: str) -> PurePosixPath:
    if "\x00" in path or "\\" in path:
        raise PolicyError(f"candidate path is not normalized: {path!r}")
    pure = PurePosixPath(path)
    if pure.is_absolute() or not pure.parts or any(part in ("", ".", "..") for part in pure.parts):
        raise PolicyError(f"candidate path is not repository-relative: {path!r}")
    return pure


def _path_findings(path: str) -> list[ScanFinding]:
    pure = _normalized_path(path)
    lowered = tuple(part.lower() for part in pure.parts)
    name = lowered[-1]
    findings: list[ScanFinding] = []
    if any(part in EXCLUDED_PARTS for part in lowered):
        findings.append(_finding("EXCLUDED_PATH", path, "build, cache, or user-runtime path is excluded"))
    env_sensitive = name == ".env" or (
        name.startswith(".env.") and name not in {".env.example", ".env.sample", ".env.template"}
    )
    if env_sensitive or name in SENSITIVE_NAMES or PurePosixPath(name).suffix in SENSITIVE_SUFFIXES:
        findings.append(_finding("SENSITIVE_PATH", path, "credential-bearing path requires exclusion"))
    suffix = PurePosixPath(name).suffix
    if suffix in DATA_SUFFIXES:
        findings.append(_finding("UNAPPROVED_DATA", path, "data or binary file type is not enabled in v1"))
    if suffix in {".jsonl", ".rollout"} or "transcript" in name:
        findings.append(_finding("SENSITIVE_LOG", path, "raw conversation or observation log is excluded"))
    return findings


def _entropy(value: bytes) -> float:
    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def _is_exact_reviewed_fixture(path: str, data: bytes) -> bool:
    expected = REVIEWED_FIXTURE_DIGESTS.get(path)
    return expected is not None and hashlib.sha256(data).hexdigest() == expected


def _content_findings(path: str, data: bytes, max_file_bytes: int) -> list[ScanFinding]:
    findings: list[ScanFinding] = []
    if len(data) > max_file_bytes:
        findings.append(_finding("LARGE_FILE", path, "file exceeds the review-sync safety limit"))
    if data.startswith(b"version https://git-lfs.github.com/spec/v1\n"):
        findings.append(_finding("GIT_LFS", path, "Git LFS pointers are unsupported in v1"))
    if any(marker in data for marker in PRIVATE_KEY_MARKERS):
        findings.append(_finding("PRIVATE_KEY", path, "private-key material detected"))
    known_match = False
    for rule_id, pattern in KNOWN_SECRET_PATTERNS:
        if pattern.search(data):
            findings.append(_finding(rule_id, path, "credential-like content detected"))
            known_match = True
    if not known_match:
        for match in ENTROPY_TOKEN.finditer(data):
            token = match.group(0)
            if len(token) in {40, 64} and HEX_TOKEN.fullmatch(token):
                continue
            if token.startswith((b"sha256", b"example", b"replace")):
                continue
            if LOWERCASE_REPOSITORY_PATH.fullmatch(token):
                continue
            classes = sum(
                bool(pattern.search(token))
                for pattern in (re.compile(rb"[a-z]"), re.compile(rb"[A-Z]"), re.compile(rb"[0-9]"))
            )
            if classes >= 2 and _entropy(token) >= 4.5:
                findings.append(_finding("HIGH_ENTROPY", path, "high-entropy credential-like content detected"))
                break
    return findings


def scan_candidate(
    files: Mapping[str, CandidateFile],
    *,
    max_file_bytes: int = 5 * 1024 * 1024,
) -> ScanReport:
    findings: list[ScanFinding] = []
    for path, candidate in sorted(files.items()):
        if candidate.mode not in {"100644", "100755"}:
            findings.append(_finding("UNSUPPORTED_MODE", path, f"Git mode {candidate.mode} is unsupported"))
            continue
        exact_reviewed_fixture = _is_exact_reviewed_fixture(path, candidate.data)
        path_findings = _path_findings(path)
        if exact_reviewed_fixture:
            path_findings = [finding for finding in path_findings if finding.rule_id != "SENSITIVE_LOG"]
        findings.extend(path_findings)
        if not exact_reviewed_fixture:
            findings.extend(_content_findings(path, candidate.data, max_file_bytes))
    return ScanReport(tuple(sorted(set(findings))))


def workflow_digest(files: Mapping[str, CandidateFile]) -> str | None:
    workflows = [
        (path, candidate)
        for path, candidate in sorted(files.items())
        if path.startswith(".github/workflows/")
    ]
    if not workflows:
        return None
    digest = hashlib.sha256()
    for path, candidate in workflows:
        digest.update(path.encode("utf-8") + b"\0")
        digest.update(candidate.mode.encode("ascii") + b"\0")
        digest.update(candidate.data)
        digest.update(b"\0")
    return digest.hexdigest()


def enforce_workflow_gate(files: Mapping[str, CandidateFile], reviewed_digest: str | None) -> None:
    actual = workflow_digest(files)
    if actual is not None and actual != reviewed_digest:
        raise PolicyError("candidate workflow snapshot is not explicitly reviewed")


def scan_reachable_history(
    git_dir,
    new_sha: str,
    verified_sha: str | None,
    runner: GitRunner,
) -> ScanReport:
    arguments = ["rev-list", "--objects", new_sha]
    if verified_sha is not None:
        arguments.append(f"^{verified_sha}")
    output = runner.snapshot(git_dir, *arguments)
    findings: list[ScanFinding] = []
    seen: set[str] = set()
    for raw_line in output.splitlines():
        raw_object, separator, raw_path = raw_line.partition(b" ")
        object_id = raw_object.decode("ascii")
        if object_id in seen:
            continue
        seen.add(object_id)
        object_type = runner.snapshot(git_dir, "cat-file", "-t", object_id).strip()
        if object_type != b"blob":
            continue
        path = raw_path.decode("utf-8", errors="surrogateescape") if separator else f"object/{object_id}"
        data = runner.snapshot(git_dir, "cat-file", "blob", object_id)
        report = scan_candidate({path: CandidateFile(mode="100644", data=data)})
        findings.extend(report.findings)
    return ScanReport(tuple(sorted(set(findings))))
