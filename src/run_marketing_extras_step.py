#!/usr/bin/env python3
"""Optional step: Request Queue marketing extras only, merged into an existing deliverable folder.

Uses **only** ``generate_additional_product_images`` (the same RQ path as the main pipeline’s Step5:
``run_image_variants`` / outcome parsing). Run this **after** the main MTWI pipeline so the reference
image is always a real ``*_final.png`` with no mixed “whole pipeline” payload on this call.

Typical usage (after ``run_one_deliverable_example.sh``)::

    python src/run_marketing_extras_step.py \\
      --reference-image demo_outputs/mtwi_images_demo_one/demo_item_final.png \\
      --deliverable-dir demo_outputs/deliverables_demo_one \\
      --product-id demo_item \\
      --count 3

Mock (no API)::

    python src/run_marketing_extras_step.py --reference-image ... --deliverable-dir ... \\
      --product-id demo_item --count 2 --mock

See CONFIGURATION.md for ``GMI_ADDITIONAL_IMAGE_MODEL``, ``GMI_FALLBACK_IMAGE_MODEL``, and
``GMI_EXTRA_IMAGES_*`` / ``GMI_RQ_OUTCOME_DEBUG``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from mtwi_ecommerce_pipeline import (  # noqa: E402
    RequestQueueClient,
    _safe_slug,
    generate_additional_product_images,
)


def _api_key() -> str:
    k = (os.getenv("GMI_API_KEY") or "").strip()
    if k:
        return k
    cred = _REPO_ROOT / "credentials.json"
    if cred.is_file():
        return str(json.loads(cred.read_text(encoding="utf-8"))["api_key"]).strip()
    raise SystemExit("Set GMI_API_KEY or add credentials.json at repo root (or use --mock).")


def _load_manifest_structured(product_dir: Path) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    man_path = product_dir / "manifest.json"
    if not man_path.is_file():
        return None, {}
    data = json.loads(man_path.read_text(encoding="utf-8"))
    sa = data.get("structured_attributes")
    if isinstance(sa, dict):
        return data, sa
    return data, {}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="RQ-only marketing extras: write product_image_extra_* into an existing deliverable package.",
    )
    parser.add_argument(
        "--reference-image",
        type=Path,
        required=True,
        help="Cleaned main image (e.g. demo_outputs/.../demo_item_final.png).",
    )
    parser.add_argument(
        "--deliverable-dir",
        type=Path,
        required=True,
        help="Deliverable root (contains per-SKU subfolder), e.g. demo_outputs/deliverables_demo_one.",
    )
    parser.add_argument(
        "--product-id",
        required=True,
        help="SKU id from the main run (folder name is slugged; e.g. demo_item).",
    )
    parser.add_argument("--count", type=int, default=3, help="Number of extra shots (default: 3).")
    parser.add_argument(
        "--model",
        default=os.getenv("GMI_ADDITIONAL_IMAGE_MODEL", "seedream-5.0-lite"),
        help="Request Queue model_id for variants.",
    )
    parser.add_argument(
        "--fallback-model",
        default=(os.getenv("GMI_FALLBACK_IMAGE_MODEL") or "").strip(),
        help="Optional second RQ model if primary returns empty.",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="Scratch dir for intermediate *_extra_*.png before copy (default: .extras_work_<slug> under deliverable-dir).",
    )
    parser.add_argument("--mock", action="store_true", help="Do not call GMI; copy reference bytes per slot.")
    parser.add_argument(
        "--user-image-instructions",
        default=(os.getenv("GMI_USER_IMAGE_INSTRUCTIONS") or "").strip(),
        help="Operator image hints (same as main pipeline user-image instructions).",
    )
    args = parser.parse_args()

    ref = args.reference_image.expanduser().resolve()
    if not ref.is_file():
        print(f"Missing --reference-image: {ref}", file=sys.stderr)
        return 1

    deliv_root = args.deliverable_dir.expanduser().resolve()
    slug = _safe_slug(args.product_id)
    product_dir = deliv_root / slug
    if not product_dir.is_dir():
        print(f"Missing deliverable folder: {product_dir}", file=sys.stderr)
        return 1

    _, structured = _load_manifest_structured(product_dir)

    work = args.work_dir
    if work is None:
        work = deliv_root / f".extras_work_{slug}"
    work = work.expanduser().resolve()
    work.mkdir(parents=True, exist_ok=True)

    n = max(0, int(args.count))
    if n < 1:
        print("Nothing to do (--count < 1).", file=sys.stderr)
        return 0

    try:
        api_key = "mock" if args.mock else _api_key()
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 1

    attempts = max(1, min(2, int(os.getenv("GMI_MAX_ATTEMPTS", "2"))))
    rq = RequestQueueClient(api_key=api_key, mock=args.mock, max_attempts=attempts)
    warnings: List[str] = []

    paths = generate_additional_product_images(
        rq=rq,
        source_for_generation=ref,
        product_id=slug,
        out_dir=work,
        model=args.model,
        count=n,
        structured_attributes=structured if structured else None,
        user_image_instructions=args.user_image_instructions,
        scenario_offset=0,
        first_file_index=1,
        warnings=warnings,
        fallback_model=args.fallback_model or None,
    )
    for w in warnings:
        print(w, file=sys.stderr)

    abs_extra: List[str] = []
    for idx, src in enumerate(paths, start=1):
        sp = Path(src)
        if not sp.is_file():
            continue
        dst = product_dir / f"product_image_extra_{idx}.png"
        dst.write_bytes(sp.read_bytes())
        abs_extra.append(str(dst.resolve()))

    man_path = product_dir / "manifest.json"
    if man_path.is_file():
        data = json.loads(man_path.read_text(encoding="utf-8"))
        data["additional_generated_images"] = abs_extra
        wprev = data.get("warnings")
        if isinstance(wprev, list):
            merged = list(wprev)
            for x in warnings:
                if x not in merged:
                    merged.append(x)
            data["warnings"] = merged
        else:
            data["warnings"] = list(warnings)
        man_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"wrote {len(abs_extra)} extra image(s) under {product_dir}")
    for x in abs_extra:
        print(x)

    if len(abs_extra) < n and not args.mock:
        print(
            "Shortfall: try --fallback-model / GMI_FALLBACK_IMAGE_MODEL, GMI_RQ_OUTCOME_DEBUG=1, "
            "or GMI_EXTRA_IMAGES_PLACEHOLDER=1 (copies reference for missing slots).",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
