"""Shared pytest fixtures: repo root, stage manifest for chained tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from stage_manifest import REPO_ROOT, load_stage_manifest, resolve_repo_path

__all__ = ["REPO_ROOT", "load_stage_manifest", "resolve_repo_path", "repo_root", "stage_manifest"]


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def stage_manifest() -> Optional[Dict[str, Any]]:
    return load_stage_manifest()
