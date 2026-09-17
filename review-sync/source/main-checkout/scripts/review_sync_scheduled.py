from __future__ import annotations

import argparse
import getpass
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PACKAGE_ROOT = _SCRIPT_DIR if (_SCRIPT_DIR / "review_sync" / "__init__.py").is_file() else _SCRIPT_DIR.parent
if _PACKAGE_ROOT != _SCRIPT_DIR:
    sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != _SCRIPT_DIR]
sys.path.insert(0, str(_PACKAGE_ROOT))

from review_sync.fs import atomic_write_json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--codex-home", type=Path, required=True)
    parser.add_argument("--git", type=Path, required=True)
    parser.add_argument("--gh", type=Path, required=True)
    parser.add_argument("--installation-id", required=True)
    args = parser.parse_args()
    status = "cli_not_installed"
    exit_code = 0
    try:
        from review_sync.cli import tick_registered

        tick_registered(args.data_root, args.git, args.gh)
        status = "tick_completed"
    except ModuleNotFoundError as error:
        if error.name != "review_sync.cli":
            raise
    except Exception:
        status = "tick_failed"
        exit_code = 1
    atomic_write_json(
        args.data_root / "scheduler-probe.json",
        {
            "schema_version": 1,
            "ran_at": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "python": str(Path(sys.executable).resolve()),
            "git": str(args.git),
            "gh": str(args.gh),
            "user": getpass.getuser(),
            "platform": platform.platform(),
            "codex_home": str(args.codex_home.resolve(strict=False)),
            "installation_id": args.installation_id,
        },
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
