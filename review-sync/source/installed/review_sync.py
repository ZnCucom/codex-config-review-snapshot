from __future__ import annotations

import sys
from pathlib import Path


_SCRIPT_DIR = Path(__file__).resolve().parent
_PACKAGE_ROOT = _SCRIPT_DIR if (_SCRIPT_DIR / "review_sync" / "__init__.py").is_file() else _SCRIPT_DIR.parent
if _PACKAGE_ROOT != _SCRIPT_DIR:
    sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != _SCRIPT_DIR]
sys.path.insert(0, str(_PACKAGE_ROOT))

from review_sync.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
