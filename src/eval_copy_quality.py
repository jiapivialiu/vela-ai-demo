"""Evaluate EN/FR copy quality from MTWI pipeline outputs.

Checks:
- required fields present
- no Chinese chars in EN/FR fields
- language-specific character heuristics
- min length / basic cleanliness

Outputs CSV + Markdown summary.
"""

from __future__ import annotations

import argparse
import csv
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import yaml  # type: ignore


ZH_RE = re.compile(r"[\u4e00-\u9fff]")
FR_MARKERS_RE = re.compile(r"[éèêëàâîïôûùçÉÈÊËÀÂÎÏÔÛÙÇ]|\\b(le|la|les|des|une|un|avec|pour|de|du)\\b", re.I)
EN_MARKERS_RE = re.compile(r"\\b(the|and|with|for|of|to|in|on|your)\\b", re.I)
URL_RE = re.compile(r"https?://", re.I)


def clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def field_text(block: Dict[str, Any]) -> str:
    parts: List[str] = []
    for k in ["title", "description", "category"]:
        val = block.get(k, "")
        if isinstance(val, str):
            parts.append(val)
    attrs = block.get("key_attributes", {})
    if isinstance(attrs, dict):
        for k, v in attrs.items():
            parts.append(str(k))
            parts.append(str(v))
    return "\n".join(parts)


def evaluate_lang_block(block: Dict[str, Any], lang: str) -> Dict[str, float]:
    text = field_text(block)
    has_required = all(isinstance(block.get(k, ""), str) and block.get(k, "").strip() for k in ["title", "description", "category"])
    has_zh = bool(ZH_RE.search(text))
    has_url = bool(URL_RE.search(text))
    length_score = clamp01(min(len(text), 900) / 350.0)

    if lang == "en":
        lang_marker = 1.0 if EN_MARKERS_RE.search(text) else 0.5
    else:
        lang_marker = 1.0 if FR_MARKERS_RE.search(text) else 0.5

    no_zh_score = 0.0 if has_zh else 1.0
    clean_score = 0.0 if has_url else 1.0
    required_score = 1.0 if has_required else 0.0

    score = (
        0.35 * required_score
        + 0.30 * no_zh_score
        + 0.20 * length_score
        + 0.10 * lang_marker
        + 0.05 * clean_score
    )
    return {
        "required_score": round(required_score, 4),
        "no_zh_score": round(no_zh_score, 4),
        "length_score": round(length_score, 4),
        "lang_marker_score": round(lang_marker, 4),
        "clean_score": round(clean_score, 4),
        "score": round(score, 4),
    }


def evaluate_one(sample: Dict[str, Any]) -> Dict[str, Any]:
    product_id = str(sample.get("product_id", ""))
    en = sample.get("canadian_english", {}) if isinstance(sample.get("canadian_english", {}), dict) else {}
    fr = sample.get("canadian_french", {}) if isinstance(sample.get("canadian_french", {}), dict) else {}

    en_s = evaluate_lang_block(en, "en")
    fr_s = evaluate_lang_block(fr, "fr")
    overall = round(0.5 * en_s["score"] + 0.5 * fr_s["score"], 4)

    return {
        "product_id": product_id,
        "en_score": en_s["score"],
        "fr_score": fr_s["score"],
        "copy_quality_score": overall,
        "en_required": en_s["required_score"],
        "fr_required": fr_s["required_score"],
        "en_no_zh": en_s["no_zh_score"],
        "fr_no_zh": fr_s["no_zh_score"],
        "en_lang_marker": en_s["lang_marker_score"],
        "fr_lang_marker": fr_s["lang_marker_score"],
    }


def write_csv(rows: List[Dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(rows[0].keys()) if rows else ["product_id"])
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def write_md(rows: List[Dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Copy Quality Metrics",
        "",
        "| product_id | en_score | fr_score | overall | en_no_zh | fr_no_zh |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['product_id']} | {r['en_score']:.3f} | {r['fr_score']:.3f} | {r['copy_quality_score']:.3f} | {r['en_no_zh']:.1f} | {r['fr_no_zh']:.1f} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate copy quality")
    parser.add_argument("--samples-yaml", default="outputs/mtwi_ecommerce_samples.yaml")
    parser.add_argument("--output-csv", default="outputs/mtwi_copy_metrics.csv")
    parser.add_argument("--output-md", default="outputs/mtwi_copy_metrics.md")
    args = parser.parse_args()

    samples = yaml.safe_load(Path(args.samples_yaml).read_text(encoding="utf-8")) or []
    if not isinstance(samples, list):
        raise ValueError("Samples YAML must be a list")
    t_run = time.perf_counter()
    print(f"[eval_copy] start {datetime.now().astimezone().isoformat()}  samples={len(samples)}", flush=True)
    rows = []
    for s in samples:
        pid = str(s.get("product_id", "?"))
        t0 = time.perf_counter()
        row = evaluate_one(s)
        dt = time.perf_counter() - t0
        rows.append(row)
        print(
            f"[eval_copy] {pid}  {dt:.3f}s  overall={row['copy_quality_score']:.3f}  "
            f"en={row['en_score']:.3f}  fr={row['fr_score']:.3f}",
            flush=True,
        )
    write_csv(rows, Path(args.output_csv))
    write_md(rows, Path(args.output_md))
    scores = [float(r["copy_quality_score"]) for r in rows]
    mean_o = sum(scores) / len(scores) if scores else 0.0
    total_dt = time.perf_counter() - t_run
    print(f"[eval_copy] done {datetime.now().astimezone().isoformat()}  total={total_dt:.2f}s  mean_overall={mean_o:.3f}", flush=True)
    print(f"Wrote copy metrics CSV: {args.output_csv}")
    print(f"Wrote copy metrics Markdown: {args.output_md}")


if __name__ == "__main__":
    main()

