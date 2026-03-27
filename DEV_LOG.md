# Development Log

Brief record of what changed and what was verified to work.

## 2026-03-28

- **README simplified**: single table of existing scripts/configs; removed long duplicate sections; `bash scripts/...` for one-item demo; fixed narrative (YAML keys described in prose, not CLI-style `erase-strategy`).
- **Repo hygiene (gen + eval path only)**:
  - Moved single-item shell demo to `scripts/run_one_deliverable_example.sh` (removed misplaced copy under `src/`).
  - Removed `test_requests.sh` (ad-hoc curl checks; not part of pipeline or bulk runner).
  - Trimmed `configs/bulk_run.yaml` / `bulk_run_smoke.yaml`: dropped redundant `eraser_model` / `restore_model` / unused extra-image keys in smoke; empty `env: {}` block (optional `env` still supported in code).
- **Documentation cleanup**: Deduplicated `README.md` (single index + run paths); trimmed repeated bullets in this log; pointed `agent.md` / configs at README for parameters; `PROMPT_TUNING_NOTES.md` remains the only place for prompt prose.

## 2026-03-27 (verified live)

- **MTWI ecommerce pipeline** (`src/mtwi_ecommerce_pipeline.py`): agent-aligned flow — text removal (local coordinates and/or model), optional harmonize, quality (local or restore), vision understanding, bilingual copy (+ fallback), optional extra same-product images with backfill if fewer images returned than requested.
- **Automation**: per-product deliverables (`product_image.png`, EN/FR markdown, `manifest.json`, `deliverables_index.csv`); `--input-image` / `--input-images-glob`; overlay vs all mask (`--mask-mode`), `erased_spans` / warnings for traceability.
- **Bulk runner** (`src/run_bulk_pipeline.py`, `configs/bulk_run.yaml`, `configs/bulk_run_smoke.yaml`): one command for pipeline + image metrics + copy metrics + `run_manifest.json` / `run.log` / `stability_baseline.*`; `--max-attempts` on external calls.
- **Eval**: `src/eval_image_quality.py`, `src/eval_copy_quality.py`.
- **Docs / safety**: README MTWI-first; `credentials.json` gitignored; earlier `press_on_nails_pipeline.py` bilingual + GMI smoke tests noted below for history.

## 2026-03-26 (initial implementation)

- `src/press_on_nails_pipeline.py`: CSV → localized output; `--mock`; optional image via Request Queue.
