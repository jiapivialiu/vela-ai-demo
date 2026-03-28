"""Load ``latest_manifest.json`` for chained stage tests (no pytest dependency)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
STAGE_GENERATED = REPO_ROOT / "tests" / "stage_artifacts" / "generated"
LATEST_MANIFEST = STAGE_GENERATED / "latest_manifest.json"


def load_stage_manifest() -> Optional[Dict[str, Any]]:
    if not LATEST_MANIFEST.is_file():
        return None
    return json.loads(LATEST_MANIFEST.read_text(encoding="utf-8"))


def resolve_repo_path(rel: str) -> Path:
    return (REPO_ROOT / rel).resolve()
