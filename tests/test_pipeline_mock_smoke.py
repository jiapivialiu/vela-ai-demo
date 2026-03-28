"""Mock end-to-end pipeline in an isolated temp dir (no stage_artifacts pre-run)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from mtwi_ecommerce_pipeline import parse_args, run_pipeline


@pytest.fixture
def demo_one_available(repo_root: Path) -> Path:
    demo = repo_root / "data" / "demo_one"
    jpg = demo / "image_train" / "demo_item.jpg"
    txt = demo / "txt_train" / "demo_item.txt"
    if not jpg.is_file() or not txt.is_file():
        pytest.skip("data/demo_one sample not present")
    return demo


def test_run_pipeline_mock_demo_one(tmp_path: Path, demo_one_available: Path) -> None:
    img_train = tmp_path / "image_train"
    txt_train = tmp_path / "txt_train"
    img_train.mkdir()
    txt_train.mkdir()
    shutil.copy2(demo_one_available / "image_train" / "demo_item.jpg", img_train / "demo_item.jpg")
    shutil.copy2(demo_one_available / "txt_train" / "demo_item.txt", txt_train / "demo_item.txt")

    work_img = tmp_path / "mtwi_images"
    yaml_out = tmp_path / "artifacts.yaml"
    deliv = tmp_path / "deliverables"

    argv = [
        "--txt-dir",
        str(txt_train),
        "--image-dir",
        str(img_train),
        "--limit",
        "1",
        "--mock",
        "--image-output-dir",
        str(work_img),
        "--output",
        str(yaml_out),
        "--export-deliverables",
        "--deliverable-dir",
        str(deliv),
        "--skip-listing-review",
        "--no-generate-additional-images",
        "--mask-mode",
        "all",
    ]
    args = parse_args(argv)
    out_path = run_pipeline(args)

    assert Path(out_path).is_file()
    final_png = work_img / "demo_item_final.png"
    assert final_png.is_file() and final_png.stat().st_size > 100
    prod = deliv / "demo_item"
    assert (prod / "product_image.png").is_file()
