#!/usr/bin/env python3
"""Smoke-test: Request Queue marketing extras only (no Chat / no full pipeline).

Use to smoke-test ``generate_additional_product_images`` only (no Chat), e.g. after switching RQ image models.

Run from repo root::

    python src/try_additional_images_only.py \\
      --reference-image outputs/mtwi_images_demo_one/demo_item_final.png \\
      --model seedream-5.0-lite

Full demo with **different** models for erase vs extras (optional)::

    export GMI_ERASER_MODEL=<your_erase_model_id>
    export GMI_ADDITIONAL_IMAGE_MODEL=<your_extras_model_id>
    bash src/run_one_deliverable_example.sh

See CONFIGURATION.md / src/README.md.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from mtwi_ecommerce_pipeline import RequestQueueClient, generate_additional_product_images


def _api_key() -> str:
    k = (os.getenv("GMI_API_KEY") or "").strip()
    if k:
        return k
    cred = Path(__file__).resolve().parent.parent / "credentials.json"
    if cred.is_file():
        return str(json.loads(cred.read_text(encoding="utf-8"))["api_key"]).strip()
    raise SystemExit("Set GMI_API_KEY or add credentials.json at repo root")


def main() -> None:
    parser = argparse.ArgumentParser(description="RQ-only: additional product images (no text pipeline).")
    parser.add_argument(
        "--reference-image",
        type=Path,
        default=Path("outputs/mtwi_images_demo_one/demo_item_final.png"),
        help="Cleaned main image (e.g. demo_item_final.png).",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("GMI_ADDITIONAL_IMAGE_MODEL", "seedream-5.0-lite"),
        help="Request Queue model_id for variants (default: env GMI_ADDITIONAL_IMAGE_MODEL or seedream-5.0-lite).",
    )
    parser.add_argument("--out-dir", type=Path, default=Path("outputs/mtwi_images_extra_smoke"))
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--product-id", default="smoke_item")
    parser.add_argument("--mock", action="store_true")
    args = parser.parse_args()

    ref = args.reference_image.expanduser().resolve()
    if not ref.is_file():
        raise SystemExit(f"Missing reference image: {ref}")

    attempts = max(1, min(2, int(os.getenv("GMI_MAX_ATTEMPTS", "2"))))
    rq = RequestQueueClient(api_key=_api_key(), mock=args.mock, max_attempts=attempts)

    out_dir = args.out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    warn: List[str] = []
    paths = generate_additional_product_images(
        rq=rq,
        source_for_generation=ref,
        product_id=args.product_id,
        out_dir=out_dir,
        model=args.model,
        count=max(0, int(args.count)),
        structured_attributes={"product_type": "product", "key_features": []},
        user_image_instructions="",
        scenario_offset=0,
        first_file_index=1,
        warnings=warn,
    )
    for w in warn:
        print(w, file=sys.stderr)
    print(f"model={args.model!r} requested={args.count} written={len(paths)}")
    for line in paths:
        print(line)
    if not paths and not args.mock:
        print(
            "Hint: 0 files — RQ returned no usable image. Try GMI_EXTRA_IMAGES_PLACEHOLDER=1 to copy the reference, "
            "or GMI_EXTRA_IMAGES_BATCH=1, or confirm model_id / outcome schema in GMI console.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
