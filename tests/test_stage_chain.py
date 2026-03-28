"""Chained stage: use ``latest_manifest.json`` → marketing extras (mock) from staged ``*_final.png``."""

from __future__ import annotations

from pathlib import Path

import pytest

from stage_manifest import load_stage_manifest, resolve_repo_path
from mtwi_ecommerce_pipeline import RequestQueueClient, generate_additional_product_images


@pytest.fixture
def require_stage_manifest():
    m = load_stage_manifest()
    if m is None:
        pytest.skip("Run: python tests/generate_stage_artifacts.py")
    return m


def test_manifest_points_to_existing_final_png(require_stage_manifest: dict) -> None:
    rel = require_stage_manifest["paths_relative_to_repo"]["final_png"]
    p = resolve_repo_path(rel)
    assert p.is_file(), f"missing staged final: {p}"


def test_extras_mock_from_staged_final(tmp_path: Path, require_stage_manifest: dict) -> None:
    rel = require_stage_manifest["paths_relative_to_repo"]["final_png"]
    final_path = resolve_repo_path(rel)
    rq = RequestQueueClient(api_key="mock", mock=True, max_attempts=1)
    paths = generate_additional_product_images(
        rq=rq,
        source_for_generation=final_path,
        product_id="chain_test",
        out_dir=tmp_path,
        model="seedream-5.0-lite",
        count=2,
        structured_attributes=None,
        user_image_instructions="",
        warnings=None,
        fallback_model=None,
    )
    assert len(paths) == 2
    for p in paths:
        assert Path(p).is_file()
        assert Path(p).stat().st_size > 100
