#!/usr/bin/env python3
"""Run a **mock** full MTWI pipeline into tests/stage_artifacts/generated/<run_id>/.

Writes ``latest_manifest.json`` (paths relative to repo root) so pytest can chain stages
(e.g. load ``*_final.png`` for marketing-extras tests without re-running the full pipeline).

Usage (from repo root)::

    python tests/generate_stage_artifacts.py

Requires ``data/demo_one`` image + txt.
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
DEMO = REPO / "data" / "demo_one"
GENERATED = REPO / "tests" / "stage_artifacts" / "generated"
LATEST = GENERATED / "latest_manifest.json"


def _rel(p: Path) -> str:
    return str(p.resolve().relative_to(REPO.resolve()))


def main() -> int:
    if not (DEMO / "image_train" / "demo_item.jpg").is_file():
        print("Missing data/demo_one/image_train/demo_item.jpg — cannot generate fixtures.", file=sys.stderr)
        return 1
    if not (DEMO / "txt_train" / "demo_item.txt").is_file():
        print("Missing data/demo_one/txt_train/demo_item.txt — cannot generate fixtures.", file=sys.stderr)
        return 1

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_root = GENERATED / run_id
    inputs = run_root / "inputs"
    img_train = inputs / "image_train"
    txt_train = inputs / "txt_train"
    mtwi_out = run_root / "mtwi_images"
    yaml_out = run_root / "artifacts.yaml"
    deliv = run_root / "deliverables"

    img_train.mkdir(parents=True, exist_ok=True)
    txt_train.mkdir(parents=True, exist_ok=True)

    shutil.copy2(DEMO / "image_train" / "demo_item.jpg", img_train / "demo_item.jpg")
    shutil.copy2(DEMO / "txt_train" / "demo_item.txt", txt_train / "demo_item.txt")

    sys.path.insert(0, str(SRC))
    from mtwi_ecommerce_pipeline import parse_args, run_pipeline

    argv = [
        "--txt-dir",
        str(txt_train),
        "--image-dir",
        str(img_train),
        "--limit",
        "1",
        "--mock",
        "--image-output-dir",
        str(mtwi_out),
        "--output",
        str(yaml_out),
        "--export-deliverables",
        "--deliverable-dir",
        str(deliv),
        "--skip-listing-review",
        "--no-generate-additional-images",
        "--mask-mode",
        "all",
        "--erase-strategy",
        "model",
    ]
    args = parse_args(argv)
    out_path = run_pipeline(args)

    pid = "demo_item"
    product_deliv = deliv / pid
    final_png = mtwi_out / f"{pid}_final.png"
    erased_png = mtwi_out / f"{pid}_erased.png"

    manifest = {
        "run_id": run_id,
        "mock": True,
        "product_id": pid,
        "paths_relative_to_repo": {
            "run_root": _rel(run_root),
            "inputs_image_dir": _rel(img_train),
            "inputs_txt_dir": _rel(txt_train),
            "source_image": _rel(img_train / f"{pid}.jpg"),
            "annotation_txt": _rel(txt_train / f"{pid}.txt"),
            "mtwi_work_dir": _rel(mtwi_out),
            "erased_png": _rel(erased_png),
            "final_png": _rel(final_png),
            "artifacts_output": _rel(Path(out_path)),
            "deliverable_product_dir": _rel(product_deliv),
        },
    }

    GENERATED.mkdir(parents=True, exist_ok=True)
    LATEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (run_root / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Wrote {_rel(run_root)} and {_rel(LATEST)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
