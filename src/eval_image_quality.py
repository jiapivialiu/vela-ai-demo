"""Evaluate MTWI edited image quality from pipeline outputs.

Reads `outputs/mtwi_ecommerce_samples.yaml` and computes per-product metrics:
- changed_ratio_all
- changed_ratio_mask
- changed_ratio_non_mask
- mean_abs_diff_all
- mean_abs_diff_mask
- mean_abs_diff_non_mask
- edit_localization_score
- preservation_score
- removal_score
- quality_score

The script uses PIL only (no numpy dependency).
"""

from __future__ import annotations

import argparse
import csv
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import yaml  # type: ignore
from PIL import Image, ImageChops, ImageOps, ImageStat


def clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def ratio_pixels_over_threshold(gray_img: Image.Image, threshold: int) -> float:
    hist = gray_img.histogram()
    over = sum(hist[threshold + 1 :])
    total = float(sum(hist)) or 1.0
    return over / total


def load_mask(mask_path: Path, size: tuple[int, int]) -> Image.Image:
    if not mask_path.exists():
        return Image.new("L", size, 0)
    mask = Image.open(mask_path).convert("L")
    if mask.size != size:
        mask = mask.resize(size, Image.NEAREST)
    return mask


def evaluate_one(sample: Dict[str, Any], diff_threshold: int = 12) -> Dict[str, Any]:
    product_id = str(sample.get("product_id", ""))
    source_path = Path(str(sample.get("source_image_path", "")))
    edited_path_str = str(sample.get("final_image_path") or sample.get("erased_image_path") or "")
    edited_path = Path(edited_path_str)
    mask_path = edited_path.with_name(f"{product_id}_mask.png")

    if not source_path.exists() or not edited_path.exists():
        return {
            "product_id": product_id,
            "source_path": str(source_path),
            "edited_path": str(edited_path),
            "mask_path": str(mask_path),
            "error": "missing_source_or_edited_image",
        }

    src = Image.open(source_path).convert("RGB")
    edt = Image.open(edited_path).convert("RGB")
    if edt.size != src.size:
        edt = edt.resize(src.size, Image.BILINEAR)

    diff = ImageChops.difference(src, edt).convert("L")
    mask = load_mask(mask_path, src.size)
    mask_inv = ImageOps.invert(mask)

    mask_cov = ratio_pixels_over_threshold(mask, threshold=1)
    changed_all = ratio_pixels_over_threshold(diff, threshold=diff_threshold)
    changed_mask = ratio_pixels_over_threshold(ImageChops.multiply(diff, mask), threshold=diff_threshold)
    changed_non = ratio_pixels_over_threshold(ImageChops.multiply(diff, mask_inv), threshold=diff_threshold)

    mean_all = float(ImageStat.Stat(diff).mean[0])
    mean_mask = float(ImageStat.Stat(diff, mask=mask).mean[0]) if mask_cov > 0 else 0.0
    mean_non = float(ImageStat.Stat(diff, mask=mask_inv).mean[0])

    # Scores (0-1), tuned for this use case:
    # - removal_score: stronger changes inside mask is better (up to ~28 gray-level mean)
    # - preservation_score: weaker changes outside mask is better (~0-10 preferred)
    # - localization: changed-in-mask / changed-outside-mask
    removal_score = clamp01(mean_mask / 28.0)
    preservation_score = clamp01(1.0 - (mean_non / 20.0))
    localization_raw = changed_mask / max(changed_non, 1e-6)
    edit_localization_score = clamp01(localization_raw / 8.0)

    quality_score = (
        0.45 * removal_score
        + 0.40 * preservation_score
        + 0.15 * edit_localization_score
    )

    return {
        "product_id": product_id,
        "source_path": str(source_path),
        "edited_path": str(edited_path),
        "mask_path": str(mask_path),
        "mask_coverage_ratio": round(mask_cov, 6),
        "changed_ratio_all": round(changed_all, 6),
        "changed_ratio_mask": round(changed_mask, 6),
        "changed_ratio_non_mask": round(changed_non, 6),
        "mean_abs_diff_all": round(mean_all, 4),
        "mean_abs_diff_mask": round(mean_mask, 4),
        "mean_abs_diff_non_mask": round(mean_non, 4),
        "removal_score": round(removal_score, 4),
        "preservation_score": round(preservation_score, 4),
        "edit_localization_score": round(edit_localization_score, 4),
        "quality_score": round(quality_score, 4),
    }


def write_csv(rows: List[Dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(rows: List[Dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# MTWI Image Quality Metrics",
        "",
        "| product_id | mask_cov | mean_mask | mean_non | changed_mask | changed_non | removal | preserve | localize | quality |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        if "error" in r:
            lines.append(f"| {r['product_id']} | - | - | - | - | - | - | - | - | ERROR |")
            continue
        lines.append(
            "| {product_id} | {mask_coverage_ratio:.4f} | {mean_abs_diff_mask:.2f} | {mean_abs_diff_non_mask:.2f} | "
            "{changed_ratio_mask:.4f} | {changed_ratio_non_mask:.4f} | {removal_score:.3f} | "
            "{preservation_score:.3f} | {edit_localization_score:.3f} | {quality_score:.3f} |".format(**r)
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate MTWI edited image quality")
    parser.add_argument("--samples-yaml", default="outputs/mtwi_ecommerce_samples.yaml")
    parser.add_argument("--output-csv", default="outputs/mtwi_image_metrics.csv")
    parser.add_argument("--output-md", default="outputs/mtwi_image_metrics.md")
    parser.add_argument("--threshold", type=int, default=12, help="Pixel-diff threshold for changed ratio")
    args = parser.parse_args()

    samples_path = Path(args.samples_yaml)
    if not samples_path.exists():
        raise FileNotFoundError(f"Samples YAML not found: {samples_path}")

    samples = yaml.safe_load(samples_path.read_text(encoding="utf-8")) or []
    if not isinstance(samples, list):
        raise ValueError("Samples YAML must be a list")

    t_run = time.perf_counter()
    print(f"[eval_image] start {datetime.now().astimezone().isoformat()}  samples={len(samples)}", flush=True)
    rows = []
    for s in samples:
        pid = str(s.get("product_id", "?"))
        t0 = time.perf_counter()
        row = evaluate_one(s, diff_threshold=args.threshold)
        dt = time.perf_counter() - t0
        rows.append(row)
        if "error" in row:
            print(f"[eval_image] {pid}  {dt:.3f}s  ERROR {row['error']}", flush=True)
        else:
            print(
                f"[eval_image] {pid}  {dt:.3f}s  quality={row['quality_score']:.3f}  "
                f"removal={row['removal_score']:.3f}  preserve={row['preservation_score']:.3f}",
                flush=True,
            )
    write_csv(rows, Path(args.output_csv))
    write_markdown(rows, Path(args.output_md))

    ok_scores = [float(r["quality_score"]) for r in rows if "error" not in r]
    mean_q = sum(ok_scores) / len(ok_scores) if ok_scores else 0.0
    total_dt = time.perf_counter() - t_run
    print(f"[eval_image] done {datetime.now().astimezone().isoformat()}  total={total_dt:.2f}s  mean_quality={mean_q:.3f}", flush=True)
    print(f"Wrote metrics CSV: {args.output_csv}")
    print(f"Wrote metrics Markdown: {args.output_md}")


if __name__ == "__main__":
    main()

