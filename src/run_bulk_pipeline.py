"""Reproducible bulk runner: pipeline → deliverables → image + copy eval → stability reports.

Targets GMI Cloud Inference Engine (same defaults as mtwi_ecommerce_pipeline.parse_args).
Config keys mirror CLI flags (see configs/bulk_run.yaml). Omitted keys use defaults in
`build_pipeline_cmd` (e.g. `pipeline.skip_listing_review: true` → `--skip-listing-review`). Model/env overview: ../agent.md and ../CONFIGURATION.md.
Operator docs: src/README.md (bulk section).

Usage:
    python src/run_bulk_pipeline.py --config configs/bulk_run.yaml
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import yaml  # type: ignore


def utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def git_commit_or_unknown(repo_root: Path) -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return out or "unknown"
    except Exception:
        return "unknown"


def build_pipeline_cmd(cfg: Dict[str, Any], run_dir: Path) -> List[str]:
    pipeline_cfg = cfg.get("pipeline", {})
    rq_image_model = str(
        pipeline_cfg.get(
            "additional_image_model",
            os.getenv("GMI_ADDITIONAL_IMAGE_MODEL", "seedream-5.0-lite"),
        )
    )
    cmd = [
        "python",
        "src/mtwi_ecommerce_pipeline.py",
        "--txt-dir",
        str(pipeline_cfg.get("txt_dir", "data/mtwi_train/txt_train")),
        "--image-dir",
        str(pipeline_cfg.get("image_dir", "data/mtwi_train/image_train")),
        "--output",
        str(run_dir / "mtwi_ecommerce_samples.yaml"),
        "--image-output-dir",
        str(run_dir / "images"),
        "--deliverable-dir",
        str(run_dir / "deliverables"),
        "--limit",
        str(int(pipeline_cfg.get("limit", 100))),
        "--mask-mode",
        str(pipeline_cfg.get("mask_mode", "all")),
        "--erase-strategy",
        str(pipeline_cfg.get("erase_strategy", "model")),
        "--quality-strategy",
        str(pipeline_cfg.get("quality_strategy", "local")),
        "--eraser-model",
        str(pipeline_cfg.get("eraser_model", rq_image_model)),
        "--restore-model",
        str(pipeline_cfg.get("restore_model", "bria-fibo-restore")),
        "--vision-model",
        str(pipeline_cfg.get("vision_model", "Qwen/Qwen3-VL-235B")),
        "--copy-understand-image",
        str(pipeline_cfg.get("copy_understand_image", "final")),
        "--english-copy-model",
        str(pipeline_cfg.get("english_copy_model", "openai/gpt-5.4-pro")),
        "--french-copy-model",
        str(pipeline_cfg.get("french_copy_model", "anthropic/claude-sonnet-4.6")),
        "--fallback-english-copy-model",
        str(pipeline_cfg.get("fallback_english_copy_model", "openai/gpt-5.4-mini")),
        "--fallback-french-copy-model",
        str(pipeline_cfg.get("fallback_french_copy_model", "openai/gpt-5.4-mini")),
        "--simple-copy-model",
        str(
            pipeline_cfg.get(
                "simple_copy_model",
                pipeline_cfg.get("fallback_english_copy_model", "openai/gpt-5.4-mini"),
            )
        ),
        "--copy-review-english-model",
        str(pipeline_cfg.get("copy_review_english_model", "anthropic/claude-sonnet-4.6")),
        "--copy-review-french-model",
        str(pipeline_cfg.get("copy_review_french_model", "anthropic/claude-sonnet-4.6")),
        "--locale-grammar-english-model",
        str(pipeline_cfg.get("locale_grammar_english_model", "openai/gpt-5.4-nano")),
        "--locale-grammar-french-model",
        str(pipeline_cfg.get("locale_grammar_french_model", "openai/gpt-5.4-nano")),
        "--copy-generation-mode",
        str(pipeline_cfg.get("copy_generation_mode", "unified")),
        "--unified-copy-model",
        str(
            pipeline_cfg.get(
                "unified_copy_model",
                pipeline_cfg.get("english_copy_model", "openai/gpt-5.4-pro"),
            )
        ),
        "--max-attempts",
        str(int(pipeline_cfg.get("max_attempts", 2))),
        "--stability-update-every",
        str(int(pipeline_cfg.get("stability_update_every", 100))),
        "--stability-report-path",
        str(run_dir / "stability_baseline.json"),
        "--stability-markdown-path",
        str(run_dir / "stability_baseline.md"),
        "--export-deliverables",
    ]
    if bool(pipeline_cfg.get("harmonize_after_erase", False)):
        cmd.append("--harmonize-after-erase")
        cmd.extend(
            [
                "--harmonize-model",
                str(pipeline_cfg.get("harmonize_model", "bria-fibo-edit")),
            ]
        )
    else:
        cmd.append("--no-harmonize-after-erase")
    if bool(pipeline_cfg.get("generate_additional_images", False)):
        cmd.append("--generate-additional-images")
        cmd.extend(
            [
                "--additional-image-model",
                str(pipeline_cfg.get("additional_image_model", "seedream-5.0-lite")),
                "--additional-image-count",
                str(int(pipeline_cfg.get("additional_image_count", 3))),
            ]
        )
        fam = pipeline_cfg.get("fallback_additional_image_model")
        if fam is not None and str(fam).strip():
            cmd.extend(["--fallback-additional-image-model", str(fam).strip()])
    else:
        cmd.append("--no-generate-additional-images")
    if bool(pipeline_cfg.get("disable_restore", True)):
        cmd.append("--disable-restore")
    if bool(pipeline_cfg.get("disable_erase", False)):
        cmd.append("--disable-erase")
    if bool(pipeline_cfg.get("no_mask", False)):
        cmd.append("--no-mask")
    if bool(pipeline_cfg.get("mock", False)):
        cmd.append("--mock")
    if bool(pipeline_cfg.get("no_simple_copy_recovery", False)):
        cmd.append("--no-simple-copy-recovery")
    if not bool(pipeline_cfg.get("annotation_audit", True)):
        cmd.append("--no-annotation-audit")
    aam = pipeline_cfg.get("annotation_audit_model")
    if aam is not None and str(aam).strip():
        cmd.extend(["--annotation-audit-model", str(aam).strip()])
    if pipeline_cfg.get("input_images_glob"):
        cmd.extend(["--input-images-glob", str(pipeline_cfg["input_images_glob"])])
    uc = pipeline_cfg.get("user_copy_instructions")
    if uc is not None and str(uc).strip():
        cmd.extend(["--user-copy-instructions", str(uc)])
    ucf = pipeline_cfg.get("user_copy_instructions_file")
    if ucf:
        cmd.extend(["--user-copy-instructions-file", str(ucf)])
    ui = pipeline_cfg.get("user_image_instructions")
    if ui is not None and str(ui).strip():
        cmd.extend(["--user-image-instructions", str(ui)])
    uif = pipeline_cfg.get("user_image_instructions_file")
    if uif:
        cmd.extend(["--user-image-instructions-file", str(uif)])
    if bool(pipeline_cfg.get("skip_listing_review", False)):
        cmd.append("--skip-listing-review")
    return cmd


def build_eval_cmd(cfg: Dict[str, Any], run_dir: Path) -> List[str]:
    eval_cfg = cfg.get("evaluation", {})
    return [
        "python",
        "src/eval_image_quality.py",
        "--samples-yaml",
        str(run_dir / "mtwi_ecommerce_samples.yaml"),
        "--output-csv",
        str(run_dir / "mtwi_image_metrics.csv"),
        "--output-md",
        str(run_dir / "mtwi_image_metrics.md"),
        "--threshold",
        str(int(eval_cfg.get("diff_threshold", 12))),
    ]


def build_copy_eval_cmd(run_dir: Path) -> List[str]:
    return [
        "python",
        "src/eval_copy_quality.py",
        "--samples-yaml",
        str(run_dir / "mtwi_ecommerce_samples.yaml"),
        "--output-csv",
        str(run_dir / "mtwi_copy_metrics.csv"),
        "--output-md",
        str(run_dir / "mtwi_copy_metrics.md"),
    ]


def run_cmd(cmd: List[str], cwd: Path, env: Dict[str, str], log_path: Path) -> int:
    with log_path.open("a", encoding="utf-8") as fp:
        fp.write(f"\n$ {' '.join(cmd)}\n")
        fp.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            fp.write(line)
        return proc.wait()


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk reproducible runner")
    parser.add_argument("--config", default="configs/bulk_run.yaml", help="Path to run config yaml")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    cfg_path = (repo_root / args.config).resolve() if not Path(args.config).is_absolute() else Path(args.config)
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config not found: {cfg_path}")
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    run_name = str(cfg.get("run_name", "bulk_mtwi"))
    run_id = f"{run_name}_{utc_ts()}"
    out_root = repo_root / str(cfg.get("output_root", "outputs/runs"))
    run_dir = out_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    for k, v in (cfg.get("env", {}) or {}).items():
        env[str(k)] = str(v)

    # Persist full run config + metadata for reproducibility
    manifest = {
        "run_id": run_id,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit_or_unknown(repo_root),
        "config_path": str(cfg_path),
        "config": cfg,
    }
    (run_dir / "run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    log_path = run_dir / "run.log"
    pipeline_cmd = build_pipeline_cmd(cfg, run_dir)
    eval_cmd = build_eval_cmd(cfg, run_dir)

    code1 = run_cmd(pipeline_cmd, repo_root, env, log_path)
    if code1 != 0:
        raise SystemExit(f"Pipeline failed with exit code {code1}. See {log_path}")

    code2 = run_cmd(eval_cmd, repo_root, env, log_path)
    if code2 != 0:
        raise SystemExit(f"Image evaluation failed with exit code {code2}. See {log_path}")

    copy_eval_cmd = build_copy_eval_cmd(run_dir)
    code3 = run_cmd(copy_eval_cmd, repo_root, env, log_path)
    if code3 != 0:
        raise SystemExit(f"Copy evaluation failed with exit code {code3}. See {log_path}")

    summary = {
        "run_id": run_id,
        "samples_yaml": str(run_dir / "mtwi_ecommerce_samples.yaml"),
        "deliverables_index": str(run_dir / "deliverables" / "deliverables_index.csv"),
        "metrics_csv": str(run_dir / "mtwi_image_metrics.csv"),
        "metrics_md": str(run_dir / "mtwi_image_metrics.md"),
        "copy_metrics_csv": str(run_dir / "mtwi_copy_metrics.csv"),
        "copy_metrics_md": str(run_dir / "mtwi_copy_metrics.md"),
        "stability_json": str(run_dir / "stability_baseline.json"),
        "stability_md": str(run_dir / "stability_baseline.md"),
        "log": str(log_path),
    }
    (run_dir / "run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Run completed: {run_id}")
    print(f"Summary: {run_dir / 'run_summary.json'}")


if __name__ == "__main__":
    main()

