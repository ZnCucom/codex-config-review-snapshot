from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PACKAGE_ROOT = _SCRIPT_DIR if (_SCRIPT_DIR / "review_sync" / "__init__.py").is_file() else _SCRIPT_DIR.parent
if _PACKAGE_ROOT != _SCRIPT_DIR:
    sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != _SCRIPT_DIR]
sys.path.insert(0, str(_PACKAGE_ROOT))

from review_sync.git import GitRunner
from review_sync.hook import handle_hook


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("event", choices=("Stop", "Interrupt", "SessionEnd"))
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--git", default="git")
    args = parser.parse_args()
    output = handle_hook(args.event, sys.stdin.buffer.read(), args.data_root, GitRunner(args.git))
    if output:
        sys.stdout.buffer.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
